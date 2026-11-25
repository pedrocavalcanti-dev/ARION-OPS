param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$manage = Join-Path $ProjectRoot 'arion_geo\gerenciar.py'
$checker = Join-Path $ProjectRoot 'deploy\windows\production_check.py'
$logRoot = Join-Path $ProjectRoot 'var\logs'
foreach ($required in @($python, $manage, $checker)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'Pré-requisito do production-check ausente.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env') -PathType Leaf)) {
    throw 'Crie .env a partir de .env.example e configure os valores locais.'
}
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$oldArionEnv = $env:ARION_ENV
$oldSettingsModule = $env:DJANGO_SETTINGS_MODULE
try {
    $env:ARION_ENV = 'production'
    $env:DJANGO_SETTINGS_MODULE = 'config.settings'
    Push-Location (Join-Path $ProjectRoot 'arion_geo')
    try {
        $result = Invoke-ArionPython $ProjectRoot @($checker)
        Write-Output $result.Output
        $result = Invoke-ArionPython $ProjectRoot @($manage, 'check', '--deploy')
        Write-Output $result.Output
    } finally { Pop-Location }
} finally {
    if ($null -eq $oldArionEnv) { Remove-Item Env:ARION_ENV -ErrorAction SilentlyContinue } else { $env:ARION_ENV = $oldArionEnv }
    if ($null -eq $oldSettingsModule) { Remove-Item Env:DJANGO_SETTINGS_MODULE -ErrorAction SilentlyContinue } else { $env:DJANGO_SETTINGS_MODULE = $oldSettingsModule }
}
