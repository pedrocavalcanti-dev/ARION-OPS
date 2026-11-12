# Atualização GEO Intelligence — Windows Server

Esta entrega não altera a versão registrada nem a tag `v1.4.0`. Nenhum deploy foi executado pelo agente. Homologar migrations e testes focados no MySQL DEV `arion_geo_dev` antes de autorizar produção. Credenciais/plano meteorológico e DPAPI do usuário de serviço exigem configuração no ambiente de destino.

## Preparação

Registre o commit atualmente implantado (`git rev-parse HEAD`) e o estado das tarefas/supervisores. A base local anterior a esta entrega era `b961b18`. Preserve `.env`, configuração de banco e identidade Windows que protege as credenciais. A coleta Open-Meteo não inicia habilitada e não exige nova variável secreta no `.env`.

No MySQL DEV, confirmar nome do schema e executar `migrate`, `check`, `makemigrations --check` e testes focados descritos na documentação funcional. Nunca apontar testes para `arion_geo` de produção nem para OTRS. Esta etapa está pendente por ausência de configuração DEV MySQL neste checkout.

## Atualização, após homologação e autorização do responsável

Executar PowerShell na raiz da instalação. Comandos abaixo são instruções para o operador, não ações já realizadas.

```powershell
git status --short
git rev-parse HEAD
.\deploy\windows\stop-arion.ps1 -Role worker
.\deploy\windows\stop-arion.ps1 -Role web
```

Com os processos parados, realizar backup MySQL pelo procedimento existente. Se `mysqldump` estiver no PATH:

```powershell
$geoBackupDir = Join-Path (Get-Location) 'var\backups'
New-Item -ItemType Directory -Force -Path $geoBackupDir | Out-Null
$geoBackupFile = Join-Path $geoBackupDir ('antes-geo-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sql')
$geoBackupTemp = Join-Path (Get-Location) 'var\backup-private'
.venv\Scripts\python.exe deploy\windows\backup_mysql.py --mysqldump (Get-Command mysqldump.exe).Source --output $geoBackupFile --temp-dir $geoBackupTemp
if ($LASTEXITCODE -ne 0) { throw 'Backup não confirmado; interromper atualização.' }
```

Sem PATH, informar o caminho real homologado do `mysqldump.exe`; não inventar credenciais. O script usa a configuração ativa protegida e não imprime a senha.

Com árvore limpa e backup confirmado:

```powershell
git pull --ff-only origin main
npm ci
npm run build:production
if ($LASTEXITCODE -ne 0) { throw 'Build falhou.' }
.venv\Scripts\python.exe arion_geo\gerenciar.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration falhou.' }
.venv\Scripts\python.exe arion_geo\gerenciar.py check
.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check --dry-run
.venv\Scripts\python.exe arion_geo\gerenciar.py collectstatic --noinput
.\deploy\windows\start-arion.ps1 -Role web
.\deploy\windows\start-arion.ps1 -Role worker
.\deploy\windows\status-arion.ps1
```

Node 24 é o requisito já declarado em `package.json`. Nenhuma dependência nova de aplicação foi introduzida. Migrations: `configuracoes.0012_alter_groupdatascope_options` e `integracoes.0017_geographycatalog_and_more`, ambas aditivas/metadata.

Na Central: habilitar IBGE, testar e solicitar sincronização; verificar 5 regiões/27 UFs e sucesso do worker. Para clima corporativo, configurar modalidade comercial, licença compatível, endpoint permitido e chave pela própria Central sob a identidade Windows apropriada. Testar o contrato, solicitar uma UF/mês autorizada e acompanhar o cache/saúde. Não interpretar “configurado” como integração ativa já homologada. A opção de atualização periódica é explícita e somente comercial.

Conceder `configuracoes.present_geo` apenas aos grupos autorizados. Validar Mapa Brasil/Sudeste/SP, troca de mês, tema claro/escuro, apresentação/encerramento, bloqueio de APIs internas durante a apresentação e os dois fluxos de Cancelamentos. Conferir as páginas autenticadas usuais e os logs sanitizados do worker.

## Rollback desta entrega

1. Parar Web e Worker pelos scripts acima.
2. Registrar o commit que falhou e retornar **ao hash previamente registrado da instalação**. Se a instalação anterior correspondia exatamente à base desta entrega, o hash é `b961b18`. Não presumir que todo servidor estava nessa base.
3. Em emergência, com árvore limpa, `git switch --detach <hash-anterior-registrado>` permite executar o código anterior sem reescrever `origin/main`. Reaplicar o procedimento de build e `collectstatic` acima.
4. **Manter as novas tabelas/cache e registros de migration.** Código anterior ignora as tabelas aditivas; não executar migration reversa destrutiva e não apagar evidências ou históricos de Cancelamentos. Não restaurar um backup antigo sobre eventos novos apenas para remover caches.
5. Iniciar Web e Worker pelos scripts existentes e validar rotas. A chave de apresentação que sobrar na sessão pode ser eliminada encerrando a sessão de usuário.
6. Restaurar backup integral somente se houver dano de banco comprovado e plano aprovado para reconciliar escritas posteriores. Não há mudança destrutiva de esquema prevista nesta entrega.

Ao reaplicar a correção, retornar à `main` validada por fast-forward conforme o processo da equipe, sem force push e sem alterar `v1.4.0`.

## Evidência DEV em 12/11/2026

- `check`: sem problemas. `makemigrations --check --dry-run`: sem alterações pendentes. As duas migrations foram aplicadas com sucesso no SQLite local.
- Bateria focada: **300 testes passaram**, cobrindo Mapa, GEO/clima, motivos, worker, APIs externas, Cancelamentos, Rankings e `MirrorAnalyticsTests` de Dashboard/Cockpit. O runner local usou um hasher rápido somente durante testes; a configuração de senhas da aplicação permaneceu intacta.
- Também foram executados pelo comando Django padrão, com a configuração normal de hashes, os **22 testes** dos módulos GEO/clima então existentes: passaram. O teste adicional de logout durante apresentação passou na bateria de 300.
- Leitura real IBGE: cinco regiões e 27 UFs, com tratamento de resposta gzip. A leitura de diagnóstico não habilitou a integração nem simulou uma sincronização aplicada.
- Build de produção: 34 bundles e Service Worker gerados; sintaxe dos três arquivos JavaScript afetados validada por Node 24. Artefatos gerados permanecem fora do Git, conforme o projeto.
- Chromium/Playwright em snapshot SQLite isolado: 1920×1080, 1440×900, 1366×768 e 1024×768 sem overflow horizontal; Sudeste com ES/MG/RJ/SP, seleção de SP, apresentação/saída e temas claro/escuro verificados, sem erros JavaScript. O Mapa e os indicadores permaneceram disponíveis sem cache meteorológico.
- Open-Meteo: parser, unidades, ausência de dados, cache, limite/licenciamento e falhas foram validados com respostas controladas. **Não houve coleta real/comercial nem validação de chave/plano neste ambiente.**
- **MySQL DEV pendente:** nenhuma configuração válida de `arion_geo_dev` estava disponível. A validação principal MySQL deverá ocorrer antes do deploy; nenhuma conexão de produção/OTRS foi usada como substituto.

Para reproduzir a bateria focada com o runner padrão no DEV, a partir da raiz:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.mapa aplicativos.integracoes.test_geo_intelligence aplicativos.integracoes.test_closing_reason aplicativos.integracoes.test_worker aplicativos.integracoes.test_external_apis aplicativos.cancelamentos.tests aplicativos.cancelamentos.test_refinements aplicativos.rankings aplicativos.painel.tests.MirrorAnalyticsTests --noinput
```

Commits funcionais: `54af248` (integrações), `f2743b4` (GEO/apresentação/indicadores) e `57ee53f` (Cancelamentos). A documentação é entregue em commit separado. Não houve deploy no Windows Server.
