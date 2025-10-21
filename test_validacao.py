"""
Teste de validação dos Argumentos 6 e 9
Verifica se os filtros pós-GPT estão funcionando corretamente
"""

import re
import sys
import io

# Configurar encoding UTF-8 para Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def validar_argumento_6(texto_defesa):
    """Valida se o Argumento 6 (decisão judicial) deve ser mantido."""
    # Verifica se há número de processo no formato CNJ ou menção a "transitado em julgado"
    tem_numero_processo = bool(re.search(r'\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}', texto_defesa))
    tem_transito = bool(re.search(r'trânsit|transitad', texto_defesa, re.IGNORECASE))
    tem_processo_especifico = re.search(r'(processo|autos)\s+(n[º°]|número)', texto_defesa, re.IGNORECASE)

    # Se não tem número de processo E não menciona trânsito em julgado E não menciona processo específico
    if not tem_numero_processo and not tem_transito and not tem_processo_especifico:
        return False

    # Filtro adicional: se menciona "jurisprudência" sem número de processo, provavelmente é falso-positivo
    tem_jurisprudencia = re.search(r'(jurisprudência|precedente|súmula|entendimento\s+dos?\s+tribunal)', texto_defesa, re.IGNORECASE)
    if tem_jurisprudencia and not tem_numero_processo:
        return False

    return True

def validar_argumento_9(texto_defesa):
    """Valida se o Argumento 9 (processo administrativo anterior) deve ser mantido."""
    # Garante que há termos administrativos explícitos
    tem_termos_admin = bool(re.search(
        r'(NUP|processo\s+administrativo|Nota\s+Técnica|PAD|já\s+foi\s+(analisado|avaliado|auditado|julgado)|decisão\s+administrativa\s+anterior)',
        texto_defesa,
        re.IGNORECASE
    ))

    return tem_termos_admin

# ========== TESTES ARGUMENTO 6 ==========

print("=" * 80)
print("TESTES ARGUMENTO 6 (Decisão Judicial)")
print("=" * 80)

# ❌ TESTE 1: Deve REJEITAR - apenas jurisprudência genérica
texto1 = "O TRF4 já decidiu que união estável não descaracteriza filha solteira"
resultado1 = validar_argumento_6(texto1)
print(f"\nTESTE 1 (jurisprudência genérica): {'❌ REJEITOU' if not resultado1 else '✅ ACEITOU'} (esperado: REJEITAR)")
print(f"Texto: {texto1}")

# ❌ TESTE 2: Deve REJEITAR - entendimento dos tribunais
texto2 = "Segundo entendimento do STF, a jurisprudência é favorável"
resultado2 = validar_argumento_6(texto2)
print(f"\nTESTE 2 (entendimento tribunais): {'❌ REJEITOU' if not resultado2 else '✅ ACEITOU'} (esperado: REJEITAR)")
print(f"Texto: {texto2}")

# ❌ TESTE 3: Deve REJEITAR - decisões genéricas sobre o tema
texto3 = "Há decisões judiciais favoráveis sobre o tema filha maior solteira"
resultado3 = validar_argumento_6(texto3)
print(f"\nTESTE 3 (decisões genéricas): {'❌ REJEITOU' if not resultado3 else '✅ ACEITOU'} (esperado: REJEITAR)")
print(f"Texto: {texto3}")

# ✅ TESTE 4: Deve ACEITAR - decisão com número de processo
texto4 = "A manutenção da pensão é respaldada por decisão judicial transitada em julgado no processo 1234567-89.2020.4.04.1234"
resultado4 = validar_argumento_6(texto4)
print(f"\nTESTE 4 (com número de processo): {'✅ ACEITOU' if resultado4 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto4}")

# ✅ TESTE 5: Deve ACEITAR - sentença transitada em julgado
texto5 = "Existe sentença favorável à interessada com trânsito em julgado"
resultado5 = validar_argumento_6(texto5)
print(f"\nTESTE 5 (transitado em julgado): {'✅ ACEITOU' if resultado5 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto5}")

# ✅ TESTE 6: Deve ACEITAR - processo nº específico
texto6 = "Decisão judicial do caso concreto proferida no processo nº 0001234"
resultado6 = validar_argumento_6(texto6)
print(f"\nTESTE 6 (processo nº): {'✅ ACEITOU' if resultado6 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto6}")

# ========== TESTES ARGUMENTO 9 ==========

print("\n" + "=" * 80)
print("TESTES ARGUMENTO 9 (Processo Administrativo Anterior)")
print("=" * 80)

# ❌ TESTE 7: Deve REJEITAR - normas administrativas gerais
texto7 = "O procedimento administrativo deve ser conduzido conforme a Lei 9.784/99"
resultado7 = validar_argumento_9(texto7)
print(f"\nTESTE 7 (normas gerais): {'❌ REJEITOU' if not resultado7 else '✅ ACEITOU'} (esperado: REJEITAR)")
print(f"Texto: {texto7}")

# ❌ TESTE 8: Deve REJEITAR - procedimentos genéricos
texto8 = "As normas administrativas determinam que o órgão siga os ritos estabelecidos"
resultado8 = validar_argumento_9(texto8)
print(f"\nTESTE 8 (procedimentos genéricos): {'❌ REJEITOU' if not resultado8 else '✅ ACEITOU'} (esperado: REJEITAR)")
print(f"Texto: {texto8}")

# ✅ TESTE 9: Deve ACEITAR - caso já julgado administrativamente
texto9 = "Este mesmo caso já foi avaliado e devidamente auditado por este órgão, conforme Nota Técnica em anexo"
resultado9 = validar_argumento_9(texto9)
print(f"\nTESTE 9 (caso já avaliado): {'✅ ACEITOU' if resultado9 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto9}")

# ✅ TESTE 10: Deve ACEITAR - processo administrativo anterior com NUP
texto10 = "Processo administrativo anterior (NUP 50001234567) já analisou a matéria e deferiu o benefício"
resultado10 = validar_argumento_9(texto10)
print(f"\nTESTE 10 (NUP anterior): {'✅ ACEITOU' if resultado10 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto10}")

# ✅ TESTE 11: Deve ACEITAR - decisão administrativa anterior
texto11 = "Já existe decisão administrativa anterior favorável sem apresentação de novos elementos"
resultado11 = validar_argumento_9(texto11)
print(f"\nTESTE 11 (decisão administrativa): {'✅ ACEITOU' if resultado11 else '❌ REJEITOU'} (esperado: ACEITAR)")
print(f"Texto: {texto11}")

# ========== RESUMO ==========

print("\n" + "=" * 80)
print("RESUMO DOS TESTES")
print("=" * 80)

testes_arg6 = [
    ("TESTE 1", not resultado1, True),
    ("TESTE 2", not resultado2, True),
    ("TESTE 3", not resultado3, True),
    ("TESTE 4", resultado4, True),
    ("TESTE 5", resultado5, True),
    ("TESTE 6", resultado6, True),
]

testes_arg9 = [
    ("TESTE 7", not resultado7, True),
    ("TESTE 8", not resultado8, True),
    ("TESTE 9", resultado9, True),
    ("TESTE 10", resultado10, True),
    ("TESTE 11", resultado11, True),
]

total_testes = len(testes_arg6) + len(testes_arg9)
testes_passaram = sum(1 for _, resultado, esperado in testes_arg6 + testes_arg9 if resultado == esperado)

print(f"\nArgumento 6: {sum(1 for _, r, e in testes_arg6 if r == e)}/{len(testes_arg6)} testes passaram")
print(f"Argumento 9: {sum(1 for _, r, e in testes_arg9 if r == e)}/{len(testes_arg9)} testes passaram")
print(f"\nTOTAL: {testes_passaram}/{total_testes} testes passaram")

if testes_passaram == total_testes:
    print("\n✅ TODOS OS TESTES PASSARAM! A validação está funcionando corretamente.")
else:
    print(f"\n⚠️ {total_testes - testes_passaram} teste(s) falharam. Revisar lógica de validação.")
