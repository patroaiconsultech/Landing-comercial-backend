# PatroAI — BACKEND AGENTE (candidato, sem deploy)

Este e o BACKEND do assistente comercial da PatroAI, NAO o backend C01
e NAO os repositorios EFATA 777.

Arquivos executaveis preservados BYTE A BYTE:
- app.py: API FastAPI, health/status/chat, integracao OpenAI Responses API.
- requirements.txt e requirements-test.txt: dependencias.
- railway.json e start.sh: configuracao proposta para Railway.
- test_app.py e test_provider_contract_ao01.py: testes de referencia.
- .env.example: APENAS nomes de variaveis, sem chave real.

DEPENDENCIA ENTRE OS DOIS ZIPs (IMPORTANTE)
O app.py original procura BASE / "index.html" e atende GET / e /index.html,
alem de /v1/public/chat/status e POST /v1/public/chat.
Este ZIP nao inclui a landing de ~8 MB para manter os downloads separados.
Para executar a aplicacao tal como esta, extraia FRONTEND e BACKEND juntos
na MESMA pasta. A API nao foi alterada.

PARA RODAR LOCALMENTE (somente se autorizado)
  python -m pip install -r requirements.txt
  python -m uvicorn app:app --host 127.0.0.1 --port 8000 --no-access-log
Acesse http://127.0.0.1:8000 para a landing. Sem CHAT_ENABLED=true e chave
OPENAI_API_KEY em variaveis de ambiente, o chat aparece indisponivel.

Para executar os testes ORIGINAIS com 17 cenarios e verificacao exata do
HTML HC11, alem de index.html da landing, e necessario copiar para a mesma
pasta o baseline_HC11_AUDITADO.html do pacote auditavel anterior.
Esse arquivo de ~8 MB foi EXCLUIDO dos 2 ZIPs para evitar downloads grandes.
Sem o baseline, test_app.py nao passa integralmente; isso e esperado.
Nunca inventar PASS ou usar mocks como resposta real de IA.

SOBRE DOIS REPOSITORIOS
Estes ZIPs permitem ORGANIZAR codigos frontend e backend separadamente.
Nao significam dois deploys independentes ja configurados: o JS frontend
usa caminhos relativos no mesmo dominio, e GET / do backend procura
o index.html no mesmo diretorio. Deploy separado requer contrato CORS/API
e configuracao especifica, com revisao/teste antes de publicar.

SEGURANCA
Nao colocar OPENAI_API_KEY no frontend, historico Git ou .env versionado.
Os limites atuais de uso sao em memoria por instancia, insuficientes
isoladamente para um endpoint publico antiabuso. Revisar privacidade,
custo e controle de borda, testar chamada REAL com chave server-side,
e obter gates de autorizacao antes de deploy.

Status: PROPOSAL_ONLY / GITHUB_PUSH=NO_GO / RAILWAY_DEPLOY=NO_GO
