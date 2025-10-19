# 📑 Analisador de Recursos - Filha Maior Solteira

Sistema inteligente para análise automatizada de recursos administrativos de pensão de filha maior solteira, com geração de Notas Técnicas no formato SEI e aprendizado contínuo baseado em feedbacks.

## 🚀 Funcionalidades

### Análise Automatizada
- ✅ Extração de dados de extratos TCU (nome, CPF, código do indício, descrição)
- ✅ Classificação automática de achados usando GPT-4o-mini
- ✅ Análise de argumentos da defesa
- ✅ Decisão baseada em matriz de decisão jurídica
- ✅ Geração de Nota Técnica no formato SEI

### Sistema de Feedbacks Inteligente
- ✅ Avaliação de análises (corretas/incorretas)
- ✅ Armazenamento em nuvem (Backblaze B2)
- ✅ Processamento e análise de padrões
- ✅ Aprendizado automático com exemplos aprovados
- ✅ Insights e recomendações acionáveis

### Interface Moderna
- 📊 10 seções organizadas do fluxo de trabalho
- 📄 Visualização em sidebar (estilo artefato)
- 📋 Botões de cópia em cada seção
- 💾 Download de Notas Técnicas
- 🎨 UX otimizada com cores e validações

## 📋 Pré-requisitos

- Python 3.8+
- Conta OpenAI (API Key)
- Conta Backblaze B2 (para feedbacks)
- Git (para deploy)

## 🔧 Instalação Local

### 1. Clone o repositório

```bash
git clone <url-do-repositorio>
cd recurso_filha_maior_app
```

### 2. Crie ambiente virtual

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Instale dependências

```bash
pip install -r requirements.txt
```

### 4. Configure credenciais

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Edite `.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "sk-proj-SUA_CHAVE_AQUI"

B2_KEY_ID = "SEU_KEY_ID_AQUI"
B2_APPLICATION_KEY = "SUA_APPLICATION_KEY_AQUI"
B2_BUCKET_NAME = "SEU_BUCKET_AQUI"
B2_ENDPOINT = "https://s3.us-east-005.backblazeb2.com"
```

### 5. Execute o app

```bash
streamlit run app.py
```

Acesse: http://localhost:8501

## ☁️ Deploy no Streamlit Cloud

### 1. Prepare o repositório

Certifique-se que o `.gitignore` está protegendo secrets:

```bash
git add .
git commit -m "Preparar para produção"
git push
```

### 2. Deploy no Streamlit Cloud

1. Acesse https://streamlit.io/cloud
2. Clique em "New app"
3. Conecte seu repositório GitHub
4. Configure:
   - **Main file path**: `app.py`
   - **Python version**: 3.9+

### 3. Configure Secrets no Streamlit Cloud

No dashboard do app, vá em **Settings** → **Secrets** e adicione:

```toml
OPENAI_API_KEY = "sk-proj-SUA_CHAVE_AQUI"

B2_KEY_ID = "SEU_KEY_ID_AQUI"
B2_APPLICATION_KEY = "SUA_APPLICATION_KEY_AQUI"
B2_BUCKET_NAME = "SEU_BUCKET_AQUI"
B2_ENDPOINT = "https://s3.us-east-005.backblazeb2.com"
```

### 4. Deploy!

Clique em **Deploy** e aguarde. Seu app estará disponível em:
`https://seu-app.streamlit.app`

## 📁 Estrutura do Projeto

```
recurso_filha_maior_app/
├── app.py                              # Aplicação principal
├── requirements.txt                    # Dependências Python
├── matriz_decisao_revisada_final.csv   # Matriz jurídica de decisão
├── .gitignore                          # Arquivos ignorados pelo Git
├── README.md                           # Este arquivo
├── SISTEMA_FEEDBACKS.md                # Documentação do sistema de feedbacks
│
├── .streamlit/
│   ├── secrets.toml                    # Credenciais (NÃO versionar!)
│   └── secrets.toml.example            # Template de credenciais
│
└── templates/
    ├── oficio_procedente.pdf           # Template ofício procedente
    ├── oficio_improcedente.pdf         # Template ofício improcedente
    └── README.md                       # Documentação dos templates
```

## 🎯 Como Usar

### Fluxo Básico

1. **Upload do Extrato TCU** (PDF)
2. **Upload do Recurso** (PDF da defesa)
3. Sistema extrai automaticamente:
   - Dados da pensionista
   - Descrição do indício
   - Achado do TCU
   - Alegações do recurso
4. **Gerar Corpo do Ofício**
5. **Revisar na sidebar**
6. **Avaliar com feedback** (✅ ou ❌)
7. **Baixar Nota Técnica**

### Processamento de Feedbacks

Periodicamente (ou quando tiver 10+ feedbacks):

1. Clique no botão **"🧠 Processar Feedbacks"** (topo da página)
2. Analise o relatório:
   - Taxa de acerto
   - Padrões de erro
   - Insights e recomendações
3. Sistema aprenderá automaticamente nas próximas gerações

## 🔐 Segurança

### Dados Sensíveis

- ⚠️ **NUNCA** versione `.streamlit/secrets.toml`
- ⚠️ **NUNCA** versione `feedbacks.csv` (contém dados pessoais)
- ✅ Feedbacks são armazenados em bucket B2 privado
- ✅ `.gitignore` protege arquivos sensíveis

### Credenciais Backblaze B2

Para criar credenciais B2:

1. Acesse https://www.backblaze.com/b2/cloud-storage.html
2. Crie uma conta (gratuita até 10GB)
3. Vá em **App Keys** → **Add a New Application Key**
4. Dê permissões: `Read and Write`
5. Copie:
   - **keyID**
   - **applicationKey**
   - **bucketName**
   - **endpoint** (exemplo: `https://s3.us-east-005.backblazeb2.com`)

## 📊 Sistema de Feedbacks

Leia a documentação completa em: [SISTEMA_FEEDBACKS.md](SISTEMA_FEEDBACKS.md)

### Resumo

- Feedback positivo (✅): campo opcional de sugestões
- Feedback negativo (❌): campo obrigatório de problemas
- Processamento analisa padrões e gera insights
- Sistema aprende automaticamente com exemplos aprovados

## 🛠️ Troubleshooting

### Erro: "Module not found"
```bash
pip install -r requirements.txt
```

### Erro: "Secrets not found"
Configure `.streamlit/secrets.toml` com suas credenciais

### Erro: "Erro ao baixar feedbacks do B2"
- Verifique credenciais B2 em `secrets.toml`
- Confirme que bucket existe
- Teste conexão com internet

### App não carrega PDFs
- Verifique se os templates estão em `templates/`
- Confirme nomes: `oficio_procedente.pdf` e `oficio_improcedente.pdf`

## 📝 Matriz de Decisão

O arquivo `matriz_decisao_revisada_final.csv` contém as regras jurídicas:

```csv
achado,argumento,resultado
Apenas CadÚnico,Negativa de união estável,procedente
Filho + endereço,Negativa de união estável,improcedente
...
```

Para modificar regras:
1. Edite o CSV
2. Reinicie o app
3. Teste com casos conhecidos

## 🤝 Contribuindo

1. Crie um branch: `git checkout -b minha-feature`
2. Commit suas mudanças: `git commit -m 'Adiciona feature X'`
3. Push para o branch: `git push origin minha-feature`
4. Abra um Pull Request

## 📄 Licença

Este projeto é de uso interno do órgão.

## 👨‍💻 Suporte

Para dúvidas ou problemas:
- Leia a documentação: [SISTEMA_FEEDBACKS.md](SISTEMA_FEEDBACKS.md)
- Verifique logs do Streamlit
- Contate o administrador do sistema

## 🎉 Changelog

### v2.0.0 - Sistema de Feedbacks
- ✅ Integração com Backblaze B2
- ✅ Sistema de avaliação com botões verde/vermelho
- ✅ Processamento inteligente de feedbacks
- ✅ Aprendizado automático com exemplos aprovados
- ✅ UX melhorada com validações

### v1.0.0 - Versão Inicial
- ✅ Extração de dados TCU
- ✅ Classificação de achados
- ✅ Geração de Notas Técnicas SEI
- ✅ Interface com 10 seções
- ✅ Matriz de decisão jurídica

---

**Desenvolvido com ❤️ para modernizar o trabalho jurídico administrativo**
