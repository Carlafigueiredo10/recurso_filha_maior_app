import pdfplumber
import pandas as pd
import streamlit as st
import json
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
    "6": "Coisa julgada judicial",
    "7": "Dissolução da união estável",
    "8": "Ameaça de judicialização",
    "9": "Recebimento de pensão do INSS não descaracteriza",
    "10": "Testemunhos de terceiros",
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

4. **Descrição do Indício**: Extraia SOMENTE a parte específica do caso no campo "Descrição"
   ⚠️ ATENÇÃO: Extraia apenas desde "Pensionista filha maior..." até ANTES da palavra "Critério:"
   - NÃO inclua a parte que começa com "Critério: A Lei 3373/1958..."
   - NÃO inclua jurisprudência, acórdãos ou fundamentação legal
   - Inclua APENAS: o texto inicial + "Evidências do indício:" + as evidências específicas do caso
   - PARE quando encontrar a palavra "Critério:"

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
- Descrição: extraia desde "Pensionista filha maior..." até ANTES de "Critério:" (NÃO inclua a fundamentação legal)
- A descrição deve conter APENAS as evidências específicas do caso
- PARE a extração quando encontrar a palavra "Critério:"
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
{ARG_MAP}

⚠️ IMPORTANTE - Diferenciar CONFISSÃO vs NEGAÇÃO de filho:
- **Argumento 2** ("Filho em comum não caracteriza"): quando a defesa ADMITE que existe filho, mas NEGA que isso caracteriza união estável
- **Argumento 12** ("Defesa admite filho em comum"): quando a defesa simplesmente CONFIRMA/ADMITE ter filho SEM negar a união estável

⚠️ Importante: trate como **Argumento 2** quando mencionar:
- "filho em comum não significa união estável"
- "mera existência de filho não caracteriza"
- "filho não comprova união"

⚠️ Importante: trate como **Argumento 12** quando:
- Defesa confirma/admite filho SEM contestar união estável
- Menciona filho mas não argumenta que isso é irrelevante

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

    # decisão judicial (6) prevalece
    if "6" in argumentos:
        saida1 = "procedente"
    else:
        saida1 = "improcedente" if len(improc) >= len(proc) else "procedente"

    # Mensagem mostra se achado foi recalculado
    info_recalculo = f"\n⚠️ Achado recalculado: {achado} → {achado_recalculado} (defesa revelou mais provas)" if achado != achado_recalculado else ""
    saida2 = f"improcedente argumentos ({', '.join(improc)})\nprocedente argumentos ({', '.join(proc)}){info_recalculo}"
    return saida1, saida2

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

# CSS Customizado - Tema Cyberpunk
st.markdown("""
<style>
/* Importar fonte futurista */
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&display=swap');

/* Background escuro */
.stApp {
    background: linear-gradient(135deg, #0a0e27 0%, #1a1d2e 100%);
}

/* Título principal com robô */
h1 {
    font-family: 'Orbitron', sans-serif !important;
    color: #00d9ff !important;
    text-shadow: 0 0 8px rgba(0, 217, 255, 0.5);
    letter-spacing: 3px;
    font-weight: 900 !important;
    margin-bottom: 30px !important;
}

/* Headers das seções */
h2, h3 {
    font-family: 'Orbitron', sans-serif !important;
    color: #00d9ff !important;
    text-shadow: 0 0 3px rgba(0, 217, 255, 0.3);
    letter-spacing: 2px;
}

/* Logo do robô */
.logo-robo {
    width: 80px;
    height: 80px;
    filter: drop-shadow(0 0 10px rgba(0, 217, 255, 0.4));
    margin-right: 20px;
    vertical-align: middle;
}

/* Cards das seções */
.element-container {
    background: rgba(26, 29, 36, 0.6);
    border: 1px solid rgba(0, 217, 255, 0.3);
    border-radius: 10px;
    padding: 15px;
    margin: 10px 0;
    box-shadow: 0 0 15px rgba(0, 217, 255, 0.1);
}

/* Botões */
.stButton button {
    background: linear-gradient(135deg, #00d9ff 0%, #0066cc 100%) !important;
    color: #0a0e27 !important;
    font-family: 'Orbitron', sans-serif !important;
    font-weight: 700 !important;
    border: none !important;
    border-radius: 25px !important;
    padding: 10px 30px !important;
    box-shadow: 0 0 20px rgba(0, 217, 255, 0.5) !important;
    transition: all 0.3s ease !important;
    letter-spacing: 1px !important;
}

.stButton button:hover {
    box-shadow: 0 0 30px rgba(0, 217, 255, 0.8) !important;
    transform: translateY(-2px) !important;
}

/* Botões do topo - altura reduzida */
.stButton button[kind="secondary"] {
    padding: 6px 20px !important;
    font-size: 14px !important;
    height: 35px !important;
}

/* Inputs */
.stTextInput input, .stTextArea textarea {
    background: rgba(26, 29, 36, 0.8) !important;
    border: 1px solid #00d9ff !important;
    color: #ffffff !important;
    border-radius: 10px !important;
}

/* Info boxes */
.stInfo {
    background: rgba(0, 217, 255, 0.1) !important;
    border-left: 4px solid #00d9ff !important;
}

/* Success boxes */
.stSuccess {
    background: rgba(0, 255, 136, 0.1) !important;
    border-left: 4px solid #00ff88 !important;
}

/* Error boxes */
.stError {
    background: rgba(255, 68, 68, 0.1) !important;
    border-left: 4px solid #ff4444 !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0a0e27 0%, #1a1d2e 100%);
    border-right: 2px solid rgba(0, 217, 255, 0.3);
}

/* Divider */
hr {
    border-color: rgba(0, 217, 255, 0.3) !important;
}

/* Números das seções com glow */
h2::before {
    content: '▸ ';
    color: #00d9ff;
    text-shadow: 0 0 10px #00d9ff;
}

/* Animação pulsante do logo */
.logo-robo-pulse {
    animation: pulse 2s infinite alternate;
}

@keyframes pulse {
    from { filter: drop-shadow(0 0 5px rgba(0, 217, 255, 0.4)); }
    to { filter: drop-shadow(0 0 25px rgba(0, 217, 255, 0.9)); }
}
</style>
""", unsafe_allow_html=True)

# Título com logo do robô
logo_base64 = get_logo_base64()

if logo_base64:
    st.markdown(f"""
    <div style="display: flex; align-items: center; margin-bottom: 30px; gap: 20px;">
        <img class="logo-robo-pulse" src="data:image/png;base64,{logo_base64}" style="width: 180px;">
        <div>
            <h1 style="margin: 0; padding: 0; font-size: 3.5em; line-height: 1;">ANALISADOR DE RECURSOS</h1>
            <p style="color: #00d9ff; font-family: 'Orbitron', sans-serif; font-size: 18px; margin: 10px 0 0 0; padding: 0; letter-spacing: 3px;">
                Filha Maior Solteira
            </p>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <h1 style="margin: 0; padding: 0;">🤖 ANALISADOR DE RECURSOS</h1>
    <p style="color: #00d9ff; font-family: 'Orbitron', sans-serif; font-size: 18px; letter-spacing: 3px;">
        Filha Maior Solteira
    </p>
    """, unsafe_allow_html=True)

# Botões logo abaixo do título, ocupando largura total
col_btn1, col_btn2 = st.columns(2)

with col_btn1:
    if st.button("🧠 Processar Feedbacks", help="Analisa feedbacks do B2 e gera insights", type="secondary", disabled=not B2_CONFIGURED, use_container_width=True):
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

with col_btn2:
    if st.button("🔄 Reiniciar", help="Limpar tudo e recomeçar", type="secondary", use_container_width=True):
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
    st.markdown("### 3️⃣ Dados da Pensionista")

    nome = dados_identificacao.get("nome", "Não identificado")
    cpf = dados_identificacao.get("cpf", "Não identificado")
    codigo = dados_identificacao.get("codigo_indicio", "Não identificado")

    # Layout em 3 colunas para dados compactos
    col_nome, col_cpf, col_codigo = st.columns(3)
    with col_nome:
        st.markdown(f"""
        <div style="background: rgba(0, 217, 255, 0.1); padding: 10px; border-radius: 5px; border-left: 3px solid #00d9ff;">
            <div style="color: #00d9ff; font-size: 12px; font-family: 'Orbitron', sans-serif;">NOME</div>
            <div style="color: #ffffff; font-size: 14px; margin-top: 5px;">{nome}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_cpf:
        st.markdown(f"""
        <div style="background: rgba(0, 217, 255, 0.1); padding: 10px; border-radius: 5px; border-left: 3px solid #00d9ff;">
            <div style="color: #00d9ff; font-size: 12px; font-family: 'Orbitron', sans-serif;">CPF</div>
            <div style="color: #ffffff; font-size: 14px; margin-top: 5px;">{cpf}</div>
        </div>
        """, unsafe_allow_html=True)

    with col_codigo:
        st.markdown(f"""
        <div style="background: rgba(0, 217, 255, 0.1); padding: 10px; border-radius: 5px; border-left: 3px solid #00d9ff;">
            <div style="color: #00d9ff; font-size: 12px; font-family: 'Orbitron', sans-serif;">CÓDIGO INDÍCIO</div>
            <div style="color: #ffffff; font-size: 14px; margin-top: 5px;">{codigo}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 4. Descrição do Indício (TCU)
    descricao_indicio = dados_identificacao.get("descricao_indicio", None)
    st.markdown("### 4️⃣ Descrição do Indício (TCU)")

    if descricao_indicio:
        st.markdown(f"""
        <div style="background: rgba(255, 255, 255, 0.95); padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; line-height: 1.8; border: 1px solid rgba(0, 217, 255, 0.3);">
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

    # Salvar achado no session_state para usar no feedback
    st.session_state.achado_atual = achado

    st.markdown(f"""
    <div style="background: rgba(0, 217, 255, 0.15); padding: 15px; border-radius: 5px; border-left: 4px solid #00d9ff;">
        <span style="color: #00d9ff; font-family: 'Orbitron', sans-serif; font-weight: 600;">📊 Achado classificado:</span>
        <span style="color: #ffffff; font-size: 16px; margin-left: 10px;">{achado}</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 6. Recurso apresentado (alegações da pensionista)
    st.markdown("### 6️⃣ Recurso apresentado")

    with st.spinner("📝 Extraindo alegações do recurso..."):
        alegacoes_recurso = extrair_alegacoes_recurso(texto_defesa)

    st.markdown(f"""
    <div style="background: rgba(255, 255, 255, 0.95); padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; line-height: 1.8; white-space: pre-wrap; border: 1px solid rgba(0, 217, 255, 0.3);">
    {alegacoes_recurso}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 7. Pergunta sobre defesa prévia
    st.markdown("### 7️⃣ Defesa Prévia")

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

    st.markdown(f"""
    <div style="background: rgba(255, 255, 255, 0.95); padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; line-height: 1.8; border: 1px solid rgba(0, 217, 255, 0.3);">
    {texto_defesa_previa}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 8. Decisão
    st.markdown("### 8️⃣ Decisão")

    s1, s2 = analisar_com_matriz(achado, argumentos)

    if s1 == "procedente":
        st.markdown(f"""
        <div style="background: rgba(34, 197, 94, 0.2); padding: 20px; border-radius: 8px; border-left: 5px solid #22c55e;">
            <div style="color: #22c55e; font-size: 24px; font-family: 'Orbitron', sans-serif; font-weight: 700;">✅ RECURSO PROCEDENTE</div>
            <div style="color: #ffffff; margin-top: 10px; font-size: 14px;">{s2}</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background: rgba(239, 68, 68, 0.2); padding: 20px; border-radius: 8px; border-left: 5px solid #ef4444;">
            <div style="color: #ef4444; font-size: 24px; font-family: 'Orbitron', sans-serif; font-weight: 700;">❌ RECURSO IMPROCEDENTE</div>
            <div style="color: #ffffff; margin-top: 10px; font-size: 14px;">{s2}</div>
        </div>
        """, unsafe_allow_html=True)

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
                # Gerar ofício com análise dos argumentos
                st.session_state.corpo_oficio = gerar_corpo_oficio(
                    decisao=s1,
                    achado=achado,
                    argumentos=argumentos,
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