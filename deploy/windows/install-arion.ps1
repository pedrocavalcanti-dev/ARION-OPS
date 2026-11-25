param(
    [switch]$ApplyMigrations,
    [switch]$InstallTasks,
    [string]$ServiceAccount,
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'arion-common.ps1')
foreach ($role in @('web', 'worker')) {
    if ((Get-ArionRoleStatus $ProjectRoot $role).locked) {
        throw 'Preparacao de instalacao exige Web e Worker parados antes de modificar assets ou dependencias.'
    }
}
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$manage = Join-Path $ProjectRoot 'arion_geo\gerenciar.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $manage -PathType Leaf)) {
    throw 'Python da .venv ou entrypoint Django não encontrado.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env') -PathType Leaf)) {
    throw 'Crie .env a partir de .env.example e configure os segredos localmente.'
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$manifest = Join-Path $ProjectRoot 'var\arion-frontend-manifest.json'
if ($npm) {
    Push-Location $ProjectRoot
    try {
        & $npm.Source ci
        if ($LASTEXITCODE -ne 0) { throw 'Instalação do frontend falhou.' }
        & $npm.Source run build:production
        if ($LASTEXITCODE -ne 0) { throw 'Build de produção do frontend falhou.' }
    } finally { Pop-Location }
} elseif (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw 'Node/npm não disponível e build de frontend não encontrado.'
}

if ($ApplyMigrations) {
    $confirmation = Read-Host 'Digite APLICAR MIGRATIONS ARION para confirmar alterações no banco local configurado'
    if ($confirmation -cne 'APLICAR MIGRATIONS ARION') { throw 'Confirmação não corresponde; nenhuma migration foi aplicada.' }
    & $python $manage migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Migrations falharam.' }
}

& $python $manage collectstatic --noinput
if ($LASTEXITCODE -ne 0) { throw 'collectstatic falhou.' }
& (Join-Path $PSScriptRoot 'production-check.ps1') -ProjectRoot $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw 'Production check falhou.' }

if ($InstallTasks) {
    if (-not $ServiceAccount) { throw 'Informe -ServiceAccount para instalar as tarefas.' }
    & (Join-Path $PSScriptRoot 'install-tasks.ps1') -ProjectRoot $ProjectRoot -ServiceAccount $ServiceAccount
}
Write-Output 'Preparação local concluída. FULL CMDB/OTRS, Tunnel, domínio e smoke test seguem o roteiro manual.'
