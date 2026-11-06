$ErrorActionPreference = 'Stop'
$manager = Join-Path $PSScriptRoot 'manage-arion.ps1'
$script:Root = [IO.Path]::GetFullPath((Join-Path $env:TEMP ('arion-manager-test-' + [guid]::NewGuid().ToString('N'))))
$script:Calls = New-Object System.Collections.Generic.List[string]
$script:FailOn = ''
$script:GitResponses = @{}
. $manager -ProjectRoot $script:Root
$script:RealLock = (Get-Command Enter-OperationLock).ScriptBlock

function Assert-Project {}
function Assert-AuthorizedOperator {}
function Enter-OperationLock {}
function Assert-RoleState([string]$Expected) { $script:Calls.Add("state:$Expected") }
function Show-Status { $script:Calls.Add('status') }
function Test-WebReadiness([int]$TimeoutSeconds = 0) { $script:Calls.Add('web-health'); return $true }
function Get-Git([string[]]$Arguments, [string]$Label, [switch]$AllowNonZero) {
    $key = $Arguments -join ' '
    $script:Calls.Add("git:$key")
    return [pscustomobject]@{ Code = 0; Output = [string]$script:GitResponses[$key] }
}
function Invoke-ArionScript([string]$Name, [string[]]$Arguments = @()) {
    $role = $Arguments[1]
    $script:Calls.Add("${Name}:$role")
    if ($script:FailOn -eq "${Name}:$role") { throw 'falha simulada' }
}

function Reset-Calls { $script:Calls.Clear(); $script:FailOn = '' }
function Assert-Sequence([string]$Name, [string[]]$Expected) {
    $actual = @($script:Calls)
    if (($actual -join '|') -ne ($Expected -join '|')) { throw "${Name}: esperado [$($Expected -join ', ')], recebido [$($actual -join ', ')]" }
    Write-Output "OK: $Name"
}

try {
    Reset-Calls
    Start-AllServices
    Assert-Sequence 'start: Web antes de Worker, com confirmação final' @('start-arion:web', 'start-arion:worker', 'state:running', 'web-health', 'status')

    Reset-Calls
    Stop-AllServices
    Assert-Sequence 'stop: Worker antes de Web, com confirmação final' @('stop-arion:worker', 'stop-arion:web', 'state:stopped', 'status')

    Reset-Calls
    Restart-AllServices
    Assert-Sequence 'restart: parada Worker/Web e partida Web/Worker' @('stop-arion:worker', 'stop-arion:web', 'state:stopped', 'start-arion:web', 'start-arion:worker', 'state:running', 'web-health', 'status')

    Reset-Calls
    $script:FailOn = 'start-arion:web'
    $failed = $false
    try { Start-AllServices } catch { $failed = $true }
    if (-not $failed -or @($script:Calls) -contains 'start-arion:worker') { throw 'Falha de Web deve interromper a inicialização antes do Worker.' }
    Write-Output 'OK: erro interrompe a ordem e não inicia o serviço seguinte'

    New-Item -ItemType Directory -Path (Join-Path $script:Root 'var\run') -Force | Out-Null
    Reset-Calls
    & $script:RealLock
    $secondLockRejected = $false
    try { & $script:RealLock } catch { $secondLockRejected = $true }
    if (-not $secondLockRejected) { throw 'O lock deve rejeitar uma segunda operação simultânea.' }
    $script:LockStream.Dispose(); $script:LockStream = $null
    Write-Output 'OK: lock exclusivo impede operação simultânea'

    Reset-Calls
    $script:GitResponses = @{ 'branch --show-current' = 'feature'; 'status --porcelain=v1' = '' }
    $failed = $false
    try { Invoke-Update } catch { $failed = $true }
    if (-not $failed -or @($script:Calls) -contains 'git:fetch origin main') { throw 'Atualização deve rejeitar branch diferente de main antes do fetch.' }
    Write-Output 'OK: atualização recusa branch diferente de main antes de buscar código'

    Reset-Calls
    $script:GitResponses = @{ 'branch --show-current' = 'main'; 'status --porcelain=v1' = ' M .env' }
    $failed = $false
    try { Invoke-Update } catch { $failed = $true }
    if (-not $failed -or @($script:Calls) -contains 'git:fetch origin main') { throw 'Atualização deve preservar alterações locais e parar antes do fetch.' }
    Write-Output 'OK: atualização preserva alterações locais e recusa fetch'

    $sanitized = Protect-Text 'password=demo-value token:sample-value https://user:pass@example.invalid/path'
    if ($sanitized -match 'demo-value|sample-value|user:pass') { throw 'O sanitizador deixou um valor sensível na mensagem.' }
    Write-Output 'OK: sanitização remove valores sensíveis de logs'

    Reset-Calls
    Invoke-Action 'Status'
    Assert-Sequence 'status não inicia nem encerra processos' @('status')

    Reset-Calls
    Invoke-Action 'Diagnostics'
    Assert-Sequence 'diagnósticos não iniciam nem encerram processos' @('status')
}
finally {
    if ($script:LockStream) { $script:LockStream.Dispose() }
    $tempBase = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    $resolvedRoot = [IO.Path]::GetFullPath($script:Root)
    if (-not $resolvedRoot.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolvedRoot) -notmatch '^arion-manager-test-[a-f0-9]{32}$') {
        throw 'Diretório temporário de teste fora do padrão esperado; limpeza cancelada.'
    }
    if (Test-Path -LiteralPath $resolvedRoot) { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
