# Templates de Ofícios

Esta pasta deve conter os modelos de ofícios em PDF que serão utilizados como base para geração automática dos ofícios de resposta.

## Arquivos necessários:

1. **oficio_procedente.pdf** - Template para casos onde o recurso é PROCEDENTE (acolhido)
2. **oficio_improcedente.pdf** - Template para casos onde o recurso é IMPROCEDENTE (não acolhido)

## Como usar:

1. Salve seus PDFs de modelo nesta pasta com os nomes exatos:
   - `oficio_procedente.pdf`
   - `oficio_improcedente.pdf`

2. O sistema irá extrair o texto desses PDFs e usar como base para gerar ofícios personalizados

3. Se os arquivos não forem encontrados, o sistema ainda funcionará, mas gerará ofícios sem um template base

## Estrutura recomendada dos PDFs:

Os templates devem conter a estrutura padrão do ofício:
- Cabeçalho institucional (se aplicável)
- Estrutura formal de apresentação do caso
- Linguagem jurídica e técnica apropriada
- Formato de fundamentação da decisão
- Conclusão padrão

O GPT irá adaptar o template ao caso específico, incluindo:
- Achado do TCU identificado
- Argumentos apresentados pela interessada
- Fundamentação específica para cada argumento
- Decisão final (procedente/improcedente)
