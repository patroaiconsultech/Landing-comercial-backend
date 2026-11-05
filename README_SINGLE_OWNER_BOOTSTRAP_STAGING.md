# PatroAI Comercial R2 — Candidate PR (sem merge / sem deploy)

**Escopo:** Somente backend `patroaiconsultech/Landing-comercial-backend`.
**Data:** 2026-11-05.
**Modo:** preparação de Pull Request, NÃO instalação no Railway.

## Primeiro ponto divergente comprovado

O ZIP incremental foi executado isoladamente na auditoria AO-01 e o teste
`test_real_r2_password_mfa_and_encryption_contract` falhou com
`ModuleNotFoundError: No module named 'dashboard'`.

Este ZIP é um **patch incremental**, não um checkout completo. Não inclua
cópias de `dashboard/auth.py` ou `dashboard/models.py` no patch: esses módulos
devem vir do repositório oficial. O bootstrap original NÃO mudou.

## Aplicação SOMENTE em branch de revisão

**Não extraia diretamente sobre `main` conectado ao Railway.**
O PR/branch exige gate e autorização humana próprios. Nada aqui autoriza
merge, deploy, criação de contas ou ativação do Dashboard.

Os arquivos do pacote se aplicam à raiz do backend:

| Path | Ação | Risco |
| --- | --- | --- |
| `tools/bootstrap_single_owner.py` | CREATE | Ferramenta offline, gate desligado |
| `tests/test_single_owner_bootstrap.py` | CREATE | Testes negativos e PostgreSQL |
| `pytest.ini` | CREATE se ausente | Marker para PostgreSQL |
| `.github/workflows/single-owner-bootstrap-r2-tests.yml` | CREATE | CI só em PR/manual |
| `README_SINGLE_OWNER_BOOTSTRAP_STAGING.md` | CREATE | Guia |
| `LOCAL_TEST_EVIDENCE.txt` | CREATE | Evidência local |
| `CI_VALIDATION_REPORT.md` | CREATE | Handoff técnico |
| `SHA256SUMS_SINGLE_OWNER_R2.txt` | CREATE | Hashes do patch |

Caso qualquer arquivo já exista, comparar antes de substituir.

## O novo workflow CI

`r2-unit`: faz checkout do **backend completo**; instala requirements-test;
verifica SHA-256 do bootstrap e `dashboard/auth.py` e `dashboard/models.py`
contra o R2 auditado; importa os módulos; roda pytest focado
excluindo `integration_postgres`.

`postgres-18-lab`: inicializa **PostgreSQL 18 EFÊMERO no runner GitHub** com
banco `patroai_r2_lab` e credenciais de teste (não são segredos reais);
aplica R1 e R2 **apenas nesse banco efêmero**, executa os dois testes
PostgreSQL com URL explícita, recusa skips e comprova zero contas e zero
auditorias após rollback.

O workflow NÃO tem secrets Railway, nenhum passo de deploy, SSH, migration
remota ou execução da ferramenta `bootstrap_single_owner.py execute`.

**Importante:** o pin de hash de `auth.py`/`models.py` se refere à versão
R2 informada pelo operador como implantada. Se HEAD tiver divergido
legitimamente, o workflow falhará e requererá conciliação de contrato
antes de permitir CI verde. Não remova o pin silenciosamente.

## Validação local reproduzida

Fonte de teste: ZIP backend histórico `Landing-comercial-backend-main (3).zip`
+ overlay R2 `DG01_PATROAI_R2_SUPER_ADMIN_SOURCE_20261105.zip`
+ patch incremental. Esta reconstrução **não equivale a git checkout do HEAD**.

```
python -m pytest -q -ra tests/test_single_owner_bootstrap.py test_super_admin_r2.py
40 passed, 2 skipped, exit 0
```

A suíte ampliada no mesmo snapshot: `54 passed, 3 failed, 2 skipped`.
As três falhas são testes HTML que exigem `index.html` e
`baseline_HC11_AUDITADO.html`, ausentes no snapshot histórico;
**não representam testes PASS** nem prova de regressão em HEAD atual.

O operador apresentou anteriormente um ensaio PostgreSQL real no Railway
em `patroai_r2_lab` com `LAB_PROPOSAL_REHEARSAL=PASS`,
`FINAL_LAB_ACCOUNTS=0` e `FINAL_LAB_AUDITS=0`. Isso é evidência
operacional reportada, não execução desta sessão.
GitHub Actions e o job PostgreSQL efêmero **ainda não foram executados**.

## Estado de proveniência

Último runtime informado pelo operador:
- Railway STAGING deployment `358d0f27-49d6-48de-854d-1ed3ae96d8c8`
- commit `10dea9df9c1801af0c9026c06b98adf1923b9459`
- auth SHA-256 `5e8e18e92077752ba5e37436f1c6b67be1b63ddf66a30b88ae8875538c05bb27`
- models SHA-256 `c50ad376d4bec44f201ef881e137991d0627cfc747c6aba97bc2a8b6d4e172a1`

GitHub `main` foi consultado por página/raw source; o SHA exato de
HEAD no momento da execução do PR **ainda não foi obtido**.
O SHA de runtime não prova o HEAD atual do GitHub.

## Próximo gate

1. Autorizar especificamente branch/PR (sem merge).
2. Aplicar este patch num checkout Git do backend no HEAD efetivo.
3. Conferir que o diff contém apenas os arquivos desta lista, sem alterações
   em `dashboard/*`, migrations, frontend, segredos ou bootstrap auditado.
4. Rodar as duas jobs do GitHub Actions e registrar commit/execução/results.
5. Submeter resultados e documentos de identidade/autoridade ao AO-01 para
   reconsideração **G1** (ainda NO-GO).
6. **G2**: eventual provisionamento individual ainda exige identidade,
   autorização documental assinada, preflight e GO específico.
7. **G3**: habilitação do Dashboard somente após conta+MFA+auditoria validados.

## Integridade / rollback

Bootstrap original: SHA-256
`b96d870a621e4571fd9c5b8654b846c6ac95616d3dfbe62d9ee838a63fade261`.

Se PR não foi mesclado: fechar o PR ou descartar a branch.
Se integração futura for autorizada e depois precisar reversão: reverter
seletivamente os arquivos, manter `PATROAI_DASHBOARD_ENABLED=false`
e `PATROAI_SINGLE_OWNER_BOOTSTRAP_GATE` desabilitado; revogar uma
eventual conta por operação administrativa auditada, não por `DROP`/DELETE.

**PRODUCTION=NO_GO | G1=NO_GO | G2=NO_GO | G3=NO_GO**
