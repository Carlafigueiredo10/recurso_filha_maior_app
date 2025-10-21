import pdfplumber
import pandas as pd
import streamlit as st
import json
import re
from pathlib import Path
from openai import OpenAI
import boto3
from io import StringIO, BytesIO
import base64

# carregar chave da API do secrets (Streamlit Cloud)
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# Configurar cliente B2 (compatível com S3) - com tratamento de erro
try:
    # Verificar se as credenciais B2 existem
    if all(key in st.secrets for key in ["B2_ENDPOINT", "B2_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME"]):
        s3_client = boto3.client(
            's3',
            endpoint_url=st.secrets["B2_ENDPOINT"],
            aws_access_key_id=st.secrets["B2_KEY_ID"],
            aws_secret_access_key=st.secrets["B2_APPLICATION_KEY"]
        )
        BUCKET_NAME = st.secrets["B2_BUCKET_NAME"]
        FEEDBACK_FILE = "feedbacks.csv"
        B2_CONFIGURED = True
    else:
        raise KeyError("B2 secrets não configurados")
except Exception as e:
    s3_client = None
    BUCKET_NAME = None
    FEEDBACK_FILE = "feedbacks.csv"
    B2_CONFIGURED = False

# --------- Funções B2 ---------
def download_feedbacks_from_b2():
    """Baixa o arquivo feedbacks.csv do Backblaze B2 e retorna como DataFrame."""
    if not B2_CONFIGURED or s3_client is None:
        return pd.DataFrame(columns=['timestamp', 'codigo', 'nome', 'decisao', 'achado', 'avaliacao', 'comentario', 'corpo_oficio'])

    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=FEEDBACK_FILE)
        csv_content = response['Body'].read().decode('utf-8')
        df = pd.read_csv(StringIO(csv_content))
        return df
    except s3_client.exceptions.NoSuchKey:
        # Arquivo ainda não existe no B2, retornar DataFrame vazio
        return pd.DataFrame(columns=['timestamp', 'codigo', 'nome', 'decisao', 'achado', 'avaliacao', 'comentario', 'corpo_oficio'])
    except Exception as e:
        st.warning(f"⚠️ Erro ao baixar feedbacks do B2: {e}")
        return pd.DataFrame(columns=['timestamp', 'codigo', 'nome', 'decisao', 'achado', 'avaliacao', 'comentario', 'corpo_oficio'])

def upload_feedbacks_to_b2(df):
    """Faz upload do DataFrame de feedbacks para o Backblaze B2."""
    if not B2_CONFIGURED or s3_client is None:
        st.error("❌ Backblaze B2 não está configurado. Configure os secrets no Streamlit Cloud.")
        return False

    try:
        csv_buffer = StringIO()
        df.to_csv(csv_buffer, index=False)
        csv_bytes = BytesIO(csv_buffer.getvalue().encode('utf-8'))

        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=FEEDBACK_FILE,
            Body=csv_bytes.getvalue(),
            ContentType='text/csv'
        )
        return True
    except Exception as e:
        st.error(f"❌ Erro ao enviar feedbacks para o B2: {e}")
        return False

def processar_feedbacks_para_aprendizado():
    """
    Processa feedbacks armazenados no B2 e gera insights de aprendizado.
    Retorna dicionário com exemplos corretos e padrões de erros.
    """
    try:
        # Baixar feedbacks do B2
        df_feedbacks = download_feedbacks_from_b2()

        if df_feedbacks.empty:
            return {
                'total': 0,
                'corretos': 0,
                'incorretos': 0,
                'exemplos_corretos': [],
                'padroes_erro': [],
                'insights': "Nenhum feedback disponível ainda."
            }

        # Separar feedbacks corretos e incorretos
        corretos = df_feedbacks[df_feedbacks['avaliacao'] == 'correto']
        incorretos = df_feedbacks[df_feedbacks['avaliacao'] == 'incorreto']

        # Gerar insights usando GPT
        prompt_insights = f"""
Você é um especialista em análise de feedbacks para melhorar sistemas de IA.

Analise os feedbacks abaixo e gere insights acionáveis para melhorar o sistema:

### FEEDBACKS CORRETOS ({len(corretos)} casos)
{corretos[['achado', 'decisao', 'comentario']].head(10).to_string() if len(corretos) > 0 else "Nenhum feedback correto ainda"}

### FEEDBACKS INCORRETOS ({len(incorretos)} casos)
{incorretos[['achado', 'decisao', 'comentario']].head(10).to_string() if len(incorretos) > 0 else "Nenhum feedback incorreto ainda"}

### TAREFA
Gere um relatório com:

1. **Padrões de Sucesso**: O que o sistema está acertando?
2. **Padrões de Erro**: Quais erros mais comuns? (analise os comentários dos feedbacks incorretos)
3. **Recomendações**: Como melhorar os prompts/classificações?

Seja específico e acionável. Foque em melhorias concretas.
"""

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt_insights}],
            temperature=0.3
        )

        insights = resp.choices[0].message.content

        # Preparar exemplos corretos para few-shot learning
        exemplos_corretos = []
        if len(corretos) > 0:
            for _, row in corretos.head(5).iterrows():
                exemplos_corretos.append({
                    'achado': row['achado'],
                    'decisao': row['decisao'],
                    'corpo_oficio': row['corpo_oficio'][:500]  # Primeiros 500 chars
                })

        # Identificar padrões de erro
        padroes_erro = []
        if len(incorretos) > 0:
            for _, row in incorretos.iterrows():
                if pd.notna(row['comentario']) and row['comentario'].strip():
                    padroes_erro.append({
                        'achado': row['achado'],
                        'decisao': row['decisao'],
                        'problema': row['comentario']
                    })

        return {
            'total': len(df_feedbacks),
            'corretos': len(corretos),
            'incorretos': len(incorretos),
            'exemplos_corretos': exemplos_corretos,
            'padroes_erro': padroes_erro,
            'insights': insights,
            'taxa_acerto': f"{(len(corretos) / len(df_feedbacks) * 100):.1f}%" if len(df_feedbacks) > 0 else "0%"
        }

    except Exception as e:
        return {
            'erro': str(e),
            'insights': f"Erro ao processar feedbacks: {e}"
        }

# Caminhos dos templates
TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_PROCEDENTE = TEMPLATE_DIR / "oficio_procedente.pdf"
TEMPLATE_IMPROCEDENTE = TEMPLATE_DIR / "oficio_improcedente.pdf"

# dicionário de argumentos
ARG_MAP = {
    "1": "Negativa de união estável",
    "2": "Filho em comum não caracteriza união estável",
    "3": "Mais de um filho em comum não caracteriza",
    "4": "Endereço distinto",
    "5": "Erro em bases cadastrais",
    "6": "Decisão judicial transitada em julgado",
    "7": "Dissolução da união estável",
    "8": "Ameaça de judicialização",
    "9": "Processo administrativo anterior sem novos elementos",
    "10": "Testemunho de terceiros",
    "11": "Inconsistência no CadÚnico",
    "12": "Defesa admite filho em comum"
}

# carregar matriz
matriz = pd.read_csv("matriz_decisao_revisada_final.csv")

def extrair_texto(pdf_file):
    texto = ""
    with pdfplumber.open(pdf_file) as pdf:
        for p in pdf.pages:
            if p.extract_text():
                texto += p.extract_text() + "\n"
    return texto

def carregar_template_oficio(decisao):
    """Carrega o template do ofício baseado na decisão (procedente ou improcedente)."""
    template_path = TEMPLATE_PROCEDENTE if decisao == "procedente" else TEMPLATE_IMPROCEDENTE

    if not template_path.exists():
        return None

    return extrair_texto(template_path)

def extrair_item_template(decisao):
    """Extrai apenas o item 13 (procedente) ou item 15 (improcedente) do template."""
    template_completo = carregar_template_oficio(decisao)

    if not template_completo:
        return None

    # Apenas para procedente (item 13)
    if decisao == "procedente":
        # Procurar pelo item 13
        import re

        # Tentar diferentes padrões para encontrar o item 13
        padroes = [
            r'(?:^|\n)\s*13[.\)]\s*(.+?)(?=\n\s*14[.\)]|\n\s*Respeitosamente|\Z)',
            r'(?:^|\n)\s*item\s*13[.\):]?\s*(.+?)(?=\n\s*(?:item\s*)?14[.\)]|\n\s*Respeitosamente|\Z)',
            r'13[.\)]\s*(.+?)(?=14[.\)]|\Z)'
        ]

        for padrao in padroes:
            match = re.search(padrao, template_completo, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        # Se não encontrou, retorna uma parte do meio do documento
        return template_completo[len(template_completo)//3:len(template_completo)*2//3]

    # Para improcedente não mexe (já funciona)
    return None

# --------- Extrair dados de identificação do extrato ---------
def extrair_dados_identificacao(texto_extrato):
    """Extrai nome, CPF, código e descrição do indício do extrato do TCU."""
    prompt = f"""
Você é um sistema de extração de dados estruturados de extratos do TCU.

Analise o texto do extrato abaixo e extraia as seguintes informações da TABELA:

1. **Código do Indício**: número que aparece na coluna "Código Indício" (exemplo: 6201799 ou 6202264)

2. **CPF da PENSIONISTA**: o CPF que aparece na coluna "CPF" da tabela (formato: XXX.XXX.XXX-XX)
   ⚠️ ATENÇÃO: Extraia APENAS o CPF da coluna "CPF" da tabela principal
   ⚠️ NÃO extraia CPFs que aparecem dentro do campo "Descrição" (esses são de terceiros)

3. **Nome completo da PENSIONISTA**: o nome que aparece na coluna "Nome" da tabela
   ⚠️ IMPORTANTE:
   - A coluna "Nome" contém o nome da PESSOA (não é tipo de indício)
   - Geralmente são nomes como "MARIA DA SILVA", "JOÃO SANTOS", "TANIA APARECIDA DOS REIS MARQUES"
   - NÃO confunda com "Tipo de indício" ou "Descrição"
   - Extraia o nome COMPLETO da pessoa, sem omitir nenhuma parte
   - Se encontrar algo como "Pensionista filha maior solteira" ou "Pensionista em união estável", isso NÃO é nome - procure o nome real da pessoa

4. **Descrição do Indício**: Extraia TODO o conteúdo do campo "Descrição" da tabela
   ⚠️ ATENÇÃO: Extraia TUDO desde o INÍCIO do campo "Descrição" até ANTES da palavra "Critério:"
   - Inclua TUDO: nome da filha entre parênteses, nome do companheiro, CPFs mencionados, endereços completos, bases cadastrais (RECEITA FEDERAL, RENACH, etc.)
   - Exemplo completo: "Pensionista possui filho em comum (ANDREZZA JATAY MOTA GARROS) e compartilha o mesmo endereço com AMELIO GENTIL GARROS (CPF: 30783690720) - Endereço em comum: DR PAULO SANFORD, 130, bairro EDSON QUEIROZ CEP 60834422 - FORTALEZA (CE) Endereço encontrado na base RECEITA FEDERAL, para o CPF 30783690720 e na base RENACH, para o CPF 36266655349"
   - PARE somente quando encontrar a palavra "Critério:"
   - NÃO inclua a fundamentação legal que vem depois de "Critério: A Lei 3373/1958..."

### Texto do Extrato:
{texto_extrato}

### Formato de saída
Responda apenas com JSON válido, sem explicações, sem Markdown, no seguinte formato:

{{
  "codigo_indicio": "6201799",
  "cpf": "164.853.578-07",
  "nome": "NORMISIA GONCALVES BEZERRA SOBRAL / EITE",
  "descricao_indicio": "Pensionista filha maior solteira com provável união estável ou casamento. Evidências do indício: Pensionista possui filho em comum..."
}}

**EXEMPLOS DE NOMES CORRETOS:**
- "TANIA APARECIDA DOS REIS MARQUES"
- "NORMISIA GONCALVES BEZERRA SOBRAL / EITE"
- "MARIA JOSE DA SILVA"

**NÃO SÃO NOMES (não use isso):**
- "Pensionista filha maior solteira"
- "Pensionista em união estável"
- "Pensionista enquadrada como filha maior"

**REGRAS IMPORTANTES:**
- Nome: extraia o nome COMPLETO da pensionista da coluna "Nome" (não omita partes)
- CPF: extraia SOMENTE o CPF da coluna "CPF" da tabela (NÃO pegue CPFs da descrição)
- Descrição: extraia TODO o conteúdo do campo "Descrição" desde o INÍCIO até ANTES de "Critério:"
- A descrição deve incluir TUDO: filhos mencionados, companheiros, CPFs citados, endereços completos, bases cadastrais
- NÃO corte informações - pegue o texto completo do campo Descrição
- PARE a extração somente quando encontrar a palavra "Critério:"
- NÃO inclua a fundamentação legal que aparece após "Critério:"
- Se não encontrar alguma informação, use null no campo correspondente
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return resp.choices[0].message.content

# --------- GPT para JSON técnico ---------
def classificar_com_gpt(descricao_indicio, texto_defesa):
    prompt = f"""
Você é um sistema de apoio jurídico que analisa recursos administrativos de pensão de filha maior solteira.

### Bloco 1 — Achado do TCU (descrição do indício)
{descricao_indicio}

### Bloco 2 — Defesa apresentada pela interessada
{texto_defesa}

### Tarefa
1. Classifique o achado do TCU com base APENAS nas evidências específicas mencionadas no Bloco 1:

REGRAS DE CLASSIFICAÇÃO:
- "Apenas CadÚnico": quando menciona SOMENTE Cadastro Único Federal / CadÚnico / Bolsa Família como responsável financeira ou cônjuge/companheiro(a)
- "Apenas 1 filho": quando menciona SOMENTE filho em comum (sem outras evidências)
- "Filho + endereço": quando menciona filho em comum + endereço em comum (bases cadastrais)
- "Filho + CadÚnico": quando menciona filho em comum + declaração no CadÚnico
- "Mais de 1 filho": quando menciona 2 ou mais filhos em comum
- "Endereço em múltiplas bases": quando menciona endereço em comum em 2+ bases cadastrais (TSE, Receita Federal, RENACH, CNIS, DENATRAN, etc.) sem mencionar filho
- "Pensão do INSS como companheira": quando menciona recebimento de pensão por morte do companheiro no INSS
- "Achado não classificado": quando não se encaixa em nenhuma das categorias acima

⚠️ ATENÇÃO: Classifique baseado APENAS no que está escrito nas evidências, não faça suposições

Escolha um dos seguintes rótulos:

2. Identifique quais argumentos da defesa correspondem aos seguintes códigos e descrições:

🚨 REGRA CRÍTICA - NÃO OMITA ARGUMENTOS:
- Você DEVE analisar TODO o texto da defesa do início ao fim
- Liste TODOS os argumentos encontrados, mesmo que sejam muitos (10, 15, 20 argumentos)
- NÃO resuma, NÃO simplifique, NÃO omita alegações
- Se a requerente apresentou 20 alegações, você DEVE listar todas as 20
- Cada alegação distinta deve ser identificada e classificada
- A quantidade de argumentos NÃO tem limite - liste quantos forem necessários
{ARG_MAP}

⚠️ IMPORTANTE - Diferenciar CONFISSÃO vs NEGAÇÃO de filho:
- **Argumento 2** ("Filho em comum não caracteriza"): quando a defesa ADMITE que existe filho, mas NEGA que isso caracteriza união estável
- **Argumento 12** ("Defesa admite filho em comum"): USO ESPECÍFICO - Use SOMENTE quando:
  * O achado do TCU classificado é "Apenas CadÚnico" (extrato NÃO menciona filho)
  * E a defesa REVELA/ADMITE que existe filho em comum
  * Isso transforma o caso de prova fraca (só CadÚnico) em prova forte (CadÚnico + Filho revelado pela defesa)

⚠️ Importante: trate como **Argumento 2** quando mencionar:
- "filho em comum não significa união estável"
- "mera existência de filho não caracteriza"
- "filho não comprova união"
- E o achado do TCU JÁ INCLUI filho ("Apenas 1 filho", "Mais de 1 filho", "Filho + CadÚnico", "Filho + endereço")

⚠️ IMPORTANTE - Argumentos 6 e 9 têm prevalência ABSOLUTA:
- **Argumento 6** ("Decisão judicial transitada em julgado"):
  🚨 ATENÇÃO CRÍTICA: Use APENAS se o texto contiver PROVAS LITERAIS de decisão DO CASO CONCRETO

  ✅ PALAVRAS-CHAVE OBRIGATÓRIAS (deve ter pelo menos UMA destas):
  * Número de processo no formato CNJ: "0000000-00.0000.0.00.0000" ou "processo nº", "autos nº"
  * "decisão judicial favorável à interessada" + número do processo
  * "sentença transitada em julgado" + referência ao caso específico
  * "acórdão transitado em julgado" + número de processo
  * "decisão judicial do caso da Sra. [nome da pensionista]"
  * "sentença proferida nos autos de" + número do processo
  * "processo judicial da requerente" + número identificador
  * "decisão com trânsito em julgado" + menção específica ao caso

  ❌ NÃO USE Argumento 6 se encontrar APENAS:
  * "jurisprudência", "entendimento dos tribunais", "precedente judicial"
  * "decisão do STF/STJ/TRF sobre o tema"
  * "súmula", "acórdão paradigma", "tese jurídica"
  * citações genéricas de casos de terceiros
  * menção a "decisões judiciais" SEM número de processo específico
  * referências a "jurisprudência dominante" ou "entendimento consolidado"

  🔴 REGRA DE OURO: Se não há número de processo identificado E não menciona explicitamente "o caso da interessada/pensionista", NÃO é Argumento 6!

- **Argumento 9** ("Processo administrativo anterior sem novos elementos"):
  🚨 ATENÇÃO: Use quando a defesa mencionar que O CASO JÁ FOI JULGADO ADMINISTRATIVAMENTE antes

  ✅ USE Argumento 9 quando mencionar:
  * "Este caso já foi avaliado/auditado por este órgão anteriormente"
  * "Conforme Nota Técnica anterior, foi deferida a manutenção"
  * "Já existe decisão administrativa anterior favorável"
  * "Processo administrativo anterior (NUP 5000....) julgou pela manutenção"
  * "PAD anterior já analisou e deferiu o benefício"
  * "Decisão administrativa anterior manteve a pensão"
  * "Já foi objeto de processo administrativo sem novos elementos"
  * Qualquer menção a processo/NUP que comece com "5000"

  ❌ NÃO confunda com:
  * Citação de normas/portarias administrativas gerais
  * Menção a procedimentos administrativos genéricos
  * Referência a rotinas administrativas do órgão

⚠️ IMPORTANTE - Argumento 10 ("Testemunho de terceiros"):
- **Argumento 10**: Procure por QUALQUER menção a:
  * declarações de terceiros, testemunhas, depoimentos
  * cartas de vizinhos, amigos, familiares
  * declarações escritas, atas notariais
  * "fulano declarou que...", "segundo testemunha..."
  * "depoimento de...", "atestado por..."
  * qualquer prova testemunhal juntada aos autos

⚠️ EXEMPLOS PRÁTICOS - Argumento 6:

❌ NÃO é Argumento 6 (apenas jurisprudência genérica):
- "O TRF4 já decidiu que união estável não descaracteriza filha solteira"
- "Segundo entendimento do STF, a jurisprudência..."
- "Há decisões judiciais favoráveis sobre o tema"

✅ SIM é Argumento 6 (decisão DO CASO CONCRETO):
- "A manutenção da pensão é respaldada por decisão judicial transitada em julgado no processo 1234567-89.2020.4.04.1234"
- "Existe sentença favorável à interessada no processo da Sra. Maria"
- "Decisão judicial do caso concreto já julgou pela manutenção do benefício"

⚠️ EXEMPLOS PRÁTICOS - Argumento 9:

❌ NÃO é Argumento 9 (apenas menção genérica a procedimentos):
- "O procedimento administrativo deve ser conduzido conforme a Lei 9.784/99"
- "As normas administrativas determinam que..."
- "O órgão deve seguir os ritos administrativos estabelecidos"

✅ SIM é Argumento 9 (caso JÁ FOI JULGADO ADMINISTRATIVAMENTE):
- "Este mesmo caso já foi avaliado e devidamente auditado por este órgão, conforme Nota Técnica em anexo, foi deferida a manutenção da pensão"
- "Processo administrativo anterior (NUP 50001234567) já analisou a matéria e deferiu o benefício"
- "Já existe decisão administrativa favorável sem apresentação de novos elementos"
- "PAD anterior julgou pela manutenção, não havendo fatos novos que justifiquem nova análise"

3. Se existirem argumentos adicionais que não se enquadram nos 12 códigos acima, liste-os em "outros".

### Formato de saída
Responda apenas com JSON válido, sem explicações, sem Markdown, no seguinte formato:

{{
  "achado": "rótulo escolhido",
  "argumentos": ["1","4","11"],
  "outros": ["boa-fé", "segurança jurídica"]
}}
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return resp.choices[0].message.content

# --------- Extrair alegações do recurso em lista ---------
def extrair_alegacoes_recurso(texto_defesa):
    """Extrai as alegações/argumentos apresentados no recurso em formato de lista numerada."""
    prompt = f"""
Você é um especialista jurídico que analisa recursos administrativos.

Leia o texto do RECURSO abaixo e identifique TODAS as alegações/argumentos apresentados pela pensionista.

### Texto do Recurso:
{texto_defesa}

### Tarefa:
Liste todas as alegações em formato numerado simples e direto:

1ª alegação - [resumo da alegação em uma linha]
2ª alegação - [resumo da alegação em uma linha]
3ª alegação - [resumo da alegação em uma linha]

**Exemplos de alegações comuns:**
- nunca teve união estável
- foi apenas um relacionamento casual
- juntou depoimentos de terceiros
- erro nas bases cadastrais
- decisão judicial favorável
- apresentou certidão de casamento/divórcio
- etc.

**IMPORTANTE:**
- Liste TODAS as alegações encontradas
- Seja objetivo e conciso (uma linha por alegação)
- Mantenha a ordem em que aparecem no recurso
- Se não houver alegações, retorne: "Nenhuma alegação específica identificada"
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return resp.choices[0].message.content

# --------- GPT para narrativa formatada ---------
def extrair_argumentos_formatado(texto_defesa):
    prompt = f"""
Você é um especialista jurídico que deve resumir um recurso administrativo.

Leia o texto da DEFESA e produza um resumo organizado no seguinte formato:

Recurso apresentado (trechos relevantes)

[Nome do argumento]
"trecho literal da defesa..."
"outro trecho..."
→ Argumento X — [descrição]

Se houver argumento fora da lista (boa-fé, segurança jurídica, proteção da confiança etc.), inclua no final como:

Outro argumento não numerado
"trecho literal..."
→ Outro argumento não numerado

### Texto da defesa:
{texto_defesa}
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    return resp.choices[0].message.content

# --------- Recalcular achado baseado na defesa ---------
def recalcular_achado(achado_original, argumentos):
    """
    Recalcula o achado quando a defesa REVELA mais provas.
    Se a defesa menciona filho/CadÚnico, isso é um FATO que aumenta as provas.
    """
    achado_atualizado = achado_original

    # Se defesa menciona FILHO (Arg 2 ou 12), filho EXISTE como prova
    menciona_filho = "2" in argumentos or "12" in argumentos or "3" in argumentos

    # Regra 1: "Apenas CadÚnico" + defesa menciona filho → "Filho + CadÚnico"
    if achado_original == "Apenas CadÚnico" and menciona_filho:
        achado_atualizado = "Filho + CadÚnico"

    # Regra 2: "Apenas 1 filho" + defesa menciona CadÚnico (Arg 11) → "Filho + CadÚnico"
    elif achado_original == "Apenas 1 filho" and "11" in argumentos:
        achado_atualizado = "Filho + CadÚnico"

    # Regra 3: "Apenas 1 filho" + defesa menciona MAIS filhos (Arg 3) → "Mais de 1 filho"
    elif achado_original == "Apenas 1 filho" and "3" in argumentos:
        achado_atualizado = "Mais de 1 filho"

    return achado_atualizado

# --------- Aplicar matriz ---------
def analisar_com_matriz(achado, argumentos):
    # PRIMEIRO: Recalcular achado se defesa revelar mais provas
    achado_recalculado = recalcular_achado(achado, argumentos)

    improc, proc = [], []

    # Se não há argumentos, buscar regra "Nenhum argumento apresentado"
    if not argumentos or len(argumentos) == 0:
        regra = matriz[(matriz["achado"] == achado_recalculado) & (matriz["argumento"] == "Nenhum argumento apresentado")]
        if not regra.empty:
            res = regra["resultado"].iloc[0]
            saida1 = res
            mensagem_achado = f" (achado original: {achado})" if achado != achado_recalculado else ""
            saida2 = f"Decisão baseada em: {achado_recalculado}{mensagem_achado} + Nenhum argumento apresentado = {res}"
            return saida1, saida2

    # Se há argumentos, processar normalmente
    for num in argumentos:
        arg_texto = ARG_MAP.get(num, num)
        regra = matriz[(matriz["achado"] == achado_recalculado) & (matriz["argumento"] == arg_texto)]
        if regra.empty:
            regra = matriz[(matriz["achado"] == "Qualquer achado") & (matriz["argumento"] == arg_texto)]
        if not regra.empty:
            res = regra["resultado"].iloc[0]
            (improc if res == "improcedente" else proc).append(num)

    # Argumentos com prevalência absoluta (sempre procedente)
    if "6" in argumentos or "9" in argumentos:
        saida1 = "procedente"
    else:
        saida1 = "improcedente" if len(improc) >= len(proc) else "procedente"

    # Mensagem mostra se achado foi recalculado
    info_recalculo = f"\n⚠️ Achado recalculado: {achado} → {achado_recalculado} (defesa revelou mais provas)" if achado != achado_recalculado else ""

    # Converter números em textos descritivos
    improc_textos = [ARG_MAP.get(num, f"Argumento {num}") for num in improc]
    proc_textos = [ARG_MAP.get(num, f"Argumento {num}") for num in proc]

    # Montar mensagens descritivas
    msg_improc = f"improcedente por: {', '.join(improc_textos)}" if improc_textos else ""
    msg_proc = f"procedente por: {', '.join(proc_textos)}" if proc_textos else ""

    # Combinar mensagens
    mensagens = [m for m in [msg_improc, msg_proc] if m]
    saida2 = '\n'.join(mensagens) + info_recalculo

    # Retornar também as listas de argumentos filtrados
    return saida1, saida2, improc, proc

# --------- Gerar corpo do ofício ---------
def gerar_corpo_oficio(decisao, achado, argumentos, outros, alegacoes, texto_defesa_previa, dados_identificacao, descricao_indicio):
    """
    Gera o corpo do ofício usando GPT com RAG (Retrieval-Augmented Generation).
    O GPT lê os textos dos templates item 15 e 13 e gera APENAS o item 15/13 (análise dos argumentos).
    """
    try:
        from templates_textos import (
            ITEM15_ACHADOS, ITEM15_ARGUMENTOS,
            ITEM13_ACHADOS, ITEM13_ARGUMENTOS
        )
    except ImportError:
        return "ERRO: Arquivo templates_textos.py não encontrado. Verifique se o arquivo existe no diretório."

    # Preparar lista de argumentos apresentados
    args_lista = "\n".join([f"- Argumento {num}: {ARG_MAP.get(num, 'Não identificado')}" for num in argumentos])

    # Selecionar templates conforme decisão
    if decisao == "improcedente":
        dict_achados = ITEM15_ACHADOS
        dict_argumentos = ITEM15_ARGUMENTOS
        item_num = "15"
    else:
        dict_achados = ITEM13_ACHADOS
        dict_argumentos = ITEM13_ARGUMENTOS
        item_num = "13"

    # Montar textos de referência do template
    texto_achado_ref = dict_achados.get(achado, "[Achado não encontrado no template]")

    textos_args_ref = []
    for num in argumentos:
        texto_arg = dict_argumentos.get(num)
        if texto_arg:
            arg_nome = ARG_MAP.get(num, f"Argumento {num}")
            textos_args_ref.append(f"**{arg_nome}:**\n{texto_arg}")

    textos_args_formatados = "\n\n".join(textos_args_ref) if textos_args_ref else "[Nenhum argumento mapeado no template]"

    prompt = f"""
Você é um sistema de montagem de documentos que usa textos literais pré-definidos.

### INSTRUÇÕES CRÍTICAS
Gere APENAS o ITEM {item_num} usando EXATAMENTE os textos fornecidos abaixo.
NÃO gere item 16, conclusão ou outros parágrafos.

### ARGUMENTOS DO CASO
{args_lista}

### TEXTOS LITERAIS (COPIE EXATAMENTE COMO ESTÃO)

#### TEXTO PARA O ACHADO "{achado}":
{texto_achado_ref}

#### TEXTOS PARA CADA ARGUMENTO:
{textos_args_formatados}

### TAREFA
Monte o item {item_num} no seguinte formato:

**{item_num}. Dos argumentos apresentados no recurso pela Interessada, segue análise:**

Depois, para cada argumento na lista acima:
- Adicione o título do argumento
- Cole o texto literal fornecido para aquele argumento
- Pule uma linha

**REGRAS ABSOLUTAS:**
- Gere APENAS o item {item_num}
- NÃO inclua item 16
- NÃO inclua conclusão (parágrafos 17-20)
- USE os textos LITERAIS - NÃO reescreva
- NÃO invente textos novos
- PARE após o último argumento
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1  # Baixa temperatura para manter fidelidade aos templates
    )

    return resp.choices[0].message.content

# --------- Carregar logo em base64 ---------
def get_logo_base64():
    """Carrega robo.png de forma compatível com Streamlit Cloud e local."""
    try:
        # Caminho relativo à pasta do app.py
        path_local = Path(__file__).parent / "robo.png"
        if path_local.exists():
            with open(path_local, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()

        # Caminho alternativo: subpasta "assets"
        path_assets = Path(__file__).parent / "assets" / "robo.png"
        if path_assets.exists():
            with open(path_assets, "rb") as img_file:
                return base64.b64encode(img_file.read()).decode()

        # Caso não encontre, mostra aviso
        st.warning("⚠️ Logo do robô não encontrado (robo.png). Exibindo título padrão.")
        return None
    except Exception as e:
        st.error(f"❌ Erro ao carregar logo: {e}")
        return None

# ------------------ INTERFACE ------------------

# CSS Customizado - Tema Institucional Moderno MGI/ENAP/TCU
st.markdown("""
<style>
/* Importar fonte Inter (moderna e institucional) */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ====== LAYOUT GERAL ====== */
.stApp {
    background-color: #ffffff !important;
    font-family: 'Inter', sans-serif;
    color: #1f1f1f;
}

/* ============================================================
   LAYOUT FLUIDO — ESTILO GOV.BR / ENAP / MGI
   Expande horizontalmente, mas mantém limite de leitura.
   ============================================================ */

/* 🔹 Expande o container principal */
.block-container {
    max-width: 1200px !important;   /* largura máxima */
    width: 100% !important;         /* ocupa toda a área disponível */
    margin: 0 auto !important;      /* centraliza no meio da tela */
    padding-left: 2.5rem !important;
    padding-right: 2.5rem !important;
    transition: all 0.3s ease-in-out;
}

/* 🔹 Remove restrições de largura herdadas em seções internas */
section[data-testid="stVerticalBlock"],
section[data-testid="stHorizontalBlock"] {
    width: 100% !important;
    max-width: 100% !important;
}

/* 🔹 Margens verticais mais equilibradas */
.main .block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 0.5rem !important;
}

/* 🔹 Remove espaçamentos verticais excessivos do Streamlit */
div.block-container {
    padding-top: 0.5rem !important;
    padding-bottom: 0.5rem !important;
}

/* 🔹 Reduz margens entre seções */
div[data-testid="stVerticalBlock"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}

/* 🔹 Reduz espaços entre colunas */
div[data-testid="stHorizontalBlock"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

/* 🔹 Remove margens extras entre botões e containers */
button, .stButton {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}

/* 🔹 Upload boxes e cards em proporção uniforme */
[data-testid="stHorizontalBlock"] > div {
    flex: 1 1 48% !important;
}

/* ====== TIPOGRAFIA ====== */
h1 {
    font-family: 'Inter', sans-serif !important;
    color: #1e3a8a !important;
    font-weight: 700 !important;
    letter-spacing: 0.5px !important;
}

h2, h3 {
    font-family: 'Inter', sans-serif !important;
    color: #1e3a8a !important;
    font-weight: 700 !important;
}

h2::before {
    content: '' !important;
}

/* ====== LOGO DO ROBÔ ====== */
img[alt="robo"], .logo-robo-pulse {
    width: 120px !important;
    height: auto !important;
    mix-blend-mode: multiply !important;
    background: none !important;
    opacity: 0.95;
    filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.25));
}

/* ====== CABEÇALHO ====== */
.header-container {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 15px 25px;
    background: #ffffff;
    border-radius: 10px;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
    margin-bottom: 20px;
    border: 1px solid #e5e7eb;
}

.header-left {
    display: flex;
    align-items: center;
    gap: 20px;
}

.header-right {
    display: flex;
    gap: 10px;
}

/* ====== CARDS ====== */
.element-container {
    background-color: #ffffff !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05) !important;
    padding: 15px !important;
}

/* ====== BOTÕES ====== */
.stButton button {
    background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 8px 18px !important;
    font-size: 14px !important;
    font-weight: 600 !important;
    cursor: pointer !important;
    box-shadow: 0 2px 6px rgba(0,0,0,0.1) !important;
    transition: all 0.2s ease-in-out !important;
}

.stButton button:hover {
    transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(0,0,0,0.15);
}

.stButton button[kind="secondary"] {
    background: #ffffff !important;
    color: #1e3a8a !important;
    border: 2px solid #1e3a8a !important;
}

.stButton button[kind="secondary"]:hover {
    background: #eef2ff !important;
}

.stDownloadButton button {
    background: linear-gradient(135deg, #168821 0%, #1a9b28 100%) !important;
    color: #ffffff !important;
}

/* ====== UPLOAD BOXES ====== */
[data-testid="stFileUploader"] {
    border: 2px dashed #2563eb !important;
    border-radius: 10px !important;
    background-color: #ffffff !important;
    min-height: 160px !important;
    text-align: center !important;
    box-shadow: 0 1px 5px rgba(0,0,0,0.05);
    position: relative;
    padding: 30px !important;
}

[data-testid="stFileUploader"]:hover {
    background-color: #f0f4ff !important;
}

[data-testid="column"]:nth-child(1) [data-testid="stFileUploader"]::before {
    content: "📄 Insira o PDF do Extrato (TCU) aqui";
    position: absolute;
    top: 10px;
    left: 0;
    right: 0;
    color: #1e3a8a;
    font-weight: 600;
}

[data-testid="column"]:nth-child(2) [data-testid="stFileUploader"]::before {
    content: "📑 Insira o PDF do Recurso da Pensionista aqui";
    position: absolute;
    top: 10px;
    left: 0;
    right: 0;
    color: #1e3a8a;
    font-weight: 600;
}

/* ====== INPUTS ====== */
.stTextInput input, .stTextArea textarea {
    background: #ffffff !important;
    border: 1.5px solid #e5e7eb !important;
    border-radius: 8px !important;
}

.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #2563eb !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1) !important;
}

/* ====== ALERTAS ====== */
.stInfo {
    background: #eef2ff !important;
    border-left: 4px solid #2563eb !important;
    border-radius: 10px !important;
}

.stSuccess {
    background: #e6f7e6 !important;
    border-left: 4px solid #168821 !important;
    border-radius: 10px !important;
}

.stWarning {
    background: #fff5e6 !important;
    border-left: 4px solid #f59e0b !important;
    border-radius: 10px !important;
}

.stError {
    background: #fee2e2 !important;
    border-left: 4px solid #E52207 !important;
    border-radius: 10px !important;
}

/* ====== SIDEBAR ====== */
[data-testid="stSidebar"] {
    background: #f0f4ff !important;
    border-right: 1px solid #d1d5db !important;
}

[data-testid="stSidebar"] h2 {
    color: #1e3a8a !important;
    border-bottom: 2px solid #2563eb !important;
}

/* ====== MÉTRICAS ====== */
[data-testid="stMetric"] {
    background: #f9fafb !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 10px !important;
}

[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #1e3a8a !important;
    font-weight: 700 !important;
}

/* ====== EXPANDER ====== */
.streamlit-expanderHeader {
    background: #f9fafb !important;
    border: 1px solid #e5e7eb !important;
    border-radius: 10px !important;
}

.streamlit-expanderHeader:hover {
    background: #eef2ff !important;
    border-color: #2563eb !important;
}

/* ====== AJUSTES ESPECÍFICOS HEADER ====== */
/* Botões compactos no topo */
div[data-testid="column"] button[kind="secondary"] {
    font-size: 0.8rem !important;
    padding: 6px 12px !important;
    height: 32px !important;
    white-space: nowrap !important;
}

/* ====== RESPONSIVIDADE ====== */

/* 🔹 Ajusta responsividade (tablet) */
@media (max-width: 1024px) {
    .block-container {
        max-width: 95% !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
    }
}

/* 🔹 Ajusta responsividade (mobile) */
@media (max-width: 768px) {
    .block-container {
        max-width: 100% !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }

    /* Cards empilham verticalmente */
    [data-testid="stHorizontalBlock"] > div {
        flex: 1 1 100% !important;
    }

    /* Título e robô reduzem */
    img[alt="robo"] {
        width: 90px !important;
    }

    h1 {
        font-size: 1.6em !important;
    }

    .stButton button {
        padding: 6px 10px !important;
        font-size: 12px !important;
    }
}
</style>
""", unsafe_allow_html=True)

# =========================
# CABEÇALHO FINAL — VERSÃO INSTITUCIONAL AJUSTADA
# =========================
logo_base64 = get_logo_base64()

# Faixa institucional superior
st.markdown("""
<div style="
    background:#1e3a8a;
    color:#ffffff;
    font-family:'Inter',sans-serif;
    font-weight:600;
    font-size:14px;
    letter-spacing:0.5px;
    padding:6px 18px;
    border-radius:6px 6px 0 0;
    text-align:left;
    box-shadow:0 2px 4px rgba(0,0,0,0.1);
">
    DECIPEX — Coordenação-Geral de Risco e Controle
</div>
""", unsafe_allow_html=True)

# CSS para estilização dos botões na terceira coluna
st.markdown("""
<style>
/* 🔹 Alinha botões à direita, com proporção harmoniosa */
div[data-testid="column"]:nth-child(3) .stButton > button {
    background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%) !important;
    color: white !important;
    font-weight: 600 !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
    box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    transition: all 0.2s ease-in-out;
}
div[data-testid="column"]:nth-child(3) .stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 3px 8px rgba(0,0,0,0.15);
}

/* 🔹 Remove padding desnecessário do topo */
div[data-testid="stHorizontalBlock"] {
    margin-top: 0 !important;
    margin-bottom: 0 !important;
}
</style>
""", unsafe_allow_html=True)

# Cabeçalho com botões integrados (layout 3 colunas)
col_logo, col_titulo, col_botoes = st.columns([1.2, 4, 2])

with col_logo:
    st.markdown(f"""
    <img src="data:image/png;base64,{logo_base64}" alt="robo" class="logo-robo-pulse"
         style="width:120px; height:auto; margin-top:10px;">
    """, unsafe_allow_html=True)

with col_titulo:
    st.markdown("""
    <h1 style="margin:0; font-size:2em; color:#1e3a8a; font-weight:700; line-height:1.2; margin-top:15px;">
        Analisador de Recursos
    </h1>
    <p style="margin:3px 0 0 0; color:#6b7280; font-size:0.9rem; font-weight:500;">
        Pensão por Morte — Filha Maior Solteira
    </p>
    """, unsafe_allow_html=True)

with col_botoes:
    # Botão Processar
    if st.button("🧠 Processar", help="Analisa feedbacks do B2 e gera insights", type="secondary", disabled=not B2_CONFIGURED, use_container_width=True, key="btn_processar"):
        with st.spinner("🔄 Processando feedbacks do B2..."):
            resultado = processar_feedbacks_para_aprendizado()

        # Exibir resultado em modal/expander
        with st.expander("📊 Relatório de Feedbacks", expanded=True):
            if 'erro' in resultado:
                st.error(f"❌ Erro: {resultado['erro']}")
            else:
                # Métricas
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total", resultado['total'])
                with col2:
                    st.metric("✅ Corretos", resultado['corretos'])
                with col3:
                    st.metric("❌ Incorretos", resultado['incorretos'])
                with col4:
                    st.metric("Taxa de Acerto", resultado['taxa_acerto'])

                st.divider()

                # Insights
                st.markdown("### 💡 Insights e Recomendações")
                st.markdown(resultado['insights'])

                st.divider()

                # Padrões de erro
                if resultado['padroes_erro']:
                    st.markdown("### ⚠️ Padrões de Erro Identificados")
                    for i, erro in enumerate(resultado['padroes_erro'][:5], 1):
                        st.warning(f"""
**Erro {i}:**
- **Achado:** {erro['achado']}
- **Decisão:** {erro['decisao']}
- **Problema:** {erro['problema']}
                        """)

                # Exemplos corretos
                if resultado['exemplos_corretos']:
                    st.markdown("### ✅ Exemplos de Análises Corretas")
                    st.caption(f"Mostrando {len(resultado['exemplos_corretos'])} melhores exemplos")
                    for i, ex in enumerate(resultado['exemplos_corretos'], 1):
                        with st.expander(f"Exemplo {i}: {ex['achado']} → {ex['decisao']}"):
                            st.text(ex['corpo_oficio'])

    # Botão Limpar
    if st.button("🔄 Limpar", help="Limpar tudo e recomeçar", type="secondary", use_container_width=True, key="btn_reiniciar"):
        # Limpar session_state
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# Aviso se B2 não estiver configurado
if not B2_CONFIGURED:
    st.warning("⚠️ **Sistema de Feedbacks Desabilitado** - Configure as credenciais B2 S3-compatible nos Secrets")

st.markdown("<br>", unsafe_allow_html=True)

# Layout em 2 colunas para uploads
col_upload1, col_upload2 = st.columns(2)

with col_upload1:
    st.markdown("### 1️⃣ PDF do Extrato (TCU)")
    extrato_file = st.file_uploader("Selecione o arquivo PDF do extrato", type=["pdf"], key="extrato", label_visibility="collapsed")

with col_upload2:
    st.markdown("### 2️⃣ PDF do Recurso")
    defesa_file = st.file_uploader("Selecione o arquivo PDF do recurso", type=["pdf"], key="recurso", label_visibility="collapsed")

if extrato_file and defesa_file:
    texto_extrato = extrair_texto(extrato_file)
    texto_defesa = extrair_texto(defesa_file)

    # --- Extrair dados de identificação ---
    with st.spinner("🔎 Extraindo dados de identificação..."):
        saida_identificacao = extrair_dados_identificacao(texto_extrato)

    try:
        saida_limpa_id = saida_identificacao.strip()
        if saida_limpa_id.startswith("```"):
            saida_limpa_id = saida_limpa_id.strip("`")
            saida_limpa_id = saida_limpa_id.replace("json", "", 1).strip()
        dados_identificacao = json.loads(saida_limpa_id)
    except Exception:
        st.error(f"⚠️ Erro ao extrair dados de identificação. Retorno bruto:\n{saida_identificacao}")
        dados_identificacao = {"nome": None, "cpf": None, "codigo_indicio": None}

    # 3. Dados da Pensionista
    col_header3, col_copy3 = st.columns([9, 1])
    with col_header3:
        st.markdown("### 3️⃣ Dados da Pensionista")

    # 🚨 AVISO PARA O ANALISTA
    st.warning("🚨 **ATENÇÃO:** Não esqueça de alterar o **ITEM 1** da Nota Técnica no SEI com estes dados!")

    with col_copy3:
        nome = dados_identificacao.get("nome", "Não identificado")
        cpf = dados_identificacao.get("cpf", "Não identificado")
        codigo = dados_identificacao.get("codigo_indicio", "Não identificado")
        dados_texto = f"Nome: {nome}\nCPF: {cpf}\nCódigo: {codigo}"
        if st.button("📋", key="copy_dados", help="Copiar dados da pensionista"):
            st.code(dados_texto, language=None)

    # Layout em 3 colunas para dados compactos
    col_nome, col_cpf, col_codigo = st.columns(3)
    with col_nome:
        st.markdown(f"""
        <div style="background: #ffffff; padding: 1rem; border-radius: 10px; border-left: 4px solid #2563eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; border-left: 4px solid #2563eb;">
            <div style="color: #2563eb; font-size: 0.75rem; font-family: 'Inter', sans-serif; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Nome</div>
            <div style="color: #1f2937; font-size: 0.9rem; margin-top: 0.5rem; font-weight: 500; line-height: 1.4;">{nome}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_cpf:
        st.markdown(f"""
        <div style="background: #ffffff; padding: 1rem; border-radius: 10px; border-left: 4px solid #2563eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; border-left: 4px solid #2563eb;">
            <div style="color: #2563eb; font-size: 0.75rem; font-family: 'Inter', sans-serif; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">CPF</div>
            <div style="color: #1f2937; font-size: 0.9rem; margin-top: 0.5rem; font-weight: 500;">{cpf}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_codigo:
        st.markdown(f"""
        <div style="background: #ffffff; padding: 1rem; border-radius: 10px; border-left: 4px solid #2563eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border: 1px solid #e5e7eb; border-left: 4px solid #2563eb;">
            <div style="color: #2563eb; font-size: 0.75rem; font-family: 'Inter', sans-serif; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Código Indício</div>
            <div style="color: #1f2937; font-size: 0.9rem; margin-top: 0.5rem; font-weight: 500;">{codigo}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 4. Descrição do Indício (TCU)
    descricao_indicio = dados_identificacao.get("descricao_indicio", None)

    col_header4, col_copy4 = st.columns([9, 1])
    with col_header4:
        st.markdown("### 4️⃣ Descrição do Indício (TCU)")
    with col_copy4:
        if descricao_indicio and st.button("📋", key="copy_descricao", help="Copiar descrição do indício"):
            st.code(descricao_indicio, language=None)

    if descricao_indicio:
        st.markdown(f"""
        <div style="background: #ffffff; padding: 1.25rem; border-radius: 10px; color: #1f2937; font-weight: 400; line-height: 1.7; border: 1px solid #e5e7eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); font-size: 0.95rem;">
        {descricao_indicio}
        </div>
        """, unsafe_allow_html=True)
    else:
        st.warning("⚠️ Descrição do indício não foi identificada no extrato.")

    st.markdown("<br>", unsafe_allow_html=True)

    # 5. Achado TCU (classificação baseada APENAS no extrato)
    st.markdown("### 5️⃣ Achado TCU")

    with st.spinner("🔎 Classificando achado do TCU..."):
        # Usar a descrição do indício extraída, não o texto completo do extrato
        descricao_para_analise = descricao_indicio if descricao_indicio else texto_extrato
        saida_gpt = classificar_com_gpt(descricao_para_analise, texto_defesa)

    try:
        saida_limpa = saida_gpt.strip()
        if saida_limpa.startswith("```"):
            saida_limpa = saida_limpa.strip("`")
            saida_limpa = saida_limpa.replace("json", "", 1).strip()
        parsed = json.loads(saida_limpa)
    except Exception:
        st.error(f"⚠️ Erro ao ler resposta do GPT. Retorno bruto:\n{saida_gpt}")
        st.stop()

    achado = parsed.get("achado", "Achado não classificado")
    argumentos = parsed.get("argumentos", [])
    outros = parsed.get("outros", [])

    # 🔹 VALIDAÇÃO PÓS-GPT: Filtros programáticos para reduzir falsos-positivos
    argumentos_validados = []

    for arg in argumentos:
        incluir_argumento = True

        # 🔹 Validação Argumento 6 (decisão judicial do caso concreto)
        if arg == "6":
            # Verifica se há número de processo no formato CNJ ou menção a "transitado em julgado"
            tem_numero_processo = bool(re.search(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}', texto_defesa))
            tem_transito = bool(re.search(r'trânsit|transitad', texto_defesa, re.IGNORECASE))
            tem_processo_especifico = re.search(r'(processo|autos)\s+(n[º°]|número)', texto_defesa, re.IGNORECASE)

            # Se não tem número de processo E não menciona trânsito em julgado E não menciona processo específico
            if not tem_numero_processo and not tem_transito and not tem_processo_especifico:
                incluir_argumento = False

            # Filtro adicional: se menciona "jurisprudência" sem número de processo, provavelmente é falso-positivo
            tem_jurisprudencia = re.search(r'(jurisprudência|precedente|súmula|entendimento\s+dos?\s+tribunal)', texto_defesa, re.IGNORECASE)
            if tem_jurisprudencia and not tem_numero_processo:
                incluir_argumento = False

        # 🔹 Validação Argumento 9 (processo administrativo anterior)
        elif arg == "9":
            # Garante que há termos administrativos explícitos
            tem_termos_admin = bool(re.search(
                r'(NUP|processo\s+administrativo|Nota\s+Técnica|PAD|já\s+foi\s+(analisado|avaliado|auditado|julgado)|decisão\s+administrativa\s+anterior)',
                texto_defesa,
                re.IGNORECASE
            ))

            if not tem_termos_admin:
                incluir_argumento = False

        # Se passou nas validações, incluir o argumento
        if incluir_argumento:
            argumentos_validados.append(arg)

    # Substituir lista de argumentos pela lista validada
    argumentos = argumentos_validados

    # Salvar achado no session_state para usar no feedback
    st.session_state.achado_atual = achado

    st.markdown(f"""
    <div style="background: #eef2ff; padding: 1rem 1.25rem; border-radius: 10px; border-left: 4px solid #2563eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05);">
        <span style="color: #1e3a8a; font-family: 'Inter', sans-serif; font-weight: 700; font-size: 0.9rem;">📊 Achado classificado:</span>
        <span style="color: #1f2937; font-size: 1rem; margin-left: 0.5rem; font-weight: 600;">{achado}</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 6. Recurso apresentado (alegações da pensionista)
    st.markdown("### 6️⃣ Recurso apresentado")

    with st.spinner("📝 Extraindo alegações do recurso..."):
        alegacoes_recurso = extrair_alegacoes_recurso(texto_defesa)

    st.markdown(f"""
    <div style="background: #ffffff; padding: 1.25rem; border-radius: 10px; color: #1f2937; font-weight: 400; line-height: 1.7; white-space: pre-wrap; border: 1px solid #e5e7eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); font-size: 0.95rem;">
    {alegacoes_recurso}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 7. Pergunta sobre defesa prévia
    col_header7, col_copy7 = st.columns([9, 1])
    with col_header7:
        st.markdown("### 7️⃣ Defesa Prévia")

    # 🚨 AVISO PARA O ANALISTA
    st.warning("🚨 **ATENÇÃO:** NÃO ESQUEÇA DE ALTERAR O **ITEM 3** DA NOTA TÉCNICA PADRÃO!")

    col_radio1, col_radio2 = st.columns([2, 8])
    with col_radio1:
        defesa_previa = st.radio(
            "A pensionista havia apresentado defesa anteriormente?",
            ["Sim", "Não"],
            key="defesa_previa",
            label_visibility="collapsed"
        )

    if defesa_previa == "Sim":
        texto_defesa_previa = """A Interessada foi devidamente notificada para apresentar defesa em observância aos princípios do contraditório e ampla defesa. Tendo sua defesa sido analisada e julgada na decisão administrativa anterior. Inconformada, a Interessada apresentou recurso tempestivo, o qual passa a ser objeto da presente Nota Técnica."""
    else:
        texto_defesa_previa = """A Interessada foi devidamente notificada para apresentar defesa em observância aos princípios do contraditório e ampla defesa. Todavia registrou-se a ausência de defesa, razão pela qual a decisão administrativa anterior foi proferida com base nos elementos constantes dos autos. Ainda assim, a Interessada apresentou recurso tempestivo, que agora se examina na presente Nota Técnica."""

    with col_copy7:
        if st.button("📋", key="copy_defesa_previa", help="Copiar texto da defesa prévia"):
            st.code(texto_defesa_previa, language=None)

    st.markdown(f"""
    <div style="background: #ffffff; padding: 1.25rem; border-radius: 10px; color: #1f2937; font-weight: 400; line-height: 1.7; border: 1px solid #e5e7eb; box-shadow: 0 2px 10px rgba(0,0,0,0.05); font-size: 0.95rem;">
    {texto_defesa_previa}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 8. Decisão
    st.markdown("### 8️⃣ Decisão")

    s1, s2, args_improc, args_proc = analisar_com_matriz(achado, argumentos)

    if s1 == "procedente":
        st.markdown(f"""
        <div style="background: #e6f7e6; padding: 1.5rem; border-radius: 8px; border-left: 5px solid #168821; box-shadow: 0 2px 8px rgba(22,136,33,0.15);">
            <div style="color: #168821; font-size: 1.4rem; font-family: 'Inter', sans-serif; font-weight: 700;">✅ RECURSO PROCEDENTE</div>
            <div style="color: #333333; margin-top: 0.75rem; font-size: 0.95rem; line-height: 1.6; white-space: pre-wrap;">{s2}</div>
        </div>
        """, unsafe_allow_html=True)

        # 🚨 AVISO MODELO SEI PROCEDENTE
        st.info("📋 **MODELO SEI:** Use o modelo **54053871** (Recurso PROCEDENTE)")
    else:
        st.markdown(f"""
        <div style="background: #ffe6e6; padding: 1.5rem; border-radius: 8px; border-left: 5px solid #E52207; box-shadow: 0 2px 8px rgba(229,34,7,0.15);">
            <div style="color: #E52207; font-size: 1.4rem; font-family: 'Inter', sans-serif; font-weight: 700;">❌ RECURSO IMPROCEDENTE</div>
            <div style="color: #333333; margin-top: 0.75rem; font-size: 0.95rem; line-height: 1.6; white-space: pre-wrap;">{s2}</div>
        </div>
        """, unsafe_allow_html=True)

        # 🚨 AVISO MODELO SEI IMPROCEDENTE
        st.info("📋 **MODELO SEI:** Use o modelo **53937770** (Recurso IMPROCEDENTE)")

    st.markdown("<br>", unsafe_allow_html=True)

    # 9. Pontos de Atenção
    st.markdown("### 9️⃣ Pontos de Atenção")

    # --- Outros argumentos não mapeados ---
    if outros:
        st.warning(f"⚠️ **Argumentos não mapeados:** {', '.join(outros)}")

        # Sugestão automática de resposta
        sugestao = []
        if any("boa-fé" in o.lower() or "segurança jurídica" in o.lower() for o in outros):
            sugestao.append(
                "A invocação de boa-fé e segurança jurídica não descaracteriza o achado. "
                "O TCU entende que a manutenção do benefício depende da ausência de união estável, "
                "independentemente da confiança legítima ou da boa-fé alegada."
            )
        if sugestao:
            st.info("💡 **Sugestão de resposta:**")
            for s in sugestao:
                st.write(s)
    else:
        st.success("✅ Todos os argumentos foram mapeados com sucesso.")

    st.markdown("<br>", unsafe_allow_html=True)

    # 10. Corpo do Ofício
    st.markdown("### 🔟 Corpo do Ofício")

    col_btn_gerar, col_btn_download = st.columns([2, 2])

    with col_btn_gerar:
        if st.button("🚀 Gerar Corpo do Ofício", type="primary", key="gerar_oficio", use_container_width=True):
            # Usar session_state para armazenar o ofício gerado
            with st.spinner("Gerando ofício..."):
                # Filtrar argumentos conforme decisão
                # Se procedente, usar apenas args procedentes; se improcedente, usar apenas args improcedentes
                args_filtrados = args_proc if s1 == "procedente" else args_improc

                # Gerar ofício com análise dos argumentos FILTRADOS
                st.session_state.corpo_oficio = gerar_corpo_oficio(
                    decisao=s1,
                    achado=achado,
                    argumentos=args_filtrados,
                    outros=outros,
                    alegacoes=alegacoes_recurso,
                    texto_defesa_previa=texto_defesa_previa,
                    dados_identificacao=dados_identificacao,
                    descricao_indicio=descricao_indicio
                )
                st.session_state.dados_oficio = {
                    'nome': nome,
                    'cpf': cpf,
                    'codigo': codigo,
                    'decisao': s1
                }

            st.success("✅ Ofício gerado com sucesso! Veja na barra lateral →")

    # Botão de download (sempre visível se já foi gerado)
    with col_btn_download:
        if 'corpo_oficio' in st.session_state and 'dados_oficio' in st.session_state:
            dados = st.session_state.dados_oficio
            st.download_button(
                label="📥 Baixar Ofício (.txt)",
                data=st.session_state.corpo_oficio,
                file_name=f"oficio_{dados['decisao']}_{dados['codigo']}_{dados['nome'][:30].replace(' ', '_')}.txt",
                mime="text/plain",
                key="download_oficio",
                use_container_width=True
            )

# CSS para aumentar a largura da sidebar
st.markdown("""
<style>
[data-testid="stSidebar"] {
    min-width: 50% !important;
    max-width: 50% !important;
}
</style>
""", unsafe_allow_html=True)

# SIDEBAR - Mostrar o ofício gerado (estilo artefato do Claude)
if 'corpo_oficio' in st.session_state:
    with st.sidebar:
        st.markdown("## 📄 Nota Técnica (SEI)")

        dados = st.session_state.dados_oficio

        # Botões de ação no topo
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📋 Copiar", key="copy_sidebar", use_container_width=True, type="primary"):
                st.code(st.session_state.corpo_oficio, language=None)
        with col2:
            st.download_button(
                label="💾 Baixar",
                data=st.session_state.corpo_oficio,
                file_name=f"nota_tecnica_{dados['codigo']}.txt",
                mime="text/plain",
                key="download_sidebar",
                use_container_width=True
            )

        st.markdown("---")

        # Cabeçalho do documento
        st.info(f"""
**Pensionista:** {dados['nome']}
**CPF:** {dados['cpf']}
**Código:** {dados['codigo']}
**Decisão:** {dados['decisao'].upper()}
        """)

        st.markdown("---")

        # Corpo do ofício com formatação melhorada - FUNDO BRANCO
        st.markdown(f"""
        <div style="
            background-color: #ffffff;
            color: #000000;
            line-height: 1.8;
            font-size: 14px;
            text-align: justify;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #00d9ff;
        ">
        {st.session_state.corpo_oficio.replace(chr(10), '<br><br>')}
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

        # Sistema de Feedback
        st.markdown("### 💬 Avaliação da Análise")
        st.caption("Sua avaliação ajuda a melhorar o sistema")

        # CSS customizado para os botões de feedback
        st.markdown("""
        <style>
        /* Botão verde para análise correta */
        div[data-testid="stHorizontalBlock"] button[kind="primary"]:has(p:contains("✅")) {
            background-color: #28a745 !important;
            border-color: #28a745 !important;
        }
        div[data-testid="stHorizontalBlock"] button[kind="primary"]:has(p:contains("✅")):hover {
            background-color: #218838 !important;
            border-color: #1e7e34 !important;
        }

        /* Botão vermelho para análise incorreta */
        div[data-testid="stHorizontalBlock"] button[kind="secondary"]:has(p:contains("❌")) {
            background-color: #dc3545 !important;
            border-color: #dc3545 !important;
            color: white !important;
        }
        div[data-testid="stHorizontalBlock"] button[kind="secondary"]:has(p:contains("❌")):hover {
            background-color: #c82333 !important;
            border-color: #bd2130 !important;
        }
        </style>
        """, unsafe_allow_html=True)

        col_feedback1, col_feedback2 = st.columns(2)

        with col_feedback1:
            if st.button("✅ Análise Correta", key="feedback_correto", use_container_width=True, type="primary"):
                # Ativar modo de sugestão opcional
                st.session_state.mostrar_sugestao = True

        with col_feedback2:
            if st.button("❌ Análise Incorreta", key="feedback_incorreto", use_container_width=True):
                # Abrir campo para comentário obrigatório
                st.session_state.mostrar_comentario = True

        # Campo de sugestão (OPCIONAL - análise correta)
        if st.session_state.get('mostrar_sugestao', False):
            st.success("✅ **Análise marcada como CORRETA**")

            sugestao = st.text_area(
                "💡 Deseja sugerir melhorias? (opcional)",
                placeholder="Se tiver alguma sugestão de melhoria, compartilhe conosco...",
                key="sugestao_feedback",
                height=80
            )

            col_env1, col_env2 = st.columns(2)
            with col_env1:
                if st.button("Enviar Feedback", key="enviar_sugestao", type="primary", use_container_width=True):
                    # Salvar feedback positivo com sugestão opcional
                    with st.spinner("Salvando feedback no B2..."):
                        feedback = {
                            'timestamp': pd.Timestamp.now(),
                            'codigo': dados['codigo'],
                            'nome': dados['nome'],
                            'decisao': dados['decisao'],
                            'achado': st.session_state.get('achado_atual', 'N/A'),
                            'avaliacao': 'correto',
                            'comentario': sugestao if sugestao else '',
                            'corpo_oficio': st.session_state.corpo_oficio
                        }

                        # Baixar feedbacks existentes do B2
                        df_existente = download_feedbacks_from_b2()

                        # Adicionar novo feedback
                        df_novo = pd.DataFrame([feedback])
                        df_atualizado = pd.concat([df_existente, df_novo], ignore_index=True)

                        # Fazer upload para B2
                        if upload_feedbacks_to_b2(df_atualizado):
                            st.success("✅ Obrigado! Feedback registrado com sucesso.")
                            st.session_state.mostrar_sugestao = False
                            st.rerun()
                        else:
                            st.error("❌ Erro ao salvar feedback no B2.")

            with col_env2:
                if st.button("Cancelar", key="cancelar_sugestao", use_container_width=True):
                    st.session_state.mostrar_sugestao = False
                    st.rerun()

        # Campo de comentário (OBRIGATÓRIO - análise incorreta)
        if st.session_state.get('mostrar_comentario', False):
            st.error("❌ **Análise marcada como INCORRETA**")

            comentario = st.text_area(
                "⚠️ O que estava incorreto? (obrigatório)",
                placeholder="Descreva o problema encontrado na análise. Este campo é OBRIGATÓRIO.",
                key="comentario_feedback",
                height=100
            )

            col_env1, col_env2 = st.columns(2)
            with col_env1:
                if st.button("Enviar Feedback", key="enviar_comentario", type="primary", use_container_width=True):
                    # Validar se comentário foi preenchido
                    if not comentario or comentario.strip() == "":
                        st.error("⚠️ Por favor, descreva o que estava incorreto. Este campo é obrigatório.")
                    else:
                        # Salvar feedback negativo com comentário
                        with st.spinner("Salvando feedback no B2..."):
                            feedback = {
                                'timestamp': pd.Timestamp.now(),
                                'codigo': dados['codigo'],
                                'nome': dados['nome'],
                                'decisao': dados['decisao'],
                                'achado': st.session_state.get('achado_atual', 'N/A'),
                                'avaliacao': 'incorreto',
                                'comentario': comentario,
                                'corpo_oficio': st.session_state.corpo_oficio
                            }

                            # Baixar feedbacks existentes do B2
                            df_existente = download_feedbacks_from_b2()

                            # Adicionar novo feedback
                            df_novo = pd.DataFrame([feedback])
                            df_atualizado = pd.concat([df_existente, df_novo], ignore_index=True)

                            # Fazer upload para B2
                            if upload_feedbacks_to_b2(df_atualizado):
                                st.success("✅ Obrigado pelo feedback! Isso nos ajudará a melhorar.")
                                st.session_state.mostrar_comentario = False
                                st.rerun()
                            else:
                                st.error("❌ Erro ao salvar feedback no B2.")

            with col_env2:
                if st.button("Cancelar", key="cancelar_comentario", use_container_width=True):
                    st.session_state.mostrar_comentario = False
                    st.rerun()

# ==============================
# 🔹 Rodapé Institucional DECIPEX
# ==============================
st.markdown("""
<hr style="margin-top:40px; margin-bottom:10px; border: none; border-top: 1px solid #e5e7eb;">
<div style="
    background-color:#f3f4f6;
    color:#4b5563;
    font-family:'Inter', sans-serif;
    font-size:13px;
    text-align:center;
    padding:10px 0;
    border-top:1px solid #e5e7eb;
">
    © 2025 — <strong>DECIPEX / MGI</strong> | Desenvolvido pela <strong>Coordenação-Geral de Risco e Controle (CGRIS)</strong>
</div>
""", unsafe_allow_html=True)