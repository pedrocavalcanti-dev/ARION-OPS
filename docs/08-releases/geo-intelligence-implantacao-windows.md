# Atualização GEO Intelligence — Windows Server

A evolução nacional/local acrescenta a migration aditiva **integracoes.0018_weathercollectionjob_progress_and_more** (progresso da fila e cache municipal), seguida de **integracoes.0019_weatherrequestlog_weathergridcache** (grade persistente e consumo por tentativa). A política de histórico acrescenta **integracoes.0020/0021**, com metadados, versões e índice municipal. Ver [operação histórica](../06-integracoes-e-dados/historico-climatico.md). Manter tabelas, snapshots e caches ao reverter código; não reverter migrations destrutivamente. Ver [operação nacional/local](../05-funcionalidades/geo-intelligence-nacional-local.md).

Esta entrega não altera a versão registrada nem a tag `v1.4.0`. Nenhum deploy foi executado pelo agente. Homologar migrations e testes focados no MySQL DEV `arion_geo_dev` antes de autorizar produção. Credenciais/plano meteorológico e DPAPI do usuário de serviço exigem configuração no ambiente de destino.

## Preparação

Registre o commit atualmente implantado (`git rev-parse HEAD`) e o estado das tarefas/supervisores. A base local anterior a esta entrega era `b961b18`. Preserve `.env`, configuração de banco e identidade Windows que protege as credenciais. Novos cadastros GEO são inicializados habilitados pelo bootstrap idempotente, com fonte pública, sem chave e sem agendamento/coleta automática. Configurações existentes são preservadas.

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

Na Central, conferir os cadastros GEO criados pelo bootstrap e as configurações preservadas. Testar IBGE e solicitar sincronização; verificar cinco regiões/27 UFs. Open-Meteo admite fonte pública sem chave, comercial com chave real ou self-hosted HTTPS. A funcionalidade não impõe bloqueio por ambiente nem confirmação administrativa de licença; as condições do fornecedor continuam aplicáveis. Testar o contrato e solicitar uma única UF/mês, conferindo Worker e cache. Agendamento exige ativação e seleção explícita das UFs. Ver [procedimento atual e evidência real SP/2026-10](../06-integracoes-e-dados/open-meteo-homologacao.md).

Conceder `configuracoes.present_geo` apenas aos grupos autorizados. Validar Mapa Brasil/Sudeste/SP, troca de mês, tema claro/escuro, apresentação/encerramento, bloqueio de APIs internas durante a apresentação e os dois fluxos de Cancelamentos. Conferir as páginas autenticadas usuais e os logs sanitizados do worker.

## Rollback desta entrega

1. Parar Web e Worker pelos scripts acima.
2. Registrar o commit que falhou e retornar **ao hash previamente registrado da instalação**. Se a instalação anterior correspondia exatamente à base desta entrega, o hash é `b961b18`. Não presumir que todo servidor estava nessa base.
3. Em emergência, com árvore limpa, `git switch --detach <hash-anterior-registrado>` permite executar o código anterior sem reescrever `origin/main`. Reaplicar o procedimento de build e `collectstatic` acima.
4. **Manter as novas tabelas/cache e registros de migration.** Código anterior ignora as tabelas aditivas; não executar migration reversa destrutiva e não apagar evidências ou históricos de Cancelamentos. Não restaurar um backup antigo sobre eventos novos apenas para remover caches.
5. Iniciar Web e Worker pelos scripts existentes e validar rotas. A chave de apresentação que sobrar na sessão pode ser eliminada encerrando a sessão de usuário.
6. Restaurar backup integral somente se houver dano de banco comprovado e plano aprovado para reconciliar escritas posteriores. Não há mudança destrutiva de esquema prevista nesta entrega.

Ao reaplicar a correção, retornar à `main` validada por fast-forward conforme o processo da equipe, sem force push e sem alterar `v1.4.0`.

## Evidência da entrega GEO anterior — 12/11/2026

- `check`: sem problemas. `makemigrations --check --dry-run`: sem alterações pendentes. As duas migrations foram aplicadas com sucesso no SQLite local.
- Bateria focada: **300 testes passaram**, cobrindo Mapa, GEO/clima, motivos, worker, APIs externas, Cancelamentos, Rankings e `MirrorAnalyticsTests` de Dashboard/Cockpit. O runner local usou um hasher rápido somente durante testes; a configuração de senhas da aplicação permaneceu intacta.
- Também foram executados pelo comando Django padrão, com a configuração normal de hashes, os **22 testes** dos módulos GEO/clima então existentes: passaram. O teste adicional de logout durante apresentação passou na bateria de 300.
- Leitura real IBGE: cinco regiões e 27 UFs, com tratamento de resposta gzip. A leitura de diagnóstico não habilitou a integração nem simulou uma sincronização aplicada.
- Build de produção: 34 bundles e Service Worker gerados; sintaxe dos três arquivos JavaScript afetados validada por Node 24. Artefatos gerados permanecem fora do Git, conforme o projeto.
- Chromium/Playwright em snapshot SQLite isolado: 1920×1080, 1440×900, 1366×768 e 1024×768 sem overflow horizontal; Sudeste com ES/MG/RJ/SP, seleção de SP, apresentação/saída e temas claro/escuro verificados, sem erros JavaScript. O Mapa e os indicadores permaneceram disponíveis sem cache meteorológico.
- Naquela entrega, Open-Meteo teve parser, unidades, ausência de dados, cache, limite/licenciamento e falhas validados com respostas controladas, sem coleta real/comercial ou validação de chave/plano. A coleta pública real posterior está registrada no [estado atual da candidata](v1.4.1-candidata.md).
- **MySQL DEV pendente:** nenhuma configuração válida de `arion_geo_dev` estava disponível. A validação principal MySQL deverá ocorrer antes do deploy; nenhuma conexão de produção/OTRS foi usada como substituto.

Para reproduzir a bateria focada com o runner padrão no DEV, a partir da raiz:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.mapa aplicativos.integracoes.test_geo_intelligence aplicativos.integracoes.test_closing_reason aplicativos.integracoes.test_worker aplicativos.integracoes.test_external_apis aplicativos.cancelamentos.tests aplicativos.cancelamentos.test_refinements aplicativos.rankings aplicativos.painel.tests.MirrorAnalyticsTests --noinput
```

Commits funcionais: `54af248` (integrações), `f2743b4` (GEO/apresentação/indicadores) e `57ee53f` (Cancelamentos). A documentação é entregue em commit separado. Não houve deploy no Windows Server.

## Validação da candidata 1.4.1 — etapa anterior à simplificação

Ver o [changelog e diagnóstico da candidata](v1.4.1-candidata.md). A versão anunciada/tag 1.4.0 permanece intacta. Naquela etapa a candidata não acrescentava migrations; a evolução nacional/local posterior acrescenta integracoes.0018/0019. Não houve implantação em produção.

- Bateria de **232 testes focados passou**, cobrindo Mapa/GEO, clima, Cancelamentos, Dashboard/Cockpit e tratamento de erros/segurança das APIs. Após a revisão de cache, os **16 testes climáticos passaram** novamente, incluindo um teste adicional de reagendamento de job concluído sem cache do método atual: **233 casos distintos validados**. O runner usa MD5 somente sob `override_settings` durante os testes; nenhum hasher da aplicação foi alterado.
- `check`: sem problemas. `makemigrations --check --dry-run`: nenhuma alteração. Build de produção: 34 bundles mais Service Worker, sem dependência nova.
- Chromium em snapshot SQLite isolado: quatro viewports (1920×1080, 1440×900, 1366×768 e 1024×768), sem overflow horizontal; Brasil/Nordeste/BA, painel expansível, preservação da câmera manual no resize, temas, todos os modos de apresentação, tela cheia e encerramento, sem erros JavaScript.
- POST real no navegador: `409`, JSON `status=disabled` com mensagem da configuração ausente. O antigo `400` foi reproduzido antes da mudança. Nem API Open-Meteo nem falha de CSRF explicavam a recusa original.
- Teste de interface com clima sintético, apenas no snapshot: zero versus ausente, timeline/reprodução sem novas consultas, temperatura em °C. A imagem está identificada como teste controlado. O cache sintético foi removido antes das imagens finais do Mapa interno/apresentação.
- Cancelamentos em navegador: link OTRS com TicketID e nova aba; backdrop/Escape mantêm formulário, fechamento sujo exige confirmação e erro de submissão controlado mantém valores. Permissões, validações e auditoria foram cobertas pelos testes Django.
- Reconciliação real do mirror DEV: 436 incidentes, 434 classificados, dois sem classificação, 426 com UF e dez sem localização; 341 links afetados e 65 reincidentes. As 427 ocorrências estaduais não substituem o total distinto. Veja as definições no changelog.
- Naquela etapa, Open-Meteo estava sem cadastro/cache/job, sem Worker ativo e sem coleta real. Esta limitação foi superada no SQLite DEV pela simplificação descrita no [estado atual da candidata](v1.4.1-candidata.md). MySQL DEV continua pendente; produção e OTRS não foram utilizados como substituto.

Comando da bateria focada desta candidata, com o runner Django padrão:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.mapa aplicativos.integracoes.test_geo_intelligence aplicativos.cancelamentos.tests aplicativos.cancelamentos.test_refinements aplicativos.painel.tests.MirrorAnalyticsTests aplicativos.configuracoes.test_errors config.test_security --noinput
```

Para rollback de um refinamento 1.4.1, usar o hash efetivamente registrado antes do rollout; `b7704d7` é a referência somente quando o servidor estiver exatamente na entrega GEO anterior. Os passos de parada, backup, build, collectstatic e retomada acima continuam válidos. Caches `v1`/`v2` são aditivos e não exigem remoção de tabelas ou dados operacionais.
