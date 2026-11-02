param(
    [switch]$Apply,
    [string]$MyIniPath = ""
)

$ErrorActionPreference = 'Stop'

Write-Output "================================================================="
Write-Output " ARION OPS - Diagnostico de Hardening do MySQL Local"
Write-Output "================================================================="

$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(3306, 33060) } |
    Select-Object LocalAddress, LocalPort, OwningProcess

if ($listeners) {
    foreach ($item in $listeners) {
        $isLoopback = $item.LocalAddress -in @('127.0.0.1', '::1')
        $status = if ($isLoopback) { "[OK]" } else { "[WARN]" }
        Write-Output ("{0} Porta {1} em escuta em {2} (PID {3})" -f $status, $item.LocalPort, $item.LocalAddress, $item.OwningProcess)
    }
} else {
    Write-Output "[INFO] Nenhuma porta 3306/33060 detectada em estado LISTEN."
}

try {
    $profiles = Get-NetFirewallProfile -ErrorAction Stop
    foreach ($prof in $profiles) {
        $fwStatus = if ($prof.Enabled -and $prof.DefaultInboundAction -in @('Block', 'NotConfigured')) { "[OK]" } else { "[WARN]" }
        Write-Output ("{0} Firewall perfil {1}: Enabled={2}, DefaultInboundAction={3}" -f $fwStatus, $prof.Name, $prof.Enabled, $prof.DefaultInboundAction)
    }
} catch {
    Write-Output "[INFO] Nao foi possivel consultar os perfis do Windows Firewall sem privilegios elevados."
}

$service = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^MySQL' -or $_.PathName -match 'mysqld' } |
    Select-Object -First 1

if (-not $MyIniPath -and $service -and ($service.PathName -match '--defaults-file="([^"]+)"')) {
    $MyIniPath = $Matches[1]
}

if ($MyIniPath -and (Test-Path -LiteralPath $MyIniPath -PathType Leaf)) {
    Write-Output ("[INFO] Arquivo de configuracao localizado: {0}" -f $MyIniPath)
    $bindLines = Select-String -Path $MyIniPath -Pattern '^\s*(bind-address|mysqlx-bind-address|mysqlx)\s*=' -ErrorAction SilentlyContinue
    if ($bindLines) {
        foreach ($line in $bindLines) {
            Write-Output ("[INFO] Configuracao atual: {0}" -f $line.Line.Trim())
        }
    } else {
        Write-Output "[WARN] bind-address explicito nao encontrado em my.ini (MySQL assume * / 0.0.0.0 por padrao)."
    }
} else {
    Write-Output "[INFO] Caminho do my.ini nao detectado automaticamente. Informe -MyIniPath se desejar inspecionar o arquivo."
}

Write-Output ""
Write-Output "Recomendacao operacional para instalacao local do ARION OPS:"
Write-Output "  [mysqld]"
Write-Output "  bind-address=127.0.0.1"
Write-Output "  mysqlx-bind-address=127.0.0.1"
Write-Output ""

if (-not $Apply) {
    Write-Output "Modo somente leitura concluido. Nenhuma alteracao foi aplicada."
    Write-Output "Para preparar backup em janela de manutencao (Administrador): .\deploy\windows\hardening-mysql.ps1 -Apply"
    exit 0
}

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Execute este script como Administrador para utilizar a opcao -Apply."
}
if (-not $MyIniPath -or -not (Test-Path -LiteralPath $MyIniPath -PathType Leaf)) {
    throw "Informe um caminho valido em -MyIniPath para aplicar o ajuste."
}

$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$backupPath = "{0}.arion-backup-{1}.bak" -f $MyIniPath, $stamp
Copy-Item -LiteralPath $MyIniPath -Destination $backupPath -Force
Write-Output ("[OK] Backup criado em: {0}" -f $backupPath)
Write-Output ("[INFO] Adicione bind-address=127.0.0.1 sob [mysqld] e reinicie o servico {0} em janela programada." -f $service.Name)
