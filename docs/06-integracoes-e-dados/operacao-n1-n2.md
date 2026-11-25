# Operação NOC Atacado — filas Atacado e Triagem

**Uma operação: NOC Atacado. Filas incluídas: NOC - ATACADO e NOC - TRIAGEM.** O nome deste arquivo foi mantido para preservar links existentes; não representa uma divisão por níveis de atendimento.

OTRS permanece a fonte oficial. `NOC_QUEUE_NAMES` define as duas filas; a coleta resolve seus IDs oficiais por nome exato e exige resultado único. Na origem homologada: **107 = NOC - ATACADO** e **181 = NOC - TRIAGEM**. As filas específicas de Cancelamentos já existentes continuam com seu escopo próprio.

## Regras preservadas

- TicketID é a identidade única. Transferências, retornos e reaberturas não criam outro ticket nem outra ocorrência.
- Criados usam a criação oficial; resolvidos usam a data efetiva canônica. Uma resolução na Triagem conta normalmente, mesmo sem passagem pelo Atacado.
- Ativos e aging da fila incluem `OPEN`, `PENDING` e `SCHEDULED`. Backlog inclui somente `OPEN` e `PENDING`, excluindo improcedentes, conforme a regra anterior.
- Fila atual e passagem histórica são informações diferentes. O banco preserva a fila oficial, os eventos e a participação comprovada na operação. Tickets externos sem essa participação não são admitidos no suporte.
- Data Scope e RBAC permanecem aplicados no backend. Dashboard, Cockpit, Chamados, GEO/Apresentação, Qualidade, reincidências, Rankings e Planta × Chamados reutilizam o universo consolidado, mantendo suas exclusões e definições. Nenhum snapshot CMDB é reescrito.
- A interface mantém os componentes aprovados, sem resumo, produtividade, filtro ou rótulo gerencial por nível. O detalhe de Chamados preserva a fila oficial OTRS.

## Coleta e histórico

O Worker existente utiliza as mesmas credenciais e os ciclos FULL/FAST. A assinatura das filas exige FULL antes do primeiro FAST que inclua a Triagem. São coletados também resolvidos e cancelados, passagens pela Triagem e alterações posteriores de tickets conhecidos. A origem permanece somente leitura.

A migration aditiva **`integracoes.0025_noc_triagem_lifecycle`** preserva eventos oficiais em `OTRSTicketEvent`, progresso em `OTRSQueueBackfill`, participação histórica do ticket e fila dos intervalos de backlog. Não há nova migration neste ajuste de interface.

O backfill é paginado por TicketID, usa o sincronizador existente e confirma o cursor na mesma transação de cada lote. Reexecuções não duplicam tickets/eventos; falha preserva os lotes confirmados. `complete=true` indica conclusão. `--restart` revisa desde o início sem apagar registros e só deve ser usado quando houver necessidade comprovada.

O histórico usa a fila e o estado efetivos de cada evento, nunca a fila atual para reconstruir o passado. Evidências antigas somente do Atacado permanecem armazenadas, mas não representam cobertura das duas filas. Lacunas continuam indisponíveis; não se presume histórico completo.

## Próxima atualização no Windows Server — somente pendências da Triagem

Procedimento **documentado, não executado nesta tarefa**. Usar a identidade e a pasta operacional existentes, preservar configurações e confirmar backup válido. Não repetir processamento financeiro, importação legada, sincronização CMDB ou migrations antigas.

```powershell
Set-Location C:\Users\Arion\Desktop\ARION-OPS
$ErrorActionPreference = 'Stop'
git status --short
git fetch origin --tags
if ($LASTEXITCODE -ne 0) { throw 'Falha no fetch' }
git log --oneline HEAD..origin/main
# Prosseguir com checkout limpo e somente a atualizacao esperada.
.\deploy\windows\stop-arion.ps1 -Role worker
.\deploy\windows\stop-arion.ps1 -Role web
git merge --ff-only origin/main
if ($LASTEXITCODE -ne 0) { throw 'Fast-forward recusado' }
$env:ARION_ENV = 'production'
.\.venv\Scripts\python.exe arion_geo\gerenciar.py check
.\.venv\Scripts\python.exe arion_geo\gerenciar.py showmigrations integracoes
.\.venv\Scripts\python.exe arion_geo\gerenciar.py migrate integracoes 0025 --plan
```

Conferir o plano antes de aplicar: a base deve ter `0024` e suas dependências aplicadas. **Se o plano incluir migrations anteriores ou de outros apps, parar e revisar; não aplicar o plano completo automaticamente.** Se `0025` já estiver marcada `[X]`, pular a aplicação. Caso somente `0025` esteja pendente:

```powershell
.\.venv\Scripts\python.exe arion_geo\gerenciar.py migrate integracoes 0025 --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration 0025 falhou' }
```

Na Central de Integrações/registro de execuções, verificar se há FULL aplicado e bem-sucedido com a assinatura atual das cinco filas (incluindo Triagem), backfill concluído e histórico com chave `NOC - ATACADO|NOC - TRIAGEM`. Executar **somente os comandos cujas etapas estiverem pendentes**, com o Worker parado:

```powershell
# Somente se o backfill ainda nao foi concluido; repetir lotes ate complete=true.
.\.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_historico_triagem --apply --batch-size 200 --max-batches 10
# Somente se ainda nao existe FULL aplicado com a cobertura das duas filas.
.\.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_otrs --apply --full
# Somente se ainda falta a cobertura historica consolidada.
.\.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_historico_backlog --apply
```

Conferir sucesso de cada comando antes do próximo. Uma execução interrompida retoma pelo cursor; não usar `--restart` automaticamente. O Worker retomará o acompanhamento incremental normal após iniciar.

```powershell
npm.cmd run build:production
if ($LASTEXITCODE -ne 0) { throw 'Build falhou' }
.\.venv\Scripts\python.exe arion_geo\gerenciar.py collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'Collectstatic falhou' }
.\deploy\windows\production-check.ps1
if ($LASTEXITCODE -ne 0) { throw 'Preflight falhou' }
.\deploy\windows\manage-arion.ps1 -Action Start
.\deploy\windows\status-arion.ps1
```

Web e Worker são os supervisores/tarefas existentes; não criar serviços novos nem reiniciar Cloudflared por causa desta alteração. Conferir saúde, login, totais consolidados e ausência de erros de assets. Preservar `v1.4.0` e `v1.4.1`; não criar `v1.4.2` nesta conferência.

## Conferência DEV — 25/11/2026, 10:19:06 (America/Sao_Paulo)

Após atualização FAST, a leitura oficial e o espelho coincidiram por TicketID: **24 ativos (18 Atacado + 6 Triagem), backlog 24, nenhum agendado e zero faltantes/extras**. Dashboard e Cockpit reconciliaram **706 criados em novembro** e backlog 24. Esses valores são uma referência temporal, não metas fixadas na interface.

Passaram 65 testes focados de escopo, resolução, transferência/deduplicação, reabertura, cancelamento, backlog, GEO/Apresentação, paridade e regressões da interface. Também passaram Django check, migration check, build de produção e smoke tests de Dashboard, Cockpit e Chamados (HTTP 200). Migration 0025, coleta e backfill foram preservados; nenhuma carga histórica extensa foi repetida nem houve ação no servidor de produção.
