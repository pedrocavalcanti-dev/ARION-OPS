param(
    [ValidateSet('web', 'worker')][string]$Role = 'web',
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [ValidateSet('production', 'development')][string]$Environment = 'production'
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'stop-arion.ps1') -Role $Role -ProjectRoot $ProjectRoot
& (Join-Path $PSScriptRoot 'start-arion.ps1') -Role $Role -ProjectRoot $ProjectRoot -Environment $Environment
