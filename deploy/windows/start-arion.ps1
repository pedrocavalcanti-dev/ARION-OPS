param(
    [ValidateSet('web', 'worker')][string]$Role = 'web',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
$pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$runner = Join-Path $ProjectRoot 'deploy\windows\run_process.py'
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) { throw 'pythonw.exe da .venv não encontrado.' }
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
    throw 'An existing ARION supervisor did not confirm its service process within 30 seconds.'
}

$process = Start-Process -FilePath $pythonw -ArgumentList @('"' + $runner + '"', $Role) `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds(30)
$statusOutput = @()
do {
    if ($process.HasExited) {
        throw "ARION supervisor exited during startup (code $($process.ExitCode))."
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
throw 'The supervisor stayed active but did not confirm its service process within 30 seconds.'
