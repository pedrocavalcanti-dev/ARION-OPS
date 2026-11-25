param(
    [Parameter(Mandatory = $true)][string]$ServiceAccount,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env') -PathType Leaf)) {
    throw 'Crie e proteja o arquivo .env antes de instalar as tarefas.'
}
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$runner = Join-Path $ProjectRoot 'deploy\windows\run_process.py'
foreach ($required in @($python, $runner)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw 'Pré-requisito do supervisor ausente.' }
}

$runRoot = Join-Path $ProjectRoot 'var\run'
$logRoot = Join-Path $ProjectRoot 'var\logs'
$mediaRoot = Join-Path $ProjectRoot 'var\private_media'
$staticRoot = Join-Path $ProjectRoot 'var\staticfiles'
foreach ($directory in @($runRoot, $logRoot, $mediaRoot, $staticRoot)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$account = Get-Credential -UserName $ServiceAccount -Message 'Credencial da conta dedicada do ARION (usada por Web e Worker).'
if ($account.UserName -ine $ServiceAccount) { throw 'A conta informada não corresponde à conta solicitada.' }
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($account.Password)
try {
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $actions = @(
        @{ Name = 'ARION-Web'; Role = 'web' },
        @{ Name = 'ARION-Integration-Worker'; Role = 'worker' }
    )
    $registeredTasks = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $actions) {
        if (Get-ScheduledTask -TaskName $item.Name -ErrorAction SilentlyContinue) {
            throw "A tarefa $($item.Name) já existe; nenhuma tarefa foi sobrescrita."
        }
    }
    foreach ($item in $actions) {
        $arguments = '"{0}" {1}' -f $runner, $item.Role
        if ($item.Role -eq 'worker') { $arguments += ' --wait-for-web' }
        $action = New-ScheduledTaskAction -Execute $python `
            -Argument $arguments -WorkingDirectory $ProjectRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
        Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $trigger `
            -Settings $settings -User $ServiceAccount -Password $plainPassword `
            -Description "ARION production $($item.Role) process supervisor" | Out-Null
        $registeredTasks.Add($item.Name)
    }
} catch {
    foreach ($registeredTask in $registeredTasks) {
        Unregister-ScheduledTask -TaskName $registeredTask -Confirm:$false -ErrorAction SilentlyContinue
    }
    throw
} finally {
    if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $plainPassword = $null
    $account = $null
}

$identity = [System.Security.Principal.NTAccount]::new($ServiceAccount)
$sid = $identity.Translate([System.Security.Principal.SecurityIdentifier]).Value
$systemSid = 'S-1-5-18'
$adminsSid = 'S-1-5-32-544'
foreach ($directory in @($runRoot, $logRoot, $mediaRoot)) {
    & icacls.exe $directory '/inheritance:r' '/grant:r' `
        "*${sid}:(OI)(CI)M" "*${systemSid}:(OI)(CI)F" "*${adminsSid}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível proteger um diretório operacional do ARION.' }
}
foreach ($secretFile in @((Join-Path $ProjectRoot '.env'), (Join-Path $ProjectRoot 'var\database-config.json'))) {
    if (Test-Path -LiteralPath $secretFile -PathType Leaf) {
        & icacls.exe $secretFile '/inheritance:r' '/grant:r' `
            "*${sid}:R" "*${systemSid}:F" "*${adminsSid}:F" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Não foi possível proteger um arquivo local de configuração.' }
    }
}

Start-ScheduledTask -TaskName 'ARION-Web'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$null = Wait-ArionRoleReady $ProjectRoot 'web'
Start-ScheduledTask -TaskName 'ARION-Integration-Worker'
$null = Wait-ArionRoleReady $ProjectRoot 'worker'
Write-Output 'Tarefas ARION-Web e ARION-Integration-Worker instaladas; prontidao confirmada.'
