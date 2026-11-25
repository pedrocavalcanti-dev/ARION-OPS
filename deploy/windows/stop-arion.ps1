param(
    [ValidateSet('web', 'worker')][string]$Role = 'web',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$control = Enter-ArionRoleControl $ProjectRoot $Role
try {
    $runner = Join-Path $ProjectRoot 'deploy\windows\run_process.py'
    $result = Invoke-ArionPython $ProjectRoot @($runner, '--stop', $Role)
    Write-Output $result.Output
    $status = Get-ArionRoleStatus $ProjectRoot $Role
    if ($status.state -ne 'stopped') { throw "$Role nao confirmou encerramento: $($status.state)." }
} finally { $control.Dispose() }
