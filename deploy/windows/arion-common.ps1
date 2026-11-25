# Shared controls for Windows PowerShell 5.1 and PowerShell 7.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
function Protect-ArionText([string]$Text) {
    if ($null -eq $Text) { return '' }
    $safe = $Text -replace '(?i)\b(password|passwd|senha|token|access_token|refresh_token|secret_key|secret|client_secret|api_key|authorization|cookie|database_url)(\s*[=:]\s*|\s+)("[^"]*"|''[^'']*''|[^\s;,&]+)', '$1=[REDACTED]'
    $safe = $safe -replace '(?i)(://[^:/\s]+:)[^@/\s]+@', '$1[REDACTED]@'
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        if ($entry.Key -match '(?i)secret|password|token|credential|database_url|api_key|private_key' -and $entry.Value) {
            $safe = $safe.Replace([string]$entry.Value, '[REDACTED]')
        }
    }
    return $safe
}
function Invoke-ArionPython([string]$Root, [string[]]$Arguments, [switch]$AllowNonZero) {
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python da .venv nao encontrado.' }
    # PS 5.1 turns native stderr into ErrorRecords; honor exit codes instead.
    $previous = $ErrorActionPreference
    $previousExitCode = [int]$global:LASTEXITCODE
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $python @Arguments 2>&1 | ForEach-Object { Protect-ArionText ([string]$_) })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        # Status 1 means Stopped, not failure of the caller script's Stop.
        $global:LASTEXITCODE = $previousExitCode
    }
    if ($code -ne 0 -and -not $AllowNonZero) {
        foreach ($line in $lines) { Write-Host $line -ForegroundColor Red }
        throw "Comando Python falhou (codigo $code). Consulte os logs locais sanitizados."
    }
    return [pscustomobject]@{ Code = $code; Output = ($lines -join "`n") }
}
function Get-ArionRoleStatus([string]$Root, [string]$Role) {
    $runner = Join-Path $Root 'deploy\windows\run_process.py'
    $result = Invoke-ArionPython $Root @($runner, '--status', $Role, '--json') -AllowNonZero
    try { $value = $result.Output | ConvertFrom-Json -ErrorAction Stop }
    catch { throw "Status invalido de $Role; consulte arion-$Role.log e startup.*.log." }
    if ($value.role -ne $Role -or $value.code -ne $result.Code) { throw "Status inconsistente de $Role." }
    return $value
}
function Enter-ArionRoleControl([string]$Root, [string]$Role) {
    $run = Join-Path $Root 'var\run'
    New-Item -ItemType Directory -Force -Path $run | Out-Null
    try { return [IO.File]::Open((Join-Path $run "$Role.control.lock"), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw "Outra operacao de $Role esta em andamento; aguarde e consulte o status." }
}
function Wait-ArionRoleReady([string]$Root, [string]$Role, [double]$LaunchedAt = 0) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $identity = ''
    $confirmed = 0
    do {
        $status = Get-ArionRoleStatus $Root $Role
        $current = '{0}:{1}:{2}' -f $status.supervisor_pid, $status.child_pid, $status.restarts
        if ($status.state -eq 'running') {
            if ($identity -eq $current) { $confirmed++ } else { $confirmed = 1; $identity = $current }
            if ($confirmed -ge 3) { return $status }
        } else {
            $confirmed = 0
            if ($status.state -eq 'failed' -and $status.updated_at -ge $LaunchedAt) { break }
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Role nao confirmou prontidao estavel (estado: $($status.state)). Consulte arion-$Role.log e startup.*.log; use Restart apos corrigir a causa."
}
