[CmdletBinding()]
param(
    [ValidateSet('Menu', 'Start', 'Stop', 'Restart', 'Status', 'Update', 'Diagnostics', 'WebLogs', 'WorkerLogs', 'Health', 'ProductionCheck')]
    [string]$Action = 'Menu',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('all', 'web', 'worker')][string]$Role = 'all',
    [ValidateSet('production', 'development')][string]$Environment = 'production'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$script:Root = [IO.Path]::GetFullPath($ProjectRoot)
$script:LogPath = $null
$script:LockStream = $null
$script:Steps = New-Object System.Collections.Generic.List[object]
$script:HasError = $false

function Protect-Text([string]$Text) { return Protect-ArionText $Text }

function Write-Log([string]$Message, [string]$Level = 'INFO') {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, (Protect-Text $Message)
    $color = switch ($Level) { 'ERRO' { 'Red' } 'WARN' { 'Yellow' } 'OK' { 'Green' } default { 'Gray' } }
    Write-Host $line -ForegroundColor $color
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
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $File @Arguments 2>&1 | ForEach-Object { Protect-Text ([string]$_) })
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    foreach ($line in $lines) { if ($line) { Write-Log $line 'OUT' } }
    if ($code -ne 0 -and -not $AllowNonZero) { Add-Step $Label 'falha' "código $code"; throw "$Label falhou (código $code)." }
    Add-Step $Label $(if ($code -eq 0) { 'ok' } else { "codigo-$code" })
    return [pscustomobject]@{ Code = $code; Output = ($lines -join "`n") }
}

function Invoke-ArionScript([string]$Name, [hashtable]$Parameters = @{}) {
    $path = Join-Path $script:Root ("deploy\windows\{0}.ps1" -f $Name)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Script ausente: $path" }
    Write-Log "Executando $Name"
    try {
        $global:LASTEXITCODE = 0
        & $path @Parameters -ProjectRoot $script:Root
        if ($LASTEXITCODE -ne 0) { throw "Script retornou codigo $LASTEXITCODE." }
        Add-Step $Name 'ok'
    }
    catch { Add-Step $Name 'falha' $_.Exception.Message; throw "Etapa '$Name' falhou: $($_.Exception.Message)" }
}

function Get-RoleStatus([string]$Role) {
    $value = Get-ArionRoleStatus $script:Root $Role
    return [pscustomobject]@{ Role = $Role; State = $value.state; Code = $value.code
        SupervisorPid = $value.supervisor_pid; LauncherPid = $value.launcher_pid
        ChildPid = $value.child_pid; Detail = $value.detail; Restarts = $value.restarts }
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
    $versionFile = Join-Path $script:Root 'docs\08-releases\releases.json'
    $version = if (Test-Path $versionFile) { (Get-Content -Raw -Encoding UTF8 -LiteralPath $versionFile | ConvertFrom-Json).current_version } else { '?' }
    $hash = (Get-Git @('rev-parse', '--short', 'HEAD') 'Commit ativo' -AllowNonZero).Output.Trim()
    Write-Host "`nARION OPS | Gerenciador Windows 2.0" -ForegroundColor Cyan
    Write-Host "Versao $version | commit $hash | ambiente da operacao: $Environment"
    foreach ($item in $states) {
        $color = if ($item.State -eq 'running') { 'Green' } elseif ($item.State -eq 'failed') { 'Red' } else { 'Yellow' }
        Write-Host ('{0,-8} {1,-11} PID efetivo: {2} | supervisor: {3} | launcher: {4}' -f $item.Role, $item.State, $item.ChildPid, $item.SupervisorPid, $item.LauncherPid) -ForegroundColor $color
        if ($item.Detail) { Write-Host (Protect-Text $item.Detail) -ForegroundColor Yellow }
    }
    $health = Test-WebReadiness
    Write-Host "Health Check: $(if ($health) { 'OK' } else { 'indisponivel' })"
    $service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
    Write-Host "Cloudflared: $(if ($service) { $service.Status } else { 'nao instalado' }) (somente consulta)"
    $lastPath = Join-Path $script:Root 'var\run\manage-arion-last.json'
    if (Test-Path -LiteralPath $lastPath) {
        try {
            $last = Get-Content -LiteralPath $lastPath -Raw -Encoding UTF8 | ConvertFrom-Json
            Write-Host (Protect-Text "Ultima operacao: $($last.action) / $($last.role) - $($last.result) em $($last.at)")
        } catch { Write-Host 'Ultimo resultado indisponivel.' -ForegroundColor Yellow }
    }
    Write-Host "Consulta: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    return $states
}

function Assert-SingleRoleState([string]$Role, [string]$Expected) {
    $state = Get-RoleStatus $Role
    if ($state.State -ne $Expected) { throw "$Role=$($state.State); esperado $Expected. Consulte os logs e use Restart apos corrigir a causa." }
}
function Assert-RoleState([string]$Expected) {
    foreach ($item in @('web', 'worker')) { Assert-SingleRoleState $item $Expected }
}
function Start-OrderedServices {
    Invoke-ArionScript 'start-arion' @{ Role = 'web'; Environment = $Environment }
    Assert-SingleRoleState 'web' 'running'
    if (-not (Test-WebReadiness)) { throw 'Web sem /health/ valido; Worker nao sera iniciado.' }
    Invoke-ArionScript 'start-arion' @{ Role = 'worker'; Environment = $Environment }
    Assert-RoleState 'running'
}
function Start-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    try { Start-OrderedServices }
    catch {
        Write-Log 'Partida incompleta; os servicos saudaveis foram preservados. Consulte Status e logs; corrija e reinicie somente o servico afetado.' 'ERRO'
        throw
    }
    Show-Status | Out-Null
    Assert-RoleState 'running'
    Write-Log 'Web e Worker confirmados.' 'OK'
}
function Stop-OrderedServices {
    Invoke-ArionScript 'stop-arion' @{ Role = 'worker' }
    Assert-SingleRoleState 'worker' 'stopped'
    Invoke-ArionScript 'stop-arion' @{ Role = 'web' }
    Assert-SingleRoleState 'web' 'stopped'
}
function Stop-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    Stop-OrderedServices
    Write-Log 'Web e Worker encerrados.' 'OK'
    Show-Status | Out-Null
}
function Restart-AllServices {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    Stop-OrderedServices
    Start-OrderedServices
    Show-Status | Out-Null
    Assert-RoleState 'running'
    Write-Log 'Reinicio concluido.' 'OK'
}
function Invoke-SingleService([string]$Operation, [string]$SelectedRole) {
    Assert-Project
    Assert-AuthorizedOperator
    Enter-OperationLock
    if ($Operation -in @('Stop', 'Restart')) {
        Invoke-ArionScript 'stop-arion' @{ Role = $SelectedRole }
        Assert-SingleRoleState $SelectedRole 'stopped'
    }
    if ($Operation -in @('Start', 'Restart')) {
        Invoke-ArionScript 'start-arion' @{ Role = $SelectedRole; Environment = $Environment }
        Assert-SingleRoleState $SelectedRole 'running'
        if ($SelectedRole -eq 'web' -and -not (Test-WebReadiness)) { throw 'Web nao confirmou Health Check.' }
    }
    Show-Status | Out-Null
    Write-Log "$Operation de $SelectedRole concluido; outro servico preservado." 'OK'
}

function Get-Git([string[]]$Arguments, [string]$Label, [switch]$AllowNonZero) {
    return Invoke-Native 'git.exe' (@('-C', $script:Root) + $Arguments) $Label -AllowNonZero:$AllowNonZero
}

function Confirm-Exact([string]$Prompt, [string]$Expected) {
    $answer = Read-Host $Prompt
    return ($answer -ceq $Expected)
}

function Invoke-Update {
    Push-Location -LiteralPath $script:Root
    try { Invoke-UpdateCore }
    catch {
        Write-Log 'Atualização interrompida. Não houve restauração automática do MySQL nem reinício após validação falha.' 'ERRO'
        try {
            foreach ($role in @('web', 'worker')) {
                $state = Get-RoleStatus $role
                Write-Log ("Estado após falha: {0}={1}" -f $role, $state.State) 'WARN'
            }
        } catch { Write-Log 'Não foi possível confirmar o estado dos serviços; confira status-arion.ps1 antes de qualquer recuperação.' 'ERRO' }
        Write-Log 'Corrija a falha, execute production-check.ps1 e use manage-arion.ps1 -Action Start; valide /health/ e acesso autenticado. Não repita migrations já aplicadas.' 'WARN'
        throw
    }
    finally { Pop-Location }
}

function Invoke-UpdateCore {
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
    # Build and reference checks must fail while the existing services are still running.
    if ($frontendDependencyChange) { [void](Invoke-Native 'npm.cmd' @('ci') 'Instalar dependências frontend') }
    if ($frontend.Count -gt 0) { [void](Invoke-Native 'npm.cmd' @('run', 'build:production') 'Compilar frontend antes da parada') }
    $preflightEnv = $env:ARION_ENV
    try {
        $env:ARION_ENV = 'production'
        [void](Invoke-Native $python @((Join-Path $script:Root 'deploy\windows\production_check.py'), '--build-only') 'Preflight de assets antes da parada')
    } finally {
        if ($null -eq $preflightEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $preflightEnv }
    }
    Invoke-ArionScript 'stop-arion' @{ Role = 'worker' }
    Invoke-ArionScript 'stop-arion' @{ Role = 'web' }
    Assert-RoleState 'stopped'
    $leaseEnv = $env:ARION_ENV
    try {
        $env:ARION_ENV = 'production'
        [void](Invoke-Native $python @((Join-Path $script:Root 'deploy\windows\run_process.py'), '--wait-lease', 'worker') 'Aguardar lease anterior sem alterar banco')
    } finally {
        if ($null -eq $leaseEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $leaseEnv }
    }
    if ($pythonDependencyChange) {
        $requirements = Join-Path $script:Root 'requirements-prod.txt'
        [void](Invoke-Native $python @('-m', 'pip', 'install', '-r', $requirements) 'Instalar dependências Python')
    }
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
        $oldStaticEnv = $env:ARION_ENV
        try {
            $env:ARION_ENV = 'production'
            [void](Invoke-Native $python @($manage, 'collectstatic', '--noinput', '--clear') 'Coletar arquivos estáticos')
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
    Invoke-ArionScript 'production-check'
    try {
        Start-OrderedServices
        Assert-RoleState 'running'
        if (-not (Test-WebReadiness -TimeoutSeconds 30)) { throw 'Web não confirmou /health/.' }
    } catch {
        Write-Log 'Atualizacao com partida incompleta; servicos saudaveis preservados. Consulte Status e reinicie somente o servico afetado.' 'ERRO'
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

function Show-RoleLogs([string]$SelectedRole) {
    $logs = Join-Path $script:Root 'var\logs'
    $files = @((Join-Path $logs "arion-$SelectedRole.log"))
    $files += @(Get-ChildItem -LiteralPath $logs -Filter "arion-$SelectedRole-startup.*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 2 -ExpandProperty FullName)
    foreach ($file in $files) {
        Write-Host "=== $([IO.Path]::GetFileName($file)) ===" -ForegroundColor Cyan
        if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file -Encoding UTF8 -Tail 80 | ForEach-Object { Write-Host (Protect-Text ([string]$_)) } }
    }
}
function Save-OperationResult([string]$Selected, [string]$SelectedRole, [string]$Result) {
    $run = Join-Path $script:Root 'var\run'
    New-Item -ItemType Directory -Force -Path $run | Out-Null
    $value = [pscustomobject]@{ action = $Selected; role = $SelectedRole; result = $Result; at = (Get-Date).ToString('o') }
    $path = Join-Path $run 'manage-arion-last.json'
    $temporary = "$path.$PID.tmp"
    $value | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $path -Force
}
function Invoke-RecordedAction([string]$Selected, [string]$SelectedRole) {
    $script:Steps.Clear()
    try {
        Invoke-Action $Selected $SelectedRole
        Save-OperationResult $Selected $SelectedRole 'OK'
    } catch {
        Save-OperationResult $Selected $SelectedRole 'FALHOU'
        Write-Log $_.Exception.Message 'ERRO'
        Add-Step $Selected 'falha' $_.Exception.Message
        throw
    } finally {
        Exit-OperationLock
        if ($Selected -in @('Start', 'Stop', 'Restart', 'Update')) { Write-DeploymentReport "$Selected-$SelectedRole" }
    }
}
function Start-Manager {
    Assert-Project
    $logDir = Join-Path $script:Root 'var\logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $script:LogPath = Join-Path $logDir ('manage-arion-{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'), $PID)
    if ($Action -ne 'Menu') { Invoke-RecordedAction $Action $Role; return }
    $choices = @{
        '1' = @('Start','all'); '2' = @('Stop','all'); '3' = @('Restart','all')
        '4' = @('Start','web'); '5' = @('Stop','web'); '6' = @('Restart','web')
        '7' = @('Start','worker'); '8' = @('Stop','worker'); '9' = @('Restart','worker')
        '10' = @('Status','all'); '11' = @('WebLogs','web'); '12' = @('WorkerLogs','worker')
        '13' = @('Health','all'); '14' = @('ProductionCheck','all')
    }
    while ($true) {
        Show-Status | Out-Null
        Write-Host @"

  1 Iniciar todos           2 Parar todos            3 Reiniciar todos
  4 Iniciar Web             5 Parar Web              6 Reiniciar Web
  7 Iniciar Worker          8 Parar Worker           9 Reiniciar Worker
 10 Status completo        11 Logs Web              12 Logs Worker
 13 Health Check           14 Verificacoes producao  0 Sair
"@ -ForegroundColor Cyan
        $choice = Read-Host 'Selecione uma opcao'
        if ($choice -eq '0') { return }
        if (-not $choices.ContainsKey($choice)) { Write-Host 'Opcao invalida.' -ForegroundColor Yellow; continue }
        $selection = $choices[$choice]
        try { Invoke-RecordedAction $selection[0] $selection[1] }
        catch { Write-Host 'Operacao falhou. Consulte o status e os logs antes de repetir.' -ForegroundColor Red }
        Read-Host 'Enter para voltar ao menu' | Out-Null
    }
}
function Invoke-Action([string]$Selected, [string]$SelectedRole = 'all') {
    if ($Selected -in @('Start', 'Stop', 'Restart') -and $SelectedRole -ne 'all') {
        Invoke-SingleService $Selected $SelectedRole
        return
    }
    switch ($Selected) {
        'Start' { Start-AllServices }
        'Stop' { Stop-AllServices }
        'Restart' { Restart-AllServices }
        'Status' { Show-Status | Out-Null }
        'Update' { Invoke-Update }
        'Diagnostics' { Show-Diagnostics }
        'WebLogs' { Show-RoleLogs 'web' }
        'WorkerLogs' { Show-RoleLogs 'worker' }
        'Health' { if (-not (Test-WebReadiness)) { throw 'Health Check indisponivel.' }; Write-Log 'Health Check: OK' 'OK' }
        'ProductionCheck' { Invoke-ArionScript 'production-check' }
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
        steps = $script:Steps.ToArray()
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
