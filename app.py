import pdfplumber
import pandas as pd
import streamlit as st
import json
from openai import OpenAI

# carregar chave da API do secrets (Streamlit Cloud)
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

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
matriz = pd.read_csv("matriz_decisao.csv")

def extrair_texto(pdf_file):
    texto = ""
    with pdfplumber.open(pdf_file) as pdf:
        for p in pdf.pages:
            if p.extract_text():
                texto += p.extract_text() + "\n"
    return texto

# --------- GPT para JSON técnico ---------
def classificar_com_gpt(texto_extrato, texto_defesa):
    prompt = f"""
Você é um sistema de apoio jurídico que analisa recursos administrativos de pensão de filha maior solteira.

### Bloco 1 — Achado do TCU (texto do extrato)
{texto_extrato}

### Bloco 2 — Defesa apresentada pela interessada
{texto_defesa}

### Tarefa
1. Classifique o achado do TCU em um dos seguintes rótulos:
- "Apenas 1 filho"
- "Apenas CadÚnico"
- "Filho + endereço"
- "Filho + CadÚnico"
- "Mais de 1 filho"
- "Endereço em múltiplas bases (TSE/Receita)"
- "Pensão do INSS como companheira"
- "Achado não classificado"

2. Identifique quais argumentos da defesa correspondem aos seguintes códigos e descrições:
{ARG_MAP}

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

# ------------------ INTERFACE ------------------

st.title("📑 Analisador de Recursos - Filha Maior Solteira (com GPT)")

extrato_file = st.file_uploader("Upload do PDF do Extrato (TCU)", type=["pdf"])
defesa_file = st.file_uploader("Upload do PDF da Defesa", type=["pdf"])

if extrato_file and defesa_file:
    texto_extrato = extrair_texto(extrato_file)
    texto_defesa = extrair_texto(defesa_file)

    # --- 1. JSON técnico ---
    st.info("🔎 Chamando GPT para classificar (JSON técnico)...")
    saida_gpt = classificar_com_gpt(texto_extrato, texto_defesa)

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

    s1, s2 = analisar_com_matriz(achado, argumentos)

    st.subheader("✅ Resultado técnico")
    st.markdown(f"**Achado:** {achado}")
    st.markdown(f"**Saída 1:** {s1}")
    st.text(s2)

    st.subheader("📚 Argumentos detectados (numéricos)")
    if argumentos:
        for a in argumentos:
            st.write(f"{a} — {ARG_MAP[a]}")
    else:
        st.warning("Nenhum argumento numerado detectado.")
    if outros:
        st.info(f"⚠️ Outros argumentos não mapeados: {', '.join(outros)}")

    # --- 2. Narrativa formatada ---
    st.info("📝 Chamando GPT para gerar narrativa formatada...")
    saida_formatada = extrair_argumentos_formatado(texto_defesa)

    st.subheader("📑 Recurso apresentado (trechos relevantes)")
    st.write(saida_formatada)