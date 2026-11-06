[CmdletBinding()]
param(
    [ValidateSet('Menu', 'Start', 'Stop', 'Restart', 'Status', 'Update', 'Diagnostics')]
    [string]$Action = 'Menu',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$script:Root = [IO.Path]::GetFullPath($ProjectRoot)
$script:LogPath = $null
$script:LockStream = $null
$script:Steps = New-Object System.Collections.Generic.List[object]
$script:HasError = $false

function Protect-Text([string]$Text) {
    if ($null -eq $Text) { return '' }
    $safe = $Text -replace '(?i)(password|passwd|token|secret|client_secret|api_key|authorization|cookie|database_url)(\s*[=:]\s*)([^\s;,&]+)', '$1$2[REDACTED]'
    $safe = $safe -replace '(?i)(://[^:/\s]+:)[^@/\s]+@', '$1[REDACTED]@'
    return $safe
}

function Write-Log([string]$Message, [string]$Level = 'INFO') {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, (Protect-Text $Message)
    Write-Host $line
    if ($script:LogPath) { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 }
}

function Add-Step([string]$Name, [string]$State, [string]$Detail = '') {
    $script:Steps.Add([pscustomobject]@{ name = $Name; state = $State; detail = (Protect-Text $Detail); at = (Get-Date).ToString('o') })
}

function Assert-Project {
    if (-not (Test-Path -LiteralPath (Join-Path $script:Root '.git'))) { throw "Diretório do projeto sem .git: $script:Root" }
    foreach ($path in @('.venv\Scripts\python.exe', 'arion_geo\gerenciar.py', 'deploy\windows\run_process.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:Root $path) -PathType Leaf)) { throw "Arquivo necessário não encontrado: $path" }
    }
}

function Assert-AuthorizedOperator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Esta operação exige uma conta autorizada do grupo Administradores locais. Abra o gerenciador como administrador.'
    }
}

function Enter-OperationLock {
    $run = Join-Path $script:Root 'var\run'
    New-Item -ItemType Directory -Path $run -Force | Out-Null
    $path = Join-Path $run 'manage-arion.lock'
    try { $script:LockStream = [IO.File]::Open($path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
    catch { throw 'Já existe outra operação do gerenciador em andamento (ou o lock está inacessível).' }
}

function Exit-OperationLock {
    if ($script:LockStream) { $script:LockStream.Dispose(); $script:LockStream = $null }
}

function Invoke-Native([string]$File, [string[]]$Arguments, [string]$Label, [switch]$AllowNonZero) {
    Write-Log "Iniciando: $Label"
    $lines = @(& $File @Arguments 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
    foreach ($line in $lines) { if ($line) { Write-Log $line 'OUT' } }
    if ($code -ne 0 -and -not $AllowNonZero) { Add-Step $Label 'falha' "código $code"; throw "$Label falhou (código $code)." }
    Add-Step $Label $(if ($code -eq 0) { 'ok' } else { "codigo-$code" })
    return [pscustomobject]@{ Code = $code; Output = ($lines -join "`n") }
}

function Invoke-ArionScript([string]$Name, [string[]]$Arguments = @()) {
    $path = Join-Path $script:Root ("deploy\windows\{0}.ps1" -f $Name)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Script ausente: $path" }
    Write-Log "Executando $Name"
    try { & $path @Arguments -ProjectRoot $script:Root; Add-Step $Name 'ok' }
    catch { Add-Step $Name 'falha' $_.Exception.Message; throw "Etapa '$Name' falhou: $($_.Exception.Message)" }
}

function Get-RoleStatus([string]$Role) {
    $python = Join-Path $script:Root '.venv\Scripts\python.exe'
    $runner = Join-Path $script:Root 'deploy\windows\run_process.py'
    $result = Invoke-Native $python @($runner, '--status', $Role) "Consultar estado $Role" -AllowNonZero
    $state = switch ($result.Code) { 0 { 'running' } 1 { 'stopped' } 2 { 'unavailable' } default { 'error' } }
    return [pscustomobject]@{ Role = $Role; State = $state; Code = $result.Code }
}

function Get-WebPort {
    $configured = $env:ARION_WEB_PORT
    if (-not $configured) {
        $envFile = Join-Path $script:Root '.env'
        if (Test-Path -LiteralPath $envFile) {
            $match = Select-String -LiteralPath $envFile -Pattern '^\s*ARION_WEB_PORT\s*=\s*["'']?(\d+)' | Select-Object -First 1
            if ($match) { $configured = $match.Matches[0].Groups[1].Value }
        }
    }
    if ($configured) {
        $port = 0
        if (-not [int]::TryParse($configured, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw 'ARION_WEB_PORT deve ser uma porta TCP válida.' }
        return $port
    }
    return 8000
}

function Test-WebReadiness([int]$TimeoutSeconds = 0) {
    $port = Get-WebPort
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health/" -f $port) -UseBasicParsing -TimeoutSec 3
            $payload = $response.Content | ConvertFrom-Json
            if ($response.StatusCode -eq 200 -and $payload.status -eq 'ok') { return $true }
        } catch { }
        if ($TimeoutSeconds -le 0 -or [DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Show-Status {
    $states = @((Get-RoleStatus 'web'), (Get-RoleStatus 'worker'))
    foreach ($item in $states) { Write-Log ("{0}: {1}" -f $item.Role, $item.State) }
    if (($states | Where-Object { $_.Role -eq 'web' -and $_.State -eq 'running' }).Count -gt 0) {
        if (Test-WebReadiness) { Write-Log 'Web HTTP health: ok' 'OK' } else { Write-Log 'Web HTTP health: sem resposta válida' 'WARN' }
    }
    $service = Get-Service -Name 'Cloudflared' -ErrorAction SilentlyContinue
    if ($service) { Write-Log "Cloudflared: $($service.Status) (somente consulta)" } else { Write-Log 'Cloudflared: não instalado' }
    return $states
}

function Assert-RoleState([string]$Expected) {
    $states = @((Get-RoleStatus 'web'), (Get-RoleStatus 'worker'))
    $bad = @($states | Where-Object { $_.State -ne $Expected })
    if ($bad.Count -gt 0) {
        $badText = ($bad | ForEach-Object { '{0}={1}' -f $_.Role, $_.State }) -join ', '
        throw "Estado final inesperado: $badText"
    }
}

function Start-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    Write-Log 'Validando Python da .venv e iniciando Web antes do Worker.'
    try {
        Invoke-ArionScript 'start-arion' @('-Role', 'web')
        Invoke-ArionScript 'start-arion' @('-Role', 'worker')
        Assert-RoleState 'running'
        if (-not (Test-WebReadiness -TimeoutSeconds 30)) { throw 'Web não confirmou /health/.' }
    } catch {
        Write-Log 'A inicialização falhou; solicitando parada dos processos ARION para evitar estado parcial.' 'ERRO'
        foreach ($role in @('worker', 'web')) {
            try { Invoke-ArionScript 'stop-arion' @('-Role', $role) }
            catch { Write-Log "Não foi possível confirmar a parada de $role após falha: $($_.Exception.Message)" 'ERRO' }
        }
        throw "Falha ao iniciar os serviços: $($_.Exception.Message)"
    }
    Write-Log 'Web e Worker ativos.' 'OK'
    Show-Status | Out-Null
}

function Stop-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    Write-Log 'Parando Worker antes do Web; somente supervisores ARION serão sinalizados.'
    Invoke-ArionScript 'stop-arion' @('-Role', 'worker')
    Invoke-ArionScript 'stop-arion' @('-Role', 'web')
    Assert-RoleState 'stopped'
    Write-Log 'Web e Worker encerrados.' 'OK'
    Show-Status | Out-Null
}

function Restart-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    Write-Log 'Reiniciando na ordem Worker → Web → Web → Worker.'
    Invoke-ArionScript 'stop-arion' @('-Role', 'worker')
    Invoke-ArionScript 'stop-arion' @('-Role', 'web')
    Assert-RoleState 'stopped'
    try {
        Invoke-ArionScript 'start-arion' @('-Role', 'web')
        Invoke-ArionScript 'start-arion' @('-Role', 'worker')
        Assert-RoleState 'running'
        if (-not (Test-WebReadiness -TimeoutSeconds 30)) { throw 'Web não confirmou /health/.' }
    } catch {
        Write-Log 'A reinicialização falhou; solicitando parada dos processos ARION para evitar estado parcial.' 'ERRO'
        foreach ($role in @('worker', 'web')) {
            try { Invoke-ArionScript 'stop-arion' @('-Role', $role) }
            catch { Write-Log "Não foi possível confirmar a parada de $role após falha: $($_.Exception.Message)" 'ERRO' }
        }
        throw "Falha ao reiniciar os serviços: $($_.Exception.Message)"
    }
    Write-Log 'Reinício concluído; Web e Worker confirmados.' 'OK'
    Show-Status | Out-Null
}

function Get-Git([string[]]$Arguments, [string]$Label, [switch]$AllowNonZero) {
    return Invoke-Native 'git.exe' (@('-C', $script:Root) + $Arguments) $Label -AllowNonZero:$AllowNonZero
}

function Confirm-Exact([string]$Prompt, [string]$Expected) {
    $answer = Read-Host $Prompt
    return ($answer -ceq $Expected)
}

function Invoke-Update {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    $branch = (Get-Git @('branch', '--show-current') 'Verificar branch').Output.Trim()
    if ($branch -ne 'main') { throw "Atualização permitida somente na branch main; branch atual: '$branch'." }
    $dirty = (Get-Git @('status', '--porcelain=v1') 'Verificar alterações locais').Output.Trim()
    if ($dirty) { throw 'Existem alterações locais. Atualização cancelada sem modificar ou descartar arquivos.' }
    $before = (Get-Git @('rev-parse', 'HEAD') 'Ler commit atual').Output.Trim()
    $versionFile = Join-Path $script:Root 'docs\08-releases\releases.json'
    $oldVersion = if (Test-Path $versionFile) { (Get-Content -Raw -LiteralPath $versionFile | ConvertFrom-Json).current_version } else { 'desconhecida' }
    [void](Get-Git @('fetch', 'origin', 'main') 'Buscar origin/main')
    $remote = (Get-Git @('rev-parse', 'origin/main') 'Ler commit remoto').Output.Trim()
    $ancestor = Get-Git @('merge-base', '--is-ancestor', 'HEAD', 'origin/main') 'Verificar avanço somente fast-forward' -AllowNonZero
    if ($ancestor.Code -ne 0) { throw 'A branch local divergiu ou está à frente de origin/main; nenhum merge foi feito.' }
    if ($before -eq $remote) { Write-Log "Já está atualizado ($before)." 'OK'; Add-Step 'update' 'sem-atualizacoes'; return }
    [void](Get-Git @('merge', '--ff-only', 'origin/main') 'Aplicar merge --ff-only')
    $after = (Get-Git @('rev-parse', 'HEAD') 'Ler commit atualizado').Output.Trim()
    $changed = (Get-Git @('diff', '--name-only', "$before..$after") 'Inspecionar arquivos alterados').Output -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $deps = @($changed | Where-Object { $_ -match '(^|/)(requirements[^/]*\.txt|pyproject\.toml|Pipfile|package\.json|package-lock\.json)$' })
    $migrations = @($changed | Where-Object { $_ -match '(^|/)migrations/[^/]+\.py$' -and $_ -notmatch '(^|/)migrations/__init__\.py$' })
    $frontend = @($changed | Where-Object { $_ -match '^(frontend_src/|package\.json$|package-lock\.json$|arion_geo/(templates|static)/)' })
    Write-Log ('Atualização recebida: {0} -> {1}; dependências={2}; migrations={3}; frontend={4}.' -f $before, $after, $deps.Count, $migrations.Count, $frontend.Count)
    $pythonDependencyChange = [bool]($deps | Where-Object { $_ -match '^requirements.*\.txt$|^pyproject\.toml$|^Pipfile$' })
    $frontendDependencyChange = [bool]($deps | Where-Object { $_ -match '^(package\.json|package-lock\.json)$' })
    if ($deps.Count -gt 0) {
        Write-Host ('Dependências alteradas: ' + ($deps -join ', '))
        if (-not (Confirm-Exact 'Digite INSTALAR para instalar as dependências alteradas' 'INSTALAR')) { throw 'Instalação de dependências não confirmada; atualização interrompida.' }
    }
    $python = Join-Path $script:Root '.venv\Scripts\python.exe'
    $manage = Join-Path $script:Root 'arion_geo\gerenciar.py'
    $applyMigrations = $false
    $oldEnv = $env:ARION_ENV
    try {
        $env:ARION_ENV = 'production'
        $plan = Invoke-Native $python @($manage, 'migrate', '--plan') 'Revisar plano de migrations'
        $hasPlan = $plan.Output -notmatch '(?i)no planned migration operations|no migrations to apply'
        $backupPath = $null
        if ($hasPlan -and $migrations.Count -gt 0) {
            $shown = $plan.Output -split "`n" | Where-Object { $_ -match '\.' }
            Write-Host 'Migrations pendentes:'
            $shown | ForEach-Object { Write-Host (Protect-Text $_) }
            $backupDir = Join-Path (Split-Path $script:Root -Parent) 'arion-backups'
            $beforeBackups = @()
            if (Test-Path -LiteralPath $backupDir) { $beforeBackups = @(Get-ChildItem -LiteralPath $backupDir -File -Filter 'arion-db-*.sql' | ForEach-Object { $_.FullName }) }
            [void](Invoke-Native 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $script:Root 'deploy\windows\backup-arion.ps1'), '-ProjectRoot', $script:Root, '-BackupDirectory', $backupDir, '-RetentionDays', '0') 'Criar backup MySQL pré-migration')
            $newBackups = @(Get-ChildItem -LiteralPath $backupDir -File -Filter 'arion-db-*.sql' | Where-Object { $beforeBackups -notcontains $_.FullName })
            if ($newBackups.Count -ne 1) { throw 'Não foi possível identificar exatamente o novo arquivo de backup.' }
            $backupPath = $newBackups[0].FullName
            $backup = Get-Item -LiteralPath $backupPath
            if ($backup.Length -le 0 -or -not (Select-String -LiteralPath $backupPath -Pattern '(?i)-- (MySQL|MariaDB) dump' -Quiet -TotalCount 20)) { throw 'O arquivo de backup não passou na validação de formato/tamanho.' }
            Write-Log "Backup MySQL validado: $($backup.Name), $($backup.Length) bytes."
            if (-not (Confirm-Exact 'Digite APLICAR MIGRATIONS para parar os serviços e aplicar o plano exibido' 'APLICAR MIGRATIONS')) { throw 'Aplicação de migrations não confirmada; serviços não foram parados.' }
            $applyMigrations = $true
        } elseif ($hasPlan) {
            throw 'Há migrations pendentes, mas nenhum arquivo de migration mudou neste fast-forward; atualização interrompida para revisão manual.'
        }
    } finally {
        if ($null -eq $oldEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $oldEnv }
    }
    Invoke-ArionScript 'stop-arion' @('-Role', 'worker')
    Invoke-ArionScript 'stop-arion' @('-Role', 'web')
    Assert-RoleState 'stopped'
    if ($pythonDependencyChange) {
        $requirements = Join-Path $script:Root 'requirements-prod.txt'
        [void](Invoke-Native $python @('-m', 'pip', 'install', '-r', $requirements) 'Instalar dependências Python')
    }
    if ($frontendDependencyChange) { [void](Invoke-Native 'npm.cmd' @('ci') 'Instalar dependências frontend') }
    if ($applyMigrations) {
        $oldMigrationEnv = $env:ARION_ENV
        try {
            $env:ARION_ENV = 'production'
            [void](Invoke-Native $python @($manage, 'migrate', '--noinput') 'Aplicar migrations confirmadas')
        } finally {
            if ($null -eq $oldMigrationEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $oldMigrationEnv }
        }
    }
    if ($frontend.Count -gt 0) {
        if ($changed | Where-Object { $_ -match '^(frontend_src/|package\.json$|package-lock\.json$)' }) { [void](Invoke-Native 'npm.cmd' @('run', 'build:production') 'Compilar frontend') }
        $oldStaticEnv = $env:ARION_ENV
        try {
            $env:ARION_ENV = 'production'
            [void](Invoke-Native $python @($manage, 'collectstatic', '--noinput') 'Coletar arquivos estáticos')
        } finally {
            if ($null -eq $oldStaticEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $oldStaticEnv }
        }
    }
    $oldCheckEnv = $env:ARION_ENV
    try {
        $env:ARION_ENV = 'production'
        [void](Invoke-Native $python @($manage, 'check') 'Verificação Django')
        if ($migrations.Count -gt 0) { [void](Invoke-Native $python @($manage, 'makemigrations', '--check', '--dry-run') 'Verificar consistência das migrations') }
    } finally {
        if ($null -eq $oldCheckEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $oldCheckEnv }
    }
    try {
        Invoke-ArionScript 'start-arion' @('-Role', 'web')
        Invoke-ArionScript 'start-arion' @('-Role', 'worker')
        Assert-RoleState 'running'
        if (-not (Test-WebReadiness -TimeoutSeconds 30)) { throw 'Web não confirmou /health/.' }
    } catch {
        Write-Log 'A inicialização após atualização falhou; solicitando parada dos processos ARION para evitar estado parcial.' 'ERRO'
        foreach ($role in @('worker', 'web')) {
            try { Invoke-ArionScript 'stop-arion' @('-Role', $role) }
            catch { Write-Log "Não foi possível confirmar a parada de $role após falha: $($_.Exception.Message)" 'ERRO' }
        }
        throw "Falha ao validar os serviços após atualização: $($_.Exception.Message)"
    }
    $newVersion = if (Test-Path $versionFile) { (Get-Content -Raw -LiteralPath $versionFile | ConvertFrom-Json).current_version } else { 'desconhecida' }
    Add-Step 'deploy' 'concluido' "commit=$after; versão=$newVersion; backup=$backupPath"
    Write-Log "Atualização concluída: $oldVersion → $newVersion ($after)." 'OK'
    Show-Status | Out-Null
}

function Show-Diagnostics {
    Assert-Project
    Write-Host '=== Estado dos serviços ==='
    Show-Status | Out-Null
    foreach ($file in @('arion-web.log', 'arion-worker.log')) {
        $path = Join-Path $script:Root ("var\logs\$file")
        Write-Host "=== $file (últimas 100 linhas) ==="
        if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Tail 100 | ForEach-Object { Write-Host (Protect-Text ([string]$_)) } }
        else { Write-Host 'Arquivo ainda não existe.' }
    }
}

function Start-Manager {
    Assert-Project
    $logDir = Join-Path $script:Root 'var\logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $script:LogPath = Join-Path $logDir ('manage-arion-{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $PID)
    $hash = (Get-Git @('rev-parse', '--short', 'HEAD') 'Commit atual' -AllowNonZero).Output.Trim()
    $versionPath = Join-Path $script:Root 'docs\08-releases\releases.json'
    $version = if (Test-Path $versionPath) { (Get-Content -Raw -LiteralPath $versionPath | ConvertFrom-Json).current_version } else { 'desconhecida' }
    Write-Log "ARION OPS | versão $version | commit $hash"
    if ($Action -eq 'Menu') {
        Show-Status | Out-Null
        while ($true) {
            Write-Host "`n========================================`n         ARION OPS - GERENCIADOR`n========================================`n`n [1] Iniciar todos os servicos`n [2] Parar todos os servicos`n [3] Reiniciar todos os servicos`n [4] Consultar status`n [5] Atualizar pelo GitHub e reiniciar`n [6] Verificar logs e diagnosticos`n [0] Sair`n`n========================================"
            $choice = Read-Host 'Selecione uma opção'
            if ($choice -eq '0') { break }
            $selected = @{ '1' = 'Start'; '2' = 'Stop'; '3' = 'Restart'; '4' = 'Status'; '5' = 'Update'; '6' = 'Diagnostics' }[$choice]
            if (-not $selected) { Write-Host 'Opção inválida.' -ForegroundColor Yellow; continue }
            $script:Steps.Clear()
            try { Invoke-Action $selected }
            catch { $script:HasError = $true; Write-Log $_.Exception.Message 'ERRO'; Add-Step $selected 'falha' $_.Exception.Message }
            finally { Exit-OperationLock }
            if ($selected -ne 'Status' -and $selected -ne 'Diagnostics') { Write-DeploymentReport $selected }
            Read-Host 'Pressione Enter para voltar ao menu' | Out-Null
        }
        if ($script:HasError) { throw 'Uma ou mais operações falharam durante esta sessão. Consulte os logs e relatórios.' }
        return
    }
    $script:Steps.Clear()
    Invoke-Action $Action
    if ($Action -ne 'Status' -and $Action -ne 'Diagnostics') { Write-DeploymentReport $Action }
}

function Invoke-Action([string]$Selected) {
    switch ($Selected) {
        'Start' { Start-AllServices }
        'Stop' { Stop-AllServices }
        'Restart' { Restart-AllServices }
        'Status' { Show-Status | Out-Null }
        'Update' { Invoke-Update }
        'Diagnostics' { Show-Diagnostics }
    }
}

function Write-DeploymentReport([string]$Operation) {
    $directory = Join-Path $script:Root 'var\logs'
    $commitResult = Get-Git -Arguments @('rev-parse', 'HEAD') -Label 'Commit para relatório' -AllowNonZero
    $versionFile = Join-Path $script:Root 'docs\08-releases\releases.json'
    $versionInfo = Get-Content -LiteralPath $versionFile -Raw | ConvertFrom-Json
    $report = [pscustomobject]@{
        operation = $Operation
        created_at = (Get-Date).ToString('o')
        commit = $commitResult.Output.Trim()
        version = $versionInfo.current_version
        steps = @($script:Steps)
    }
    $path = Join-Path $directory ('manage-arion-report-{0}-{1}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $PID)
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
    Write-Log "Relatório operacional: $path"
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Start-Manager; exit 0 }
    catch {
        if (-not $script:LogPath -and (Test-Path (Join-Path $script:Root 'var\logs'))) { $script:LogPath = Join-Path $script:Root 'var\logs\manage-arion-error.log' }
        Write-Log $_.Exception.Message 'ERRO'
        Add-Step $Action 'falha' $_.Exception.Message
        try { Write-DeploymentReport $Action } catch { Write-Log $_.Exception.Message 'ERRO' }
        exit 1
    }
    finally { Exit-OperationLock }
}
