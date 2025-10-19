# 🧠 Sistema de Feedbacks e Aprendizado

## Como Funciona

O sistema implementa um ciclo de aprendizado contínuo baseado em feedbacks dos usuários:

### 1. Coleta de Feedbacks ✅❌

Após gerar uma Nota Técnica, o usuário pode avaliar:

- **✅ Análise Correta** (botão VERDE)
  - Abre campo OPCIONAL para sugestões de melhoria
  - Permite enviar sem comentário
  - Salva caso de sucesso para aprendizado

- **❌ Análise Incorreta** (botão VERMELHO)
  - Abre campo OBRIGATÓRIO para descrever o problema
  - Não permite enviar sem explicar o erro
  - Identifica padrões de erro para correção

### 2. Armazenamento no B2 ☁️

Todos os feedbacks são salvos no **Backblaze B2** (bucket: MapaGov):

```
feedbacks.csv
├── timestamp: data/hora do feedback
├── codigo: código do indício
├── nome: nome da pensionista
├── decisao: procedente/improcedente
├── achado: tipo de achado (TCU)
├── avaliacao: correto/incorreto
├── comentario: sugestão ou descrição do erro
└── corpo_oficio: texto completo gerado
```

### 3. Processamento Inteligente 🤖

**Botão "🧠 Processar Feedbacks"** (topo da página):

Quando clicado, o sistema:

1. **Baixa todos os feedbacks** do B2
2. **Separa** feedbacks corretos vs incorretos
3. **Usa GPT-4o-mini** para analisar padrões:
   - O que está funcionando bem?
   - Quais são os erros mais comuns?
   - Como melhorar os prompts?

4. **Gera relatório** com:
   - 📊 Métricas (total, corretos, incorretos, taxa de acerto)
   - 💡 Insights e recomendações
   - ⚠️ Padrões de erro identificados
   - ✅ Exemplos de análises aprovadas

### 4. Aprendizado Automático 🎯

O sistema **aprende automaticamente** ao gerar novas Notas Técnicas:

- Busca no B2 por feedbacks **corretos** do mesmo tipo (achado + decisão)
- Injeta exemplo aprovado no prompt como **referência de qualidade**
- GPT usa o exemplo para manter consistência e qualidade

**Resultado:** Quanto mais feedbacks corretos, melhor o sistema fica!

## Fluxo de Uso Recomendado

### Primeira Semana (Calibração)
1. Gerar 10-20 Notas Técnicas
2. Avaliar TODAS com feedbacks honestos
3. Clicar "Processar Feedbacks" ao final do dia
4. Ler os insights e ajustar expectativas

### Uso Contínuo
1. Gerar Nota Técnica
2. Revisar e avaliar (✅ ou ❌)
3. Se incorreta, descrever claramente o problema
4. Processar feedbacks 1x por semana

### Aprendizado em Ação
- Após 5+ feedbacks corretos do mesmo tipo: sistema começa a usar como referência
- Após 20+ feedbacks: qualidade se estabiliza
- Após 50+ feedbacks: sistema altamente consistente

## Arquitetura Técnica

```
Usuário avalia
     ↓
Feedback salvo localmente (session_state)
     ↓
Download feedbacks.csv do B2
     ↓
Adiciona novo feedback
     ↓
Upload feedbacks.csv atualizado para B2
     ↓
Confirmação ao usuário

[Separadamente]

Usuário clica "Processar Feedbacks"
     ↓
Download feedbacks.csv do B2
     ↓
GPT analisa padrões (corretos vs incorretos)
     ↓
Gera insights e recomendações
     ↓
Exibe relatório visual

[Ao gerar nova Nota]

Sistema busca feedbacks corretos similares
     ↓
Se encontrar, injeta no prompt como exemplo
     ↓
GPT gera texto seguindo padrão aprovado
```

## Benefícios

### Para o Usuário
- ✅ Sistema melhora com o uso
- ✅ Não precisa reescrever prompts manualmente
- ✅ Vê claramente o que está funcionando/falhando
- ✅ Controle total (decide quando processar)

### Para o Sistema
- 🧠 Aprende com casos reais
- 🧠 Mantém consistência de qualidade
- 🧠 Identifica problemas recorrentes
- 🧠 Adapta-se ao estilo preferido

### Para a Organização
- 📈 Base de conhecimento crescente
- 📈 Reduz tempo de revisão
- 📈 Padroniza análises
- 📈 Melhoria contínua documentada

## Exemplos de Insights Gerados

```markdown
### Padrões de Sucesso
- Sistema acerta 85% dos casos de "Apenas CadÚnico"
- Fundamentação jurídica está consistente
- Estilo SEI bem aplicado

### Padrões de Erro
- Confunde "filho em comum" com "endereço em comum" (12% dos erros)
- Às vezes omite análise do argumento de boa-fé
- Citação de acórdãos pode estar desatualizada

### Recomendações
1. Adicionar validação extra para evidências de filho
2. Incluir checklist de argumentos antes de gerar
3. Atualizar prompt com jurisprudência recente
```

## Monitoramento

### Métricas Importantes
- **Taxa de Acerto**: % de feedbacks positivos
  - Meta: >80% após 30 feedbacks

- **Padrões de Erro**: tipos de problemas
  - Meta: <3 tipos recorrentes

- **Exemplos Disponíveis**: casos aprovados por tipo
  - Meta: 3+ exemplos de cada combinação achado+decisão

### Quando Processar
- **Diariamente**: se >10 novos feedbacks/dia
- **Semanalmente**: uso normal (2-5 feedbacks/dia)
- **Sob demanda**: quando notar queda de qualidade

## Segurança e Privacidade

- Feedbacks incluem dados pessoais (nome, CPF)
- Armazenados em bucket privado (Backblaze B2)
- Apenas acessível com credenciais em `secrets.toml`
- **NÃO** versionar `feedbacks.csv` (já está no `.gitignore`)

## Troubleshooting

### "Erro ao baixar feedbacks do B2"
- Verificar credenciais em `.streamlit/secrets.toml`
- Confirmar bucket "MapaGov" existe e está acessível
- Checar conexão com internet

### "Nenhum feedback disponível"
- Normal na primeira execução
- Gere e avalie ao menos 1 caso primeiro

### "Taxa de acerto muito baixa"
- Revisar critérios de avaliação (pode estar muito rígido)
- Processar feedbacks e ler recomendações
- Ajustar expectativas (sistema aprende com o tempo)

## Próximas Melhorias

- [ ] Dashboard de métricas ao longo do tempo
- [ ] Exportar relatório de feedbacks em PDF
- [ ] Sugestões automáticas de ajuste de prompt
- [ ] Alertas quando taxa de erro sobe
- [ ] Comparação antes/depois do aprendizado
