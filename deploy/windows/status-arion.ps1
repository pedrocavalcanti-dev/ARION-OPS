param([string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
$problem = $false
foreach ($role in @('web', 'worker')) {
    $status = Get-ArionRoleStatus $ProjectRoot $role
    Write-Output "$role`: $($status.state) (supervisor pid $($status.supervisor_pid), launcher pid $($status.launcher_pid), child pid $($status.child_pid)); exit=$($status.last_exit_code); reinicios=$($status.restarts)"
    if ($status.state -ne 'running') { $problem = $true }
}
$service = Get-Service -Name Cloudflared -ErrorAction SilentlyContinue
Write-Output "Cloudflared: $(if ($service) { $service.Status } else { 'Not installed' }) (somente consulta)"
Write-Output "Consulta: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
if ($problem) { exit 2 }
