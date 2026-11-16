# Backend comercial PatrioAI / PatroAI — treinamento DG-01

**Gate:** `PROPOSAL_ONLY`, sem branch/commit/push/deploy.

Origem: ZIP `Landing-comercial-backend-main (7).zip`. Este não é o backend
EFATÀ 777 oficial. Código EFATÀ e migrations não foram alterados.

## Alteração nesta entrega
Apenas `app.py`: atualização de `SYSTEM_PROMPT` e `DIAGNOSIS_PROMPT` do
agente comercial para apresentar consultoria, estudo de viabilidade
econômico-financeira, Business Plan, holding / venture builder,
desenvolvimento de IA, especialistas associados e governança.

Preservados: rotas existentes, FastAPI, banco, modelagem, permissões,
feature flags, provedor de IA, limites de segurança e integrações.
Novo manual de atendimento: `docs/AGENTE_COMERCIAL_PLAYBOOK.md`.

## Privacidade e limites
Este **chat público usa provedor externo** quando habilitado. Os visitantes
não devem enviar planos de negócios sigilosos, segredos ou dados pessoais
sensíveis. Controle e privacidade de projetos privados exigem revisão
separada de arquitetura, contratos e runtime; não foram implementados
nesta alteração. Painel de investidores permanece conceitual.

## Testes focados
```
pytest -q tests/test_venture_prompt.py test_provider_contract_ao01.py
```

Testes legados `test_app.py` também exigem arquivos `index.html` e
`baseline_HC11_AUDITADO.html` **não incluídos neste ZIP**; suas falhas
de fixture são esperadas até que baseline e layout de teste sejam
providenciados, e não justificam alterar rotas ou autenticação.

## Rollback
Repor `app.py` do ZIP de origem. Nenhuma migration ou mudança de dados.
Handoff e promoção mediante validação AO-01 + aprovação humana.

## Correção R2 — raiz da API sem HTML
Como frontend e backend são distribuídos em pacotes diferentes, os endpoints
`GET /` e `GET /index.html` retornam `404 LANDING_NOT_AVAILABLE_ON_API`
quando `index.html` não está neste pacote. Isso evita o erro 500 de `FileResponse`.
Nenhum endpoint de chat, autenticação, autorização, banco ou migration foi alterado.
Teste focado: `pytest -q tests/test_backend_root_guard.py`.
O teste legado `test_app.py` ainda pressupõe arquivos ausentes no ZIP original;
não foi reescrito para esconder esta divergência.
