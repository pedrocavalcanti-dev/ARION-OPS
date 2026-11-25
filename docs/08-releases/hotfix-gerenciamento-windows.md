# Gerenciamento Windows 2.0 — hotfix de infraestrutura

Base: `031e164ed449776023f18c6392a117c8610223be`. Versão funcional **1.4.1 preservada**. Hotfix restrito a `deploy/windows/` e documentação; publicação autorizada separadamente do deploy, que permanece manual. Nenhum módulo da aplicação, frontend, modelo, migration ou regra de negócio foi alterado.

## Causas e correções

- **Sucesso prematuro:** a existência do PID do Worker bastava para `running`. Agora exige a mensagem de inicialização emitida após adquirir a lease, identidade do processo efetivo, cinco segundos de estabilidade e três consultas consecutivas com a mesma identidade na partida. A Web também exige listener próprio e `/health/` válido.
- **Corrida na partida:** o lock podia existir antes da publicação dos registros de identidade. Esse intervalo é `starting`, sem falso `failed`. Uma falha antiga também não invalida a nova tentativa antes de ela publicar seu estado.
- **Falso erro na parada:** o código 1 da consulta `stopped` contaminava o código da operação concluída. O helper preserva o código do chamador; scripts que realmente retornam erro continuam provocando falha.
- **Lease residual:** o encerramento forçado do Windows não executa o `finally` do Worker. O processo efetivo agora recebe uma interrupção interna, permitindo a saída normal e liberação da lease. Após o prazo de saída, o fallback atua somente nos PIDs registrados, com horário de criação e parentesco verificados. Não encerra outros processos Python.
- **Reinício infinito:** o supervisor agora limita a cinco falhas em cinco minutos, com backoff de 2, 4, 8 e 16 segundos entre essas tentativas; chega a `failed` e conserva o diagnóstico. Uma execução estável de cinco minutos permite recuperar o orçamento.
- **Diagnóstico perdido:** `runpy` substitui `sys.argv`; o papel é capturado antes disso. Exceções do filho vão pelo stream sanitizado ao supervisor, único escritor do log rotativo. Saída não bufferizada evita atrasar a confirmação do Worker. Preflight registra o erro no log do papel correspondente.
- **Falha parcial derrubava serviços saudáveis:** operações completas preservam a Web quando o Worker falha. Operações por papel não reiniciam o outro processo. A Web precisa confirmar Health Check antes da partida completa do Worker.

Esses mecanismos foram reproduzidos/verificados no DEV. Sem os logs completos da última falha do servidor, não se atribui uma exceção específica à ocorrência de produção.

## Entradas e comandos oficiais

`deploy/windows/ARION-OPS.bat` abre o menu com as 14 opções solicitadas, aceita os argumentos do gerenciador e conserva a pausa padrão. Atalhos existentes que apontam para esse BAT continuam válidos; não há outros BAT/CMD/atalhos versionados.

Na raiz do projeto:

- `powershell.exe -NoProfile -File .\deploy\windows\manage-arion.ps1` — menu; também pode ser executado com `pwsh`.
- `...\manage-arion.ps1 -Action Start|Stop|Restart` — todos os processos; acrescentar `-Role web` ou `-Role worker` para controle isolado.
- `...\manage-arion.ps1 -Action Status|WebLogs|WorkerLogs|Health|ProductionCheck` — consultas e verificações. `Diagnostics` e `Update` permanecem disponíveis por CLI.
- `...\start-arion.ps1 -Role web|worker`, `stop-arion.ps1`, `restart-arion.ps1` e `status-arion.ps1` preservam os comandos individuais.

Menu e CLI do gerenciador preservam a exigência de Administradores locais para controle/Update. O ambiente padrão é `production`; a opção explícita `-Environment development` permite homologação isolada sem modificar `.env`. O status mostra contexto, PIDs efetivos, Cloudflared somente em consulta, Health Check, horário e último resultado persistido. `run_process.py --status web|worker --json` fornece `running=0`, `stopped=1`, `starting/restarting=2` e `failed=3`.

`install-arion.ps1` exige processos parados antes de alterar dependências/assets. `install-tasks.ps1` usa `python.exe`, confirma a Web antes do Worker e registra `--wait-for-web` no boot do Worker. O supervisor mantém as proteções mesmo para tarefas antigas com `pythonw.exe`. `uninstall-tasks.ps1` verifica a propriedade das tarefas e solicita parada controlada antes da remoção. Backup/MySQL e scripts dedicados de instalação do Cloudflare continuam procedimentos separados, sem chamada pelas operações normais. Não reinstalar tarefas neste hotfix.

## Homologação DEV

- Python **3.12.10 Microsoft Store**, venv, Windows PowerShell **5.1** e PowerShell **7.7.0 portátil**, sem trocar o Python instalado.
- **16 testes focados** de identidade, listener, estados, segurança e diagnóstico; **2 testes reais** de lifecycle/falha precoce, também com caminhos contendo espaços; testes PowerShell do gerenciador nas duas versões.
- **Sequência real ponta a ponta aprovada**, com 47 invocações, Waitress, Worker Django normal e MySQL local em schema descartável já existente, sem migrations/cargas. Inclui BAT/menu, sair e reabrir o chamador, estabilidade, idempotência, reinícios isolados/ambos, parada repetida, nova partida, falha de preflight, diagnóstico, Health Check, comandos individuais e Worker controlado pelo PowerShell 7. A lease foi consultada após a parada e estava liberada; nenhuma importação/sincronização de origem foi executada. A entrada ProductionCheck também rejeitou corretamente uma configuração DEV incompleta para produção, persistindo o resultado de falha.
- **Limitação explícita:** a sessão DEV não possui token elevado. O gerenciador sem alteração recusou controle, como esperado. A orquestração real usou uma ponte temporária de teste que substitui somente a checagem de Administrador no diretório DEV isolado; nenhum bypass foi adicionado aos arquivos de produção. Instalação/remoção real de tarefas, ACLs e serviço Cloudflare não foram executadas. A homologação não substitui a confirmação no Windows Server elevado.
- Waitress estava ausente no DEV e foi instalado somente na venv, usando a dependência já declarada em `requirements-prod.txt`. PowerShell portátil e evidências ficam em `var/`, ignorado pelo Git. Não há novas dependências do produto. Não foram repetidas suítes dos módulos funcionais.
- Verificação final focada aprovada: quatro testes das barreiras e um cenário real com 22 invocações usando scripts do commit base, sem repetir a homologação completa. O helper em `var/run` confirmou a raiz; snapshot com PID/criação e conferência física bloquearam a troca enquanto havia processos antigos ou listener Web. O cenário aguardou a expiração natural da lease deixada pelo encerramento antigo, reiniciou os serviços pelos scripts anteriores e confirmou HTTP, processos estáveis e lease da nova execução. Com o Worker parado, recusou a conclusão. A confirmação independe do arquivo de estado novo e não modifica a lease. O snapshot conserva identidades ainda vivas durante tentativas de recuperação/rollback, mesmo se o script antigo remover seus arquivos PID.

## Deploy manual e rollback

Executar somente depois de autorizar/publicar o commit revisado. O bloco é autossuficiente; solicita o hash exato, exige avanço fast-forward e recusa mudanças fora da infraestrutura/documentação prevista. Não instala pacotes, não altera tarefas ou Cloudflared, não gera assets, não executa migrations nem jobs extraordinários. O Worker retoma sua agenda normal.

A primeira parada ainda pode ser realizada pelo supervisor antigo, que encerra o Worker de forma forçada. `--wait-lease` apenas consulta a lease até ela expirar (até 190 segundos); não a apaga, renova nem modifica. Isso evita iniciar outro Worker durante a validade do proprietário anterior. Nas paradas corrigidas, a liberação normal torna a espera imediata.

Na raiz do checkout do servidor, em PowerShell elevado e sem outra atualização simultânea:

```powershell
$ErrorActionPreference = 'Stop'
$Rollback = $false # Para rollback, executar este mesmo bloco com $true.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Set-Location -LiteralPath 'C:\Users\Arion\Desktop\ARION-OPS'
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $File @Arguments 2>&1 | ForEach-Object {
            ([string]$_) -replace '(?i)(://[^:/\s]+:)[^@/\s]+@', '$1[REDACTED]@'
        })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    $lines
    if ($code -ne 0) { throw "Falha em $File (codigo $code). Corrija ou execute o rollback; nao force a partida." }
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Abra PowerShell como administrador.' }
$root = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$state = Join-Path $root 'var\run\windows-manager-2-rollback.txt'
$leaseHelper = Join-Path $root 'var\run\windows-manager-2-lease.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath '.git')) { throw 'Executar na raiz do ARION.' }
if (Invoke-Checked git @('status', '--porcelain=v1')) { throw 'Checkout alterado; nenhum arquivo sera descartado.' }
$before = (Invoke-Checked git @('rev-parse', 'HEAD')).Trim()
if ($Rollback) {
    $target = (Get-Content -LiteralPath $state -Raw).Trim()
} else {
    if ((Invoke-Checked git @('branch', '--show-current')).Trim() -ne 'main') { throw 'Branch deve ser main.' }
    Invoke-Checked git @('fetch', 'origin', 'main')
    $target = (Read-Host 'Commit completo do hotfix, publicado apos autorizacao').Trim()
}
if ($target -notmatch '^[0-9a-f]{40}$') { throw 'Commit invalido.' }
Invoke-Checked git @('cat-file', '-e', "$target^{commit}")
if (-not $Rollback -and $before -eq $target) { throw 'Commit ja aplicado; nenhuma parada executada.' }
if (-not $Rollback) {
    Invoke-Checked git @('merge-base', '--is-ancestor', $target, 'origin/main')
    Invoke-Checked git @('merge-base', '--is-ancestor', 'HEAD', $target)
}
$changed = @(Invoke-Checked git @('diff', '--name-only', "$before..$target"))
$forbidden = @($changed | Where-Object {
    $_ -notmatch '^deploy/windows/' -and
    $_ -ne 'docs/02-arquitetura/implantacao-e-producao.md' -and
    $_ -ne 'docs/08-releases/hotfix-gerenciamento-windows.md'
})
if ($forbidden.Count) { throw 'Commit altera arquivos fora do hotfix Windows; revisar antes de continuar.' }
New-Item -ItemType Directory -Force -Path (Split-Path $state) | Out-Null
if (-not $Rollback) {
    $source = @(& git show ('{0}:deploy/windows/run_process.py' -f $target))
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel preparar o controle da lease; nenhum servico foi parado.' }
    [IO.File]::WriteAllLines($leaseHelper, [string[]]$source, (New-Object System.Text.UTF8Encoding($false)))
    Set-Content -LiteralPath $state -Value $before -Encoding ASCII
}
if (-not (Test-Path -LiteralPath $leaseHelper)) { throw 'Helper de rollback ausente; conferir antes de parar.' }
$env:ARION_ENV = 'production'
$env:DJANGO_SETTINGS_MODULE = 'config.settings'
$helperRoot = (Invoke-Checked $python @($leaseHelper, '--deployment', 'root', 'worker')).Trim()
if ([IO.Path]::GetFullPath($helperRoot) -ne [IO.Path]::GetFullPath($root)) { throw 'Helper resolveu outra raiz; nenhum servico foi parado.' }
Invoke-Checked $python @($leaseHelper, '--deployment', 'snapshot', 'worker')
foreach ($role in @('worker', 'web')) {
    Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'deploy\windows\stop-arion.ps1'), '-Role', $role, '-ProjectRoot', $root)
}
Invoke-Checked $python @($leaseHelper, '--deployment', 'stopped', 'worker')
Invoke-Checked $python @($leaseHelper, '--wait-lease', 'worker')
Invoke-Checked $python @($leaseHelper, '--deployment', 'stopped', 'worker')
if ($Rollback) {
    if ($before -ne $target) { Invoke-Checked git @('switch', '--detach', $target) }
} else {
    Invoke-Checked git @('merge', '--ff-only', $target)
}
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'deploy\windows\production-check.ps1'), '-ProjectRoot', $root)
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'deploy\windows\manage-arion.ps1'), '-Action', 'Start', '-ProjectRoot', $root)
Invoke-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'deploy\windows\status-arion.ps1'), '-ProjectRoot', $root)
Invoke-Checked $python @($leaseHelper, '--deployment', 'running', 'worker')
Write-Host "Codigo ativo: $target | rollback registrado em $state"
```

Rollback usa o commit anterior registrado, sem restore de MySQL ou reversão de migrations; configuração, credenciais, documentos e dados históricos/financeiros permanecem preservados. Retorna ao comportamento da infraestrutura anterior e deixa o checkout destacado; revisar e retornar à `main` na próxima atualização autorizada. Se uma etapa falhar, não iniciar serviços sem confirmar o estado real e a disponibilidade da lease.
