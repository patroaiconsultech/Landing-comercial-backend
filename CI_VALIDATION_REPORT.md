# DG-01 → AO-01 | Reconciliação de CI e pacote incremental

**Data:** 2026-11-05
**Auditoria anterior:** `NO-GO G1`, primeiro erro `ModuleNotFoundError: dashboard`
ao executar diretamente testes num ZIP incremental.
**Status do pacote:** PR review only; não integrado.

## Diagnóstico causal
Ao adicionar o pacote a um snapshot backend + R2:
`dashboard.auth` e `dashboard.models` importam e os testes focados
executam (`40 passed`, `2 skipped`, `0 failed`). Isso confirma que o
primeiro erro deriva do contexto de teste isolado, não demonstra
ausência do módulo no repositório oficial. A página GitHub lista
`dashboard` e `test_super_admin_r2.py` na `main`.

## Arquivos e alterações
- `tools/bootstrap_single_owner.py`: byte-idêntico ao auditado,
  SHA-256 `b96d870a621e4571fd9c5b8654b846c6ac95616d3dfbe62d9ee838a63fade261`.
- `tests/test_single_owner_bootstrap.py`: sem alteração.
- `pytest.ini`: sem alteração.
- `.github/workflows/single-owner-bootstrap-r2-tests.yml`: revisão do workflow
  para testar checkout completo e rodar dois testes PostgreSQL em
  PostgreSQL 18 efêmero, recusando `skipped`.
- README/evidências/manifesto: atualizados para classificações corretas.

## Gates não satisfeitos
- SHA exato do HEAD GitHub: não provado neste ambiente.
- Execução GitHub Actions: não executada.
- CI efêmero PostgreSQL 18: não executado.
- Governança G1: NO-GO; human authorization for PR/branch required.
- G2/G3: NO-GO, identidade, documento assinado e MFA real pendentes.

## Evidências adicionais necessárias, sem repetir o laboratório
1. PR com diff somente nos arquivos previstos, a partir do HEAD Git real.
2. Artefatos/logs dos dois jobs em GitHub Actions, incluindo `CI_PG_TWO_TESTS=PASS_NO_SKIPS`
   e `CI_PG_FINAL_ACCOUNTS=0`, `CI_PG_FINAL_AUDITS=0`.
3. Verificação de identidade e autoridade e autorização documental do proprietário.
4. Decisão AO-01 expressa para G1, antes de qualquer merge na `main`.

**Nenhum deploy, migration STAGING ou criação de super_admin foi realizado.**
