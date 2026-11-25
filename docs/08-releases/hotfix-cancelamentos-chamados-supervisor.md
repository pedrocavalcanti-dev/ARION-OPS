# Hotfix operacional — Cancelamentos, diagnóstico de Chamados e supervisor

Base de investigação: `a00569874853fd306791e807416d24d13dd74c76`. Homologação em MySQL 8.4 local, schema de teste separado, com `STRICT_TRANS_TABLES` e `ONLY_FULL_GROUP_BY`. Nenhuma ação em produção nesta tarefa.

Validação: **93 testes Django aprovados** (Cancelamentos, Chamados e respostas de erro), regressão adicional do detalhe/decisão em reconciliação aprovada, **11 testes Python do supervisor/lifecycle aprovados** e testes PowerShell do gerenciador aprovados. Django check, migration check e `git diff --check` passaram. Sem nova migration ou build frontend.

## Cancelamentos

A Entrada OTRS selecionava apenas `REQUEST`, enquanto Pedido selecionava apenas `CancellationProcess` em `ORDER`. A transição **em atendimento → pendente operadora**, classificada como `TREATMENT`, retirava uma entrada `WAITING` da primeira lista sem admiti-la na segunda.

A regra geral agora mostra esses tickets em **Pedido → Aguardando reconciliação**, aplicando Data Scope, filtros e paginação. A projeção cria/reconcilia apenas intakes; não cria processos oficiais. Aprovação explícita pelo fluxo existente admite um Pedido local, conserva o responsável válido e vincula o TicketID canônico. Não há equivalência automática entre `TREATMENT` e uma etapa interna. Processos existentes, rejeições, eventos, documentos e obrigações financeiras permanecem preservados. A transição do TicketID **102987**, StateID **21**, sem processo, possui teste de regressão; reexecuções não duplicam registros.

## Chamados — diagnóstico e limite da homologação

Os registros de produção de 25/11/2026 fornecidos para os Request IDs contêm apenas referência, usuário e rota. O handler anterior descartava a exceção e o logger `django.request` não emitia traceback. Não é possível recuperar retroativamente o tipo da exceção a partir dessas linhas.

O handler 500 agora registra **tipo da exceção, código numérico quando disponível e até 12 pontos de pilha (arquivo, função e linha)** com o mesmo Request ID. Não registra mensagem da exceção, SQL, linhas de código, variáveis locais, query string, corpo, cookies ou credenciais. O frontend mantém a resposta genérica e `DEBUG=False`. Esse diagnóstico não deve ser apresentado como correção da causa funcional de `/chamados/`. Os testes de Chamados passaram no MySQL estrito sem reproduzir uma falha de consulta.

Em 25/11/2026, o operador confirmou HTTP 200 em um processo Django novo, com MySQL real de produção, usuário 4 e `DEBUG=False`. Após reinício controlado somente da Web, Web/Worker/Cloudflared permaneceram ativos, os PIDs do Worker foram preservados e `/health/` retornou `ok`. Essas evidências reforçam a hipótese de manifest antigo mantido em memória no Waitress, iniciado antes do build/collectstatic. Sem traceback das ocorrências anteriores, a causa exata não está comprovada; a homologação pelo navegador ainda precisa ser confirmada. Não foi necessário alterar consultas, permissões ou escopos de Chamados.

### Revisão adicional de `371a6a8` até `a005698`

O serviço `chamados/services.py`, o template da lista, Data Scope e decorators de permissão não mudaram nesse intervalo. A mudança relevante para a lista é o QuerySet compartilhado: duas filas e `has_support_history`. Os eventos novos não são carregados pela listagem; os filtros de presença, fila atual e estado continuam excluindo tickets históricos inativos ou fora das duas filas do atendimento atual.

Cinco testes adicionais e focados passaram no MySQL local estrito, sem repetir a suíte de 93: usuários não superusuários com acesso global e múltiplos escopos em interseção, duas filas, datas nulas, registros históricos, múltiplos CIs, responsáveis, agregações, ordenações, filtros salvos da integração anterior e renderização com armazenamento de assets de produção.

Foi reproduzido um mecanismo concreto de HTTP 500: o frontend aponta para o bundle novo de Chamados, mas `staticfiles.json` não contém essa entrada. A renderização lança `ValueError: Missing staticfiles manifest entry` em `arion_assets.py → static → storage.stored_name`, antes da execução de JavaScript. Com os manifests compatíveis, a mesma página e os mesmos dados retornam HTTP 200. A integração alterou esse JS, tornando essa divergência uma hipótese verificável; **isso não confirma a causa das ocorrências no servidor**.

No shell Django já configurado como produção, a checagem abaixo é somente leitura:

```python
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from config.templatetags.arion_assets import _production_manifest

assert settings.ARION_PRODUCTION
bundle = _production_manifest()["js/chamados/chamados.js"]
print(bundle)
print(staticfiles_storage.stored_name(bundle))
```

Se falhar com a entrada ausente, conferir build/collectstatic e o estado do processo Web na janela do hotfix. A checagem não modifica arquivos nem reinicia serviços. Não usar fallback para fontes JS ou desabilitar o manifest estrito. Uma checagem em processo novo não inspeciona o cache de um Waitress já iniciado. Nenhuma regra operacional foi alterada nesta revisão.

## Windows

- O gerenciador passa `Role` por hashtable de parâmetros nomeados; arrays posicionais podiam entregar o literal `-Role` ao parâmetro validado.
- Relatórios serializam `List[object]` por `.ToArray()`, evitando o erro de tipos do binder PowerShell.
- A partida utiliza `python.exe` da `.venv` em janela oculta, com saída inicial redirecionada a `var/logs/arion-<role>-startup.*.log`. Falhas capturadas pelo supervisor são sanitizadas e registradas também em `arion-<role>.log`.
- A parada registra solicitação pelo comando de controle. Os locks, PID, instante de criação, cadeia de parentesco, ordem Web/Worker e checagem da porta continuam ativos.
- Teste real com serviço fictício isolado confirmou persistência depois da saída do chamador, recusa de duplicata e parada do intérprete efetivo. Não inicia Django/Worker real nem executa integrações.

O gerenciador já solicitava parada dos processos quando a inicialização/reinicialização falhava parcialmente. Isso pode explicar uma solicitação posterior de desligamento do Worker; o registro anterior, isoladamente, não identifica quem a disparou.

## Deploy incremental manual

Versão do produto mantida em **1.4.1**, identificando o hotfix pelo commit da entrega; sem nova tag de versão. Nenhuma migration, instalação de dependências ou operação extraordinária de dados é necessária. Não executar migration 0025, backfill, FULL OTRS, reconstrução histórica, CMDB ou processamento financeiro.

O gerenciador já exige build (quando os arquivos frontend/templates mudam), collectstatic e production-check antes de iniciar a Web. Nesta janela, publicar novamente os assets para eliminar divergências entre os manifests. O procedimento abaixo para Worker e Web, atualiza somente o código pelo commit revisado, gera assets, coleta arquivos estáticos **sem `--clear`**, valida e só então inicia Web e Worker. Cloudflared, `.env`, credenciais, MySQL e diretórios de documentos não são alterados. O Worker retoma sua agenda normal; nenhum job extraordinário é disparado pelo procedimento.

Executar como operador autorizado, na raiz do checkout do servidor, sem outra atualização simultânea. Substituir o commit pela identificação fornecida na entrega. O hash anterior e uma cópia do lançador corrigido são preservados em `var/run` para rollback, pois o lançador/gerenciador anterior possui as falhas corrigidas neste hotfix.

```powershell
$ErrorActionPreference = 'Stop'
$Rollback = $false # Para rollback, executar o mesmo bloco com $true.
$Hotfix = 'SUBSTITUA_PELO_COMMIT_DA_ENTREGA'
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Falha em $File; nao iniciar servicos antes de corrigir." }
}
$root = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$state = Join-Path $root 'var\run\hotfix-rollback-20261125.txt'
$launcher = Join-Path $root 'var\run\hotfix-start-20261125.ps1'
if (-not (Test-Path -LiteralPath $python)) { throw 'Executar na raiz do ARION.' }
if (Invoke-Checked git @('status', '--porcelain=v1')) { throw 'Checkout com alteracoes; nada foi descartado.' }
$before = (Invoke-Checked git @('rev-parse', 'HEAD')).Trim()
if ($Rollback) {
    $target = (Get-Content -LiteralPath $state -Raw).Trim()
    if (-not (Test-Path -LiteralPath $launcher)) { throw 'Lancador de rollback ausente.' }
} else {
    if ((Invoke-Checked git @('branch', '--show-current')).Trim() -ne 'main') { throw 'Branch deve ser main.' }
    Invoke-Checked git @('fetch', 'origin', 'main')
    $target = $Hotfix
    Invoke-Checked git @('merge-base', '--is-ancestor', $target, 'origin/main')
    Invoke-Checked git @('merge-base', '--is-ancestor', 'HEAD', $target)
}
if ($target -notmatch '^[0-9a-f]{40}$') { throw 'Commit invalido.' }
Invoke-Checked git @('cat-file', '-e', "$target^{commit}")
if ($before -eq $target) { throw 'Commit ja aplicado; nenhuma reinicializacao executada.' }
$null = Get-Command npm.cmd -ErrorAction Stop
New-Item -ItemType Directory -Force -Path (Split-Path $state) | Out-Null
if (-not $Rollback) { Set-Content -LiteralPath $state -Value $before -Encoding ASCII }
.\deploy\windows\stop-arion.ps1 -Role worker -ProjectRoot $root
.\deploy\windows\stop-arion.ps1 -Role web -ProjectRoot $root
if ($Rollback) { Invoke-Checked git @('switch', '--detach', $target) }
else {
    Invoke-Checked git @('merge', '--ff-only', $target)
    Copy-Item -LiteralPath '.\deploy\windows\start-arion.ps1' -Destination $launcher -Force
}
$env:ARION_ENV = 'production'
$env:DJANGO_SETTINGS_MODULE = 'config.settings'
Invoke-Checked npm.cmd @('run', 'build:production')
Invoke-Checked $python @('arion_geo\gerenciar.py', 'collectstatic', '--noinput')
Invoke-Checked $python @('arion_geo\gerenciar.py', 'check')
.\deploy\windows\production-check.ps1 -ProjectRoot $root
# Nenhuma Web pode ser iniciada antes das etapas acima concluirem.
& $launcher -Role web -ProjectRoot $root
& $launcher -Role worker -ProjectRoot $root
Invoke-Checked $python @('deploy\windows\run_process.py', '--status', 'web')
Invoke-Checked $python @('deploy\windows\run_process.py', '--status', 'worker')
Get-Service -Name Cloudflared -ErrorAction SilentlyContinue | Select-Object Name, Status
Write-Host "Codigo ativo: $target; commit anterior registrado em $state"
```

Em caso de falha antes da partida, corrigir a etapa ou executar o mesmo bloco com `$Rollback = $true`. Não iniciar a Web com assets parcialmente publicados. O rollback recupera apenas o código anterior em checkout destacado, refaz build/collectstatic/preflight e usa o lançador corrigido preservado para iniciar os serviços, sem depender do gerenciador antigo. Não restaura banco nem reverte migrations; dados e decisões posteriores ao deploy são mantidos. Uma aplicação futura deve retornar à branch `main` após revisar o commit homologado.

Conferir pelo navegador `/chamados/` com o usuário afetado e **Pedido → Aguardando reconciliação** com o TicketID 102987, responsável preservado e ausência de processo automático. Não aprovar apenas como teste em produção. Conferir usuário com Data Scope restrito. Havendo novo 500, correlacionar Request ID e diagnóstico seguro no log Web, mantendo `DEBUG=False`. Não declarar a homologação pelo navegador concluída antes dessa verificação pelo operador.
