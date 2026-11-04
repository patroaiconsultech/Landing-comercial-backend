# DG-01 | PatroAI Dashboard R1 — Diagnóstico read-only (04/11/2026)

## Situação comprovada

A interface exibe **"Painel administrativo ainda não habilitado."**
No `dashboard.js` do pacote DG-01 essa mensagem é mapeada do backend
`503 {"detail":"ADMIN_UNAVAILABLE"}`. Os logs enviados mostram Uvicorn
iniciado, mas **não** exibem requisições HTTP e não comprovam login.
O backend define este erro quando `PATROAI_DASHBOARD_ENABLED != "true"`
ou quando alguma configuração necessária não atende aos requisitos.
O patch anterior ajustou mensagens, **não** ativou o dashboard.

## Próximo comando já disponível, sem precisar editar/deployar arquivos

Em terminal autenticado e vinculado ao projeto Railway, selecionando o
serviço **backend** e o ambiente correto:

```sh
railway ssh -s NOME_DO_BACKEND -e NOME_DO_AMBIENTE -- python -c "from dashboard.config import config; config(); print('CONFIG_OK')"
```

Esse comando chama somente a validação de configuração **já existente**
na aplicação; não faz login, não abre conexão ao banco, não executa
migração e não imprime valores. Interpretar a última linha:
- `RuntimeError: ADMIN_DISABLED` → flag ausente ou diferente de `true` (minúsculas).
- `RuntimeError: ADMIN_ORIGIN_INVALID` → origem HTTPS ausente ou inválida.
- `RuntimeError: ADMIN_CONFIG_INVALID` → alguma chave ausente/inválida/repetida.
- `RuntimeError: POSTGRES_REQUIRED` → DATABASE_URL ausente ou esquema incompatível.
- `CONFIG_OK` → configuração sintaticamente válida, **não** comprova PostgreSQL, tabelas, conta ou login.

**Não altere a flag para `true` apenas para fazer passar este comando.**
Ela é controle de autorização de implantação; outros gates vêm antes.

## Verificação adicional de banco e gestor (artefato opcional)

`patroai_admin_preflight.py` executa apenas leitura:
- verifica os nomes das variáveis e a forma das chaves, sem revelar valores;
- abre uma transação PostgreSQL `READ ONLY`;
- consulta `alembic_version`, presença das oito tabelas e **existência**
  de ao menos um gestor ativo; nunca lê senha, hashes ou segredos TOTP.

**Uso:** depois de revisão por AO-01, disponibilize o script no backend
de STAGING em `tools/patroai_admin_preflight.py` e execute dentro do
container: `python tools/patroai_admin_preflight.py`.

Ele não executa migrações nem modifica a configuração.
`LOGIN_PREREQUISITOS=CANDIDATOS_A_TESTE_E2E` não equivale a login validado.

## Sequência de ativação (sem ações automáticas)

1. Confirmar ligação `DATABASE_URL` ao PostgreSQL autorizado e fazer
   backup/snapshot consistente.
2. Auditar migração `0001_patroai` em STAGING (locks, compatibilidade,
   tabelas existentes, rollback sem DROP automático). Só executar após
   AO-01 e autorização humana com gate `PATROAI_ALLOW_MIGRATION_EXECUTION=true`.
3. Configurar no **backend**, exclusivamente via gerenciador de segredos
   Railway: `PATROAI_ADMIN_ORIGIN=https://patroai.com.br`, `DATABASE_URL`,
   `PATROAI_ADMIN_SESSION_KEY`, `PATROAI_ADMIN_TOTP_KEY`,
   `PATROAI_HANDOFF_ENCRYPTION_KEY`. As três chaves devem ter 32 bytes,
   como 64 dígitos hexadecimais, ser distintas e permanecer estáveis.
4. Provisionar primeira conta manager pela ferramenta offline
   `tools/provision_manager.py` com **duas aprovações Ed25519 independentes**,
   TTY seguro, senha forte e inscrição TOTP; sem seed automático.
5. Após dados e gestor testados em STAGING, ativar
   `PATROAI_DASHBOARD_ENABLED=true`, aplicar alterações no Railway e
   testar `GET /v1/admin/auth/session` (sem cookie deve retornar 401),
   autenticação com MFA, CSRF, sessão e logout.
6. AO-01 revisa as evidências antes de aprovação humana e eventual
   ativação controlada em produção. Chat público é independente.

## Rollback

Em caso de erro após ativação, retornar
`PATROAI_DASHBOARD_ENABLED=false` no serviço backend e aplicar
a mudança. Preservar banco, hashes, eventos e trilhas de auditoria;
nenhum downgrade destrutivo automático.

## Evidência e limitações

- Quatro testes unitários de validação local passaram.
- NÃO executado contra PostgreSQL ou Railway reais.
- Nenhuma variável, tabela, usuário, deploy ou senha foi alterada.
- Arquivo diagnóstico é ferramenta opcional, não patch de produção.
