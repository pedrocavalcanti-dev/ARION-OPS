param(
    [ValidateSet('web', 'worker')][string]$Role = 'web',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$runner = Join-Path $ProjectRoot 'deploy\windows\run_process.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'Supervisor do ARION não encontrado.' }

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python da .venv nao encontrado.' }
$existingOutput = & $python $runner --status $Role 2>&1
$existingCode = $LASTEXITCODE
if ($existingCode -eq 0) {
    $existingOutput
    return
}
if ($existingCode -eq 2) {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $existingOutput = & $python $runner --status $Role 2>&1
        $existingCode = $LASTEXITCODE
        if ($existingCode -eq 0) {
            $existingOutput
            return
        }
        if ($existingCode -eq 1) { break }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    $existingOutput
    throw 'O supervisor ARION existente não confirmou o processo e, no Web, a prontidão do Waitress na porta configurada em 30 segundos.'
}

$startupLogs = Join-Path $ProjectRoot 'var\logs'
New-Item -ItemType Directory -Path $startupLogs -Force | Out-Null
$startupOut = Join-Path $startupLogs "arion-$Role-startup.out.log"
$startupErr = Join-Path $startupLogs "arion-$Role-startup.err.log"
# A separate hidden console process retains working standard streams and
# survives the invoking terminal. pythonw can discard startup diagnostics.
$process = Start-Process -FilePath $python -ArgumentList @(('"' + $runner + '"'), $Role) `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $startupOut -RedirectStandardError $startupErr
$deadline = [DateTime]::UtcNow.AddSeconds(30)
$statusOutput = @()
do {
    if ($process.HasExited) {
        throw "ARION supervisor exited during startup (code $($process.ExitCode)). Consulte var\logs\arion-$Role-startup.err.log e arion-$Role.log."
    }
    $statusOutput = & $python $runner --status $Role 2>&1
    $statusCode = $LASTEXITCODE
    if ($statusCode -eq 0) {
        $statusOutput
        return
    }
    Start-Sleep -Seconds 1
} while ([DateTime]::UtcNow -lt $deadline)
$statusOutput
throw 'O supervisor permaneceu ativo, mas não confirmou o processo e, no Web, a prontidão do Waitress na porta configurada em 30 segundos.'
