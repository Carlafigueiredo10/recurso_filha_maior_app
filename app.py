import pdfplumber
import pandas as pd
import streamlit as st
import json
from pathlib import Path
from openai import OpenAI
import boto3
from io import StringIO, BytesIO

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
    "11": "Inconsistência no CadÚnico"
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
- "Filho + endereço": quando menciona filho em comum + endereço em comum (bases como TSE, Receita Federal)
- "Filho + CadÚnico": quando menciona filho em comum + declaração no CadÚnico
- "Mais de 1 filho": quando menciona 2 ou mais filhos em comum
- "Endereço em múltiplas bases (TSE/Receita)": quando menciona endereço em comum em bases como TSE ou Receita Federal (sem filho)
- "Pensão do INSS como companheira": quando menciona recebimento de pensão por morte do companheiro no INSS
- "Achado não classificado": quando não se encaixa em nenhuma das categorias acima

⚠️ ATENÇÃO: Classifique baseado APENAS no que está escrito nas evidências, não faça suposições

Escolha um dos seguintes rótulos:

2. Identifique quais argumentos da defesa correspondem aos seguintes códigos e descrições:
{ARG_MAP}

⚠️ Importante: trate como equivalente a **Argumento 2** qualquer menção a:
- "filho em comum"
- "descendência em comum"
- "filha em comum"
- "descendência conjunta"

3. Se existirem argumentos adicionais que não se enquadram nos 11 códigos, liste-os em "outros".

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

# --------- Aplicar matriz ---------
def analisar_com_matriz(achado, argumentos):
    improc, proc = [], []

    # Se não há argumentos, buscar regra "Nenhum argumento apresentado"
    if not argumentos or len(argumentos) == 0:
        regra = matriz[(matriz["achado"] == achado) & (matriz["argumento"] == "Nenhum argumento apresentado")]
        if not regra.empty:
            res = regra["resultado"].iloc[0]
            saida1 = res
            saida2 = f"Decisão baseada em: {achado} + Nenhum argumento apresentado = {res}"
            return saida1, saida2

    # Se há argumentos, processar normalmente
    for num in argumentos:
        arg_texto = ARG_MAP.get(num, num)
        regra = matriz[(matriz["achado"] == achado) & (matriz["argumento"] == arg_texto)]
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

    saida2 = f"improcedente argumentos ({', '.join(improc)})\nprocedente argumentos ({', '.join(proc)})"
    return saida1, saida2

# --------- Gerar corpo do ofício ---------
def gerar_corpo_oficio(decisao, achado, argumentos, outros, alegacoes, texto_defesa_previa, dados_identificacao, descricao_indicio):
    """Gera o corpo do ofício focado na análise dos argumentos (item 15 improcedente ou item 13 procedente)."""

    # Preparar lista de argumentos apresentados
    args_lista = "\n".join([f"- Argumento {num}: {ARG_MAP.get(num, 'Não identificado')}" for num in argumentos])
    outros_lista = "\n".join([f"- {o}" for o in outros]) if outros else "Nenhum argumento adicional"

    # Dados da pensionista
    nome = dados_identificacao.get("nome", "[NOME NÃO IDENTIFICADO]")
    cpf = dados_identificacao.get("cpf", "[CPF NÃO IDENTIFICADO]")
    codigo = dados_identificacao.get("codigo_indicio", "[CÓDIGO NÃO IDENTIFICADO]")

    # Carregar template do ofício como referência (APENAS para procedente)
    template_texto = ""
    if decisao == "procedente":
        try:
            item13_conteudo = extrair_item_template("procedente")
            if item13_conteudo:
                template_texto = f"""

### 📄 ITEM 13 DO TEMPLATE (PROCEDENTE) - SIGA ESTE MODELO
O item 13 do template de ofício procedente mostra COMO escrever quando o recurso é PROCEDENTE:

{item13_conteudo[:2000]}

⚠️ **IMPORTANTE**: Use este item 13 como GUIA de estilo, tom, estrutura e argumentação.
- Observe COMO ele justifica que os argumentos AFASTAM o achado do TCU
- Observe COMO ele conclui pela MANUTENÇÃO do benefício
- Adapte o conteúdo ao caso específico atual.
"""
        except:
            pass  # Se não conseguir carregar template, continua sem

    # Tentar carregar feedbacks para aprendizado adicional
    exemplos_aprendizado = ""
    try:
        df_feedbacks = download_feedbacks_from_b2()
        if not df_feedbacks.empty:
            # Filtrar exemplos corretos com mesmo tipo de achado e decisão
            corretos_similares = df_feedbacks[
                (df_feedbacks['avaliacao'] == 'correto') &
                (df_feedbacks['achado'] == achado) &
                (df_feedbacks['decisao'] == decisao)
            ]

            if len(corretos_similares) > 0:
                exemplo = corretos_similares.iloc[0]
                exemplos_aprendizado = f"""

### ✅ EXEMPLO DE ANÁLISE APROVADA (mesmo tipo de caso)
**Achado:** {exemplo['achado']}
**Decisão:** {exemplo['decisao']}
**Texto aprovado:**
{exemplo['corpo_oficio'][:800]}

⚠️ Use este exemplo como REFERÊNCIA adicional de qualidade.
"""
    except:
        pass  # Se não conseguir carregar feedbacks, continua sem exemplos

    prompt = f"""
Você é um especialista em redação de Notas Técnicas no formato SEI para análise de recursos de pensão de filha maior solteira.

### DADOS DO CASO
**Pensionista:** {nome}
**CPF:** {cpf}
**Código do Indício:** {codigo}

**Descrição do Indício (TCU):**
{descricao_indicio}

**Achado classificado:** {achado}

**Decisão:** {decisao.upper()}

### ALEGAÇÕES DO RECURSO
{alegacoes}

### ARGUMENTOS MAPEADOS
{args_lista}

### ARGUMENTOS NÃO MAPEADOS
{outros_lista}
{template_texto}
{exemplos_aprendizado}

### TAREFA
Gere a Nota Técnica completa no formato SEI, conforme {'item 15' if decisao == 'improcedente' else 'item 13'} do modelo de ofício.

**Estrutura da Nota Técnica SEI:**

1. Parágrafo introdutório: "Dos argumentos apresentados no recurso pela Interessada, segue análise:"

2-N. Para CADA argumento/alegação, escrever UM parágrafo no seguinte estilo:
   **Formato:** "A Interessada alega que [resumo do argumento]. Análise: [fundamentação jurídica]. Conclusão: [conclusão sobre o argumento]."

   **{'EXEMPLO - RECURSO IMPROCEDENTE' if decisao == 'improcedente' else 'EXEMPLO - RECURSO PROCEDENTE'}:**
   {'A Interessada alega que nunca manteve união estável com o suposto companheiro. Análise: A condição de solteira no registro civil é um dos elementos que compõem a análise da situação de dependência econômica e familiar. A legislação pertinente, especialmente a Lei 3.373/1958, estabelece que a dependência econômica deve ser analisada em conjunto com outros fatores, como a convivência e a constituição de família. O art. 1.723 do Código Civil define união estável como convivência pública, contínua e duradoura, estabelecida com o objetivo de constituição de família. Conclusão: A mera condição de solteira não impede a configuração de união estável, pois é a existência de filho em comum que indica os deveres de sustento e educação dos filhos no âmbito da união estável, não sendo suficiente para afastar o achado do TCU.' if decisao == 'improcedente' else 'A Interessada alega que nunca manteve união estável com o suposto companheiro, apresentando certidão de casamento com terceiro no período. Análise: A Lei 3.373/1958 estabelece que a pensão é devida apenas à filha maior solteira sem meios de prover a própria subsistência, sendo incompatível com a condição de casada ou em união estável. A apresentação de certidão de casamento com terceiro contemporânea ao período analisado demonstra a impossibilidade de configuração de união estável simultânea. Conclusão: As provas documentais apresentadas comprovam que a Interessada não mantinha união estável, afastando o achado do TCU e demonstrando o direito à manutenção do benefício.'}

N+1. Parágrafo final de conclusão: Fundamentar a decisão final ({decisao.upper()}) com base na análise dos argumentos

**REGRAS IMPORTANTES:**
- Use SEMPRE linguagem de Nota Técnica SEI: "A Interessada alega que...", "Conforme disposto...", "Assim..."
- Numere os parágrafos (1., 2., 3., etc.)
- Mantenha tom jurídico, técnico e objetivo
- Cite legislação pertinente (Lei 3.373/1958, Acórdãos do TCU 7972/2017-2ªC, Código Civil)
- **{'IMPROCEDENTE: Explique que as alegações NÃO são suficientes para AFASTAR o achado do TCU. A pensionista PERDE o benefício.' if decisao == 'improcedente' else 'PROCEDENTE: Explique que as alegações COMPROVAM a INEXISTÊNCIA de união estável. A pensionista MANTÉM o benefício.'}**
- Seja completo e fundamentado
- NÃO inclua cabeçalhos de seção, rodapés ou assinaturas
"""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return resp.choices[0].message.content

# ------------------ INTERFACE ------------------

st.title("📑 Analisador de Recursos - Filha Maior Solteira")

# Aviso se B2 não estiver configurado
if not B2_CONFIGURED:
    st.info("""
    ℹ️ **Sistema de Feedbacks Desabilitado**

    Para habilitar o sistema de aprendizado com feedbacks, configure as credenciais do Backblaze B2 nos Secrets:
    - `B2_ENDPOINT` (exemplo: https://s3.us-east-005.backblazeb2.com)
    - `B2_KEY_ID` (use o S3 Access Key ID, não Application Key ID)
    - `B2_APPLICATION_KEY` (use o S3 Secret Key)
    - `B2_BUCKET_NAME` (nome do seu bucket)

    **Importante:** Use as credenciais S3-compatible, não as credenciais nativas do B2!
    """)

# Botão de processar feedbacks no topo
col_titulo, col_feedback_btn = st.columns([3, 1])
with col_feedback_btn:
    if st.button("🧠 Processar Feedbacks", help="Analisa feedbacks do B2 e gera insights de aprendizado", type="secondary", disabled=not B2_CONFIGURED):
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

st.divider()

# 1. Upload do PDF do Extrato (TCU)
st.header("1️⃣ Upload do PDF do Extrato (TCU)")
extrato_file = st.file_uploader("Selecione o arquivo PDF do extrato do TCU", type=["pdf"], key="extrato")

# 2. Upload do PDF do Recurso
st.header("2️⃣ Upload do PDF do Recurso apresentado pela pensionista")
defesa_file = st.file_uploader("Selecione o arquivo PDF do recurso", type=["pdf"], key="recurso")

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
    st.header("3️⃣ Dados da Pensionista")
    st.caption("Informações extraídas do extrato TCU")

    nome = dados_identificacao.get("nome", "Não identificado")
    cpf = dados_identificacao.get("cpf", "Não identificado")
    codigo = dados_identificacao.get("codigo_indicio", "Não identificado")

    dados_completos = f"Nome: {nome}\nCPF: {cpf}\nCódigo do Indício: {codigo}"

    # Adicionar CSS para texto mais escuro e auto-height
    st.markdown("""
    <style>
    .stTextArea textarea {
        color: #1f1f1f !important;
        font-weight: 500 !important;
    }
    div[data-testid="stText"] {
        color: #1f1f1f !important;
        font-weight: 500 !important;
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 5px;
        font-family: monospace;
        white-space: pre-wrap;
        line-height: 1.8;
    }
    </style>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([10, 1])
    with col1:
        st.markdown(f"""
        <div style="background-color: #f0f2f6; padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; font-family: monospace; line-height: 1.8;">
        {dados_completos}
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.write("")  # Espaço
        if st.button("📋", key="copy_dados", help="Copiar dados"):
            st.code(dados_completos, language=None)

    st.divider()

    # 4. Descrição do Indício (TCU)
    descricao_indicio = dados_identificacao.get("descricao_indicio", None)
    st.header("4️⃣ Descrição do Indício (TCU)")
    st.caption("Evidências específicas do caso extraídas do extrato")

    if descricao_indicio:
        col1, col2 = st.columns([10, 1])
        with col1:
            st.markdown(f"""
            <div style="background-color: #f0f2f6; padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; font-family: monospace; line-height: 1.8; max-height: none;">
            {descricao_indicio}
            </div>
            """, unsafe_allow_html=True)
        with col2:
            st.write("")  # Espaço
            if st.button("📋", key="copy_descricao", help="Copiar descrição"):
                st.code(descricao_indicio, language=None)
    else:
        st.warning("⚠️ Descrição do indício não foi identificada no extrato.")

    # 5. Achado TCU (classificação baseada APENAS no extrato)
    st.header("5️⃣ Achado TCU")
    st.caption("Classificação das provas de união estável identificadas pelo TCU no extrato")

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

    st.info(f"**Achado classificado:** {achado}")

    st.divider()

    # 6. Recurso apresentado (alegações da pensionista)
    st.header("6️⃣ Recurso apresentado")
    st.caption("Alegações/argumentos extraídos do recurso da pensionista")

    with st.spinner("📝 Extraindo alegações do recurso..."):
        alegacoes_recurso = extrair_alegacoes_recurso(texto_defesa)

    col1, col2 = st.columns([10, 1])
    with col1:
        st.markdown(f"""
        <div style="background-color: #f0f2f6; padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; font-family: monospace; line-height: 1.8; white-space: pre-wrap;">
        {alegacoes_recurso}
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.write("")  # Espaço
        if st.button("📋", key="copy_alegacoes", help="Copiar alegações"):
            st.code(alegacoes_recurso, language=None)

    # 7. Pergunta sobre defesa prévia
    st.header("7️⃣ Defesa Prévia")
    st.caption("A pensionista havia apresentado defesa anteriormente?")

    defesa_previa = st.radio(
        "Selecione:",
        ["Sim", "Não"],
        key="defesa_previa",
        horizontal=True
    )

    if defesa_previa == "Sim":
        texto_defesa_previa = """A Interessada foi devidamente notificada para apresentar defesa em observância aos princípios do contraditório e ampla defesa. Tendo sua defesa sido analisada e julgada na decisão administrativa anterior. Inconformada, a Interessada apresentou recurso tempestivo, o qual passa a ser objeto da presente Nota Técnica."""
    else:
        texto_defesa_previa = """A Interessada foi devidamente notificada para apresentar defesa em observância aos princípios do contraditório e ampla defesa. Todavia registrou-se a ausência de defesa, razão pela qual a decisão administrativa anterior foi proferida com base nos elementos constantes dos autos. Ainda assim, a Interessada apresentou recurso tempestivo, que agora se examina na presente Nota Técnica."""

    col1, col2 = st.columns([10, 1])
    with col1:
        st.markdown(f"""
        <div style="background-color: #f0f2f6; padding: 15px; border-radius: 5px; color: #1f1f1f; font-weight: 500; line-height: 1.8;">
        {texto_defesa_previa}
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.write("")  # Espaço
        if st.button("📋", key="copy_defesa_previa", help="Copiar texto"):
            st.code(texto_defesa_previa, language=None)

    st.divider()

    # 8. Decisão
    st.header("8️⃣ Decisão")
    st.caption("Decisão calculada com base na matriz de decisão")

    s1, s2 = analisar_com_matriz(achado, argumentos)

    if s1 == "procedente":
        st.success(f"✅ **Recurso PROCEDENTE**")
    else:
        st.error(f"❌ **Recurso IMPROCEDENTE**")

    st.text(s2)

    st.divider()

    # 9. Pontos de Atenção
    st.header("9️⃣ Pontos de Atenção")
    st.caption("Argumentos não mapeados ou que requerem atenção especial")

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

    st.divider()

    # 10. Corpo do Ofício
    st.header("🔟 Corpo do Ofício")
    st.caption(f"Minuta baseada no template {'improcedente (item 15)' if s1 == 'improcedente' else 'procedente (item 13)'}")

    if st.button("🚀 Gerar Corpo do Ofício", type="primary", key="gerar_oficio"):
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
    if 'corpo_oficio' in st.session_state:
        dados = st.session_state.dados_oficio
        st.download_button(
            label="📥 Baixar Ofício (.txt)",
            data=st.session_state.corpo_oficio,
            file_name=f"oficio_{dados['decisao']}_{dados['codigo']}_{dados['nome'][:30].replace(' ', '_')}.txt",
            mime="text/plain",
            key="download_oficio"
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

        # Corpo do ofício com formatação melhorada
        st.markdown(f"""
        <div style="
            color: #1f1f1f;
            line-height: 1.8;
            font-size: 14px;
            text-align: justify;
            padding: 10px;
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