# ARION ATACADO GEO — Patch Semanal 2026-W44

## Status

Em desenvolvimento

## Período

Semana de 26/10/2026 a 01/11/2026.

## Objetivo

Consolidar correções e melhorias incrementais da operação antes da próxima publicação em produção.

---

## Correções concluídas

### FIX 01 — Filtro de responsáveis na Fila operacional

**Problema**

O filtro Responsável repetia o mesmo analista uma vez para cada chamado atribuído.

**Causa**

A consulta aplicava `distinct()` a valores de `TicketTracking`, cujo ordenamento padrão inclui data de atualização e chamado. Esses campos faziam registros do mesmo usuário permanecerem distintos.

**Correção**

A lista agora seleciona usuários por ID a partir dos acompanhamentos ligados ao conjunto de chamados autorizado. Cada usuário aparece uma vez, com nome completo e username como fallback. O filtro continua usando o ID interno do usuário e `TicketTracking.assigned_to`, que representa o responsável ARION.

**Comportamento esperado**

Cada responsável aparece uma única vez. Selecionar um analista retorna todos os chamados dele dentro do recorte atual. “Sem responsável” permanece uma opção única e retorna chamados sem atribuição ARION.

**Impacto**

Chamados / Fila operacional.

**Arquivos**

- `arion_geo/aplicativos/chamados/services.py`
- `arion_geo/aplicativos/chamados/tests.py`
- `docs/07-estudos/patch-semanal-2026-w44.md`

**Banco**

Sem migration.

**Deploy**

Reiniciar somente o serviço web após a publicação. Nenhuma alteração foi feita no Worker ou em assets de frontend.

**Testes**

- PASS — `.venv\Scripts\python.exe arion_geo\gerenciar.py check`.
- PASS — `.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check` (`No changes detected`).
- PASS — `.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.chamados aplicativos.configuracoes` (88 testes).
- PASS — `git diff --check`.

A suíte foi executada com `DATABASE_URL=sqlite:///:memory:` porque o usuário MySQL `arion` não tem permissão para criar o banco de teste `test_arion_geo`.

---

### FIX 02 - Resolvido como término operacional global

**Problema**

O timestamp terminal de `StateUpdate` podia incluir o intervalo administrativo entre resolução e `CLOSED`, elevando duração, aging e deslocando análises mensais.

**Regra e correção**

`resolved_at` (Dynamic Field `ticketResolvido.value_date`) agora é a data operacional principal. `terminal_state_at` permanece preservado como dado OTRS derivado, diagnóstico e fallback somente quando `resolved_at` está ausente. Sem ambas as datas, métricas temporais e duração final não fabricam um horário. `business_status=CLOSED` continua inalterado e distinto da resolução operacional.

**Mapeamento**

- Dados brutos/sincronização: status OTRS, `resolved_at` e `terminal_state_at` seguem separados; não houve mudança no FAST/FULL, Worker ou CMDB.
- Métricas, filtros e grupos: Cockpit, motivos do Mapa, aging/duração dos Rankings e duração da Base usam resolução operacional quando esse é o conceito; períodos de criação continuam em `source_created_at`.
- Dashboard: volumes mensais continuam por criação e contagens de status continuam snapshots oficiais atuais.
- Exibição/exportação: tela e exportações distinguem resolução usada de timestamp terminal OTRS.

**Impacto**

Painel/Cockpit, Mapa, Rankings, Base e documentação operacional.

**Arquivos**

- Backend: arion_geo/aplicativos/integracoes/services/analytics.py, arion_geo/aplicativos/painel/services/analytics.py, arion_geo/aplicativos/mapa/services/analytics.py, arion_geo/aplicativos/base_links/views.py, arion_geo/aplicativos/rankings/services.py e arion_geo/aplicativos/rankings/exports.py.
- Testes: arion_geo/aplicativos/painel/tests.py, arion_geo/aplicativos/mapa/tests.py, arion_geo/aplicativos/base_links/tests.py e arion_geo/aplicativos/rankings/tests.py.
- Interface: arion_geo/templates/rankings/index.html, frontend_src/js/base-links.js, frontend_src/js/mapa/mapa-real.js, frontend_src/js/painel/cockpit.js e frontend_src/js/rankings/rankings.js.
- Documentação: docs/DICIONARIO-DE-DADOS.md, docs/FONTES-DE-DADOS.md, docs/OTRS.md, docs/funcionalidades/cockpit.md, docs/funcionalidades/dashboard.md, docs/funcionalidades/mapa.md, docs/funcionalidades/rankings.md e este patch semanal.

Os arquivos de Chamados presentes no estado do Git pertencem ao FIX 01 e foram preservados.

**Banco e deploy**

Sem migration. Assets JavaScript fonte foram alterados e precisam do build frontend. Reiniciar somente o serviço web após publicar os arquivos; Worker não mudou.

**Testes**

- PASS - check do Django.
- PASS - makemigrations --check (No changes detected).
- PASS - suíte de Base, 27 testes.
- PASS - suíte afetada combinada: 258 de 261 testes.
- FALHA - 3 testes de Data Scope/resumo: test_home_summary_counts_only_scoped_links_named_providers_and_valid_states, test_calendar_respects_provider_filter_and_data_scope e test_cockpit_options_and_results_preserve_custom_data_scope. Eles não exercitam os campos de término alterados; permanecem falhando nesta execução.
- PASS - sintaxe dos quatro entrypoints JavaScript alterados via esbuild nativo.
- NÃO EXECUTADO - build de produção: npm/Node não estão instalados neste ambiente.
- PASS - git diff --check e verificação de whitespace deste documento antes da entrega.

A suíte combinada usou SQLite em memória. A tentativa padrão de criar banco de teste MySQL não funciona com o usuário arion, que não tem permissão para criar test_arion_geo.

---

### FIX 03 — Padronização de Tickets x Links afetados

**Problema e auditoria**

As contagens usavam querysets e regras locais por módulo. Dashboard e Chamados já contavam Tickets distintos e Links distintos, mas usavam o flag derivado `is_business_open`; o helper compartilhado de contagem por Link também dependia desse flag. A fila calculava os cartões em um queryset filtrado, antes de aplicar as abas/`otrs_status`; o Dashboard apresentava snapshots atuais sem esses filtros. Mapa é geográfico e conta somente Tickets relacionados a Links com UF válida. Cockpit e Rankings são análises históricas, não KPIs atuais. No Mapa, Tickets de reincidência eram somados por Link, duplicando o mesmo ID quando ligado a mais de um Link reincidente.

No snapshot local consultado em modo somente leitura: 12.062 tickets NOC presentes, 22 com status `OPEN`/`PENDING`/`SCHEDULED`, 22 com `is_business_open=True`, zero divergência entre flag e status, e 21 Links locais distintos associados. Um Ticket ativo não tinha qualquer CI; nenhum tinha CI sem Link local e nenhum estava associado a mais de um Link distinto. A diferença observada neste snapshot é, portanto, 22 Tickets / 21 Links afetados, explicada por um Ticket sem CI. O exemplo anterior de 26/21/3 não corresponde ao snapshot atual.

**Regra canônica e correção**

- Tickets operacionais atuais: IDs distintos da fila exata `NOC - ATACADO`, presentes no snapshot atual e com `business_status` em `OPEN`, `PENDING` ou `SCHEDULED`.
- Links afetados: IDs distintos de `CMDBLinkMirror` presentes localmente, dentro do Data Scope, com ao menos uma relação Ticket–ConfigItem presente ligada ao universo de Tickets contado.
- Relação N:N é preservada: vários Tickets em um Link contam vários Tickets e um Link; um Ticket em dois Links conta um Ticket e dois Links.
- Ticket sem CI ou CI sem Link local continua contando como Ticket em escopo global, mas não cria Link. Em Data Scope `CUSTOM`, Ticket sem relação com Link autorizado permanece fora do queryset autorizado.
- `integracoes.services.analytics` centraliza a seleção de Tickets business-open, as relações Ticket–Link e a agregação `COUNT(DISTINCT Link)`. Dashboard e Fila usam a mesma regra. O flag derivado não é a fonte desta contagem.
- A Fila mantém sua semântica: busca/filtros de Cliente, Projeto, Provedor, UF, ARION, responsável, prioridade, motivo, aging e reincidência afetam os cartões; paginação, abas e `otrs_status` não afetam os cartões. No Mapa, o contador nacional deduplica Tickets; reincidência deduplica Tickets também entre vários Links reincidentes. Métricas históricas de Cockpit/Rankings foram auditadas e não convertidas em snapshots atuais.

**Três testes de Data Scope anteriores**

`test_home_summary_counts_only_scoped_links_named_providers_and_valid_states`, `test_calendar_respects_provider_filter_and_data_scope` e `test_cockpit_options_and_results_preserve_custom_data_scope` criavam Links de fixture sem `contracting_client` correspondente ao nome de Operadora permitido. A política documentada de Data Scope restringe `CMDBLinkMirror` por `contracting_client`/raw e por Projeto; o código falhava fechado corretamente. Os fixtures agora recebem o cliente permitido, preservando expectativas. Nenhuma política ou asserção foi enfraquecida. A execução MySQL desses testes ainda está pendente pela preparação do schema abaixo.

**Impacto**

Integrações/analytics compartilhada, Dashboard, Fila operacional, Mapa, painel de integrações e uso comum do backlog em Rankings/Base. Cockpit, Rankings e Base mantêm suas semânticas históricas. Sem mudança em FAST/FULL, Worker ou CMDB.

**Arquivos**

- Backend: `arion_geo/aplicativos/integracoes/services/analytics.py`, `integracoes/views.py`, `painel/services/analytics.py`, `chamados/services.py`, `mapa/services/analytics.py`, `rankings/services.py`, `base_links/views.py`.
- Testes: `painel/tests.py`, `chamados/tests.py`, `mapa/tests.py`; também ajustados os fixtures dos três testes antigos de Data Scope no arquivo de Painel.
- Documentação: `docs/funcionalidades/dashboard.md`, `chamados.md`, `mapa.md`, `rankings.md`, `cockpit.md`, `docs/DICIONARIO-DE-DADOS.md` e este patch.

**Banco, migration e frontend**

Sem alteração de model e sem migration. Não houve mudança em templates, CSS ou JavaScript; build frontend não necessário.

**Testes MySQL e validações**

Testes de integração MySQL: não executados. Django está configurado para o schema operacional `arion_geo`; `TEST.NAME` não está definido, então o nome padrão de teste é `test_arion_geo`. A inspeção somente leitura não encontrou `test_arion_geo` nem `arion_geo_test`, e o usuário MySQL da aplicação não possui permissão para criar schema. A suíte foi interrompida antes de executar, conforme solicitado; nenhum teste foi direcionado a `arion_geo` e não foi usado SQLite.

Para liberar a execução segura, o administrador MySQL deve criar `test_arion_geo` e conceder ao usuário da aplicação privilégios limitados a `test_arion_geo.*` para DDL/DML da suíte (`CREATE`, `DROP`, `ALTER`, `INDEX`, `REFERENCES`, `SELECT`, `INSERT`, `UPDATE`, `DELETE`). Não é necessária permissão global de criação de bancos; não adicionar senha em código. Depois, rodar a suíte com `--keepdb`.

Foram adicionados casos para múltiplos Tickets no mesmo Link, Ticket em múltiplos Links, sem CI, CI sem Link local, estados terminais, flag divergente, Data Scope, filtro por Provedor/Projeto e igualdade Dashboard/Fila. Mapa ganhou teste para Ticket compartilhado por Links reincidentes. Não foi possível confirmar esses testes no MySQL antes da concessão.

Validações locais: PASS — `.venv\Scripts\python.exe arion_geo\gerenciar.py check`; PASS — `.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check` (`No changes detected`); PASS — `git diff --check` (somente avisos Git de normalização LF/CRLF). Testes de integração permanecem pendentes do schema MySQL separado. Sem migration; publicação exige atualizar/reiniciar somente o serviço web. Worker não muda. Resultado em produção continua pendente.

---

### FIX 04 — Universo operacional e aliases canônicos de Projeto no Mapa

**Problema e causa**

O Mapa filtrava status sem restringir a procedência operacional e agrupava Projetos pelo valor raw, separando aliases confirmados. Agregados e detalhes da UF precisavam aplicar as mesmas regras. Tickets sem relação com Link local também não podiam receber UF ou aumentar Links afetados.

**Regra e correção**

- Os modos operacionais do Mapa incluem apenas tickets NOC com status válido para o conceito analisado e responsabilidade `PROCEDENTE` ou `INDETERMINADO`. `CANCELED`, `UNKNOWN` e `IMPROCEDENTE` ficam fora; `RESOLVED` e `CLOSED` continuam válidos em análises históricas e Motivos.
- No recorte nacional sem filtros de cliente, Projeto, provedor ou UF, Tickets sem ConfigItem ou sem Link CMDB local entram na contagem nacional de Chamados/Motivos. Não são atribuídos a UF nem contam como Link afetado. O Mapa mostra essa quantidade separadamente. O Data Scope continua sendo aplicado antes das análises; em `CUSTOM`, Ticket sem relação com Link autorizado permanece fora.
- O Mapa exibe, filtra e agrupa `BDN`/`BRADESCO` como `BRADESCO` e `BB`/`BANCO DO BRASIL` como `BANCO DO BRASIL`. A comparação normaliza caixa, espaços excedentes e variações conservadoras de separadores. `BRADESCO SEGUROS` e `BB SEGUROS` permanecem distintos. Os aliases são aplicados somente ao conjunto já autorizado pelo Data Scope; campos persistidos, parser, sincronização e fontes OTRS/CMDB não são alterados.

**Impacto**

Modos Chamados, Reincidência e Motivos; filtros e opções de Projeto; contagens nacionais/por UF, Links afetados, agrupamentos e detalhes do Mapa. Contagens sem Link ficam somente no total nacional e são explicadas na interface.

**Arquivos e banco**

- Backend: `arion_geo/aplicativos/integracoes/services/project_names.py`, `arion_geo/aplicativos/mapa/services/analytics.py` e `arion_geo/aplicativos/base_links/views.py`.
- Testes adicionados/ajustados: `arion_geo/aplicativos/mapa/tests.py`, cobrindo responsabilidade e status, Tickets sem Link local, aliases, nomes distintos, detalhes e Data Scope.
- Interface/documentação: `frontend_src/js/mapa/mapa-real.js`, `docs/funcionalidades/mapa.md` e este patch.
- Sem migration. Não houve alteração em sincronização, Worker, OTRS ou CMDB.

**Frontend build**

Necessário: SIM. O JavaScript fonte mudou. O build de produção não foi executado localmente porque Node/npm não estão disponíveis.

**Deploy**

Como o JavaScript fonte mudou, publicar os arquivos e executar `npm ci`, `npm run build:production` e `.venv\Scripts\python.exe arion_geo\gerenciar.py collectstatic --noinput`. Reiniciar somente o serviço web. O Worker não muda.

**Testes MySQL**

Não executados com esta alteração. Rechecagem somente de metadados confirmou que `test_arion_geo` e `arion_geo_test` não existem e que o usuário da aplicação não tem `CREATE` global nem privilégios no schema de teste; ele tem acesso ao schema operacional `arion_geo`, que não foi usado para testes. Os casos FIX 04 e os três testes de Data Scope/resumo pendentes do FIX 03 devem ser executados em `test_arion_geo` após o administrador criar esse schema e conceder privilégios limitados a ele. Não foi usado SQLite.

---

### FIX 05 - Cockpit operacional geografico no Mapa

**UX anterior**

Hover ja exibia um resumo pequeno por UF e clique abria o drawer lateral. O drawer era uma lista corrida, sem navegacao por areas nem busca e paginacao dos Tickets.

**UX nova**

- Hover mantem tooltip compacto por modo, acompanha o cursor com ajuste automatico nas bordas e nao abre drawer.
- Clique fixa a UF no drawer lateral. Abas: Visao geral, Chamados, Projetos, Provedores, Motivos e Reincidencia. Hover em outra UF nao altera a selecao.
- Chamados oferece busca no servidor, 25/50/100 itens por pagina e campos OTRS/ARION, cliente, projeto, provedor, data, prioridade e motivo.
- Abrir encaminha ao drawer existente em Chamados. Tickets historicos ficam em leitura; alteracoes ARION seguem restritas a fila ativa.
- Projetos e provedores mostram Links CMDB distintos e Tickets distintos relacionados a cada dimensao; projetos usam os aliases canonicos de FIX 04.
- Motivos reutilizam a classificacao normalizada; Reincidencia lista Link, quantidade de ocorrencias e data da ultima ocorrencia, sem criar um modulo REC.

**Filtros**

Cliente, Projeto, periodo e datas, Provedor, UF, status OTRS, prioridade, status ARION, responsavel ARION, procedencia e motivo de fechamento. Chips ativos removem filtros individuais ou limpam o recorte. Filtros e selecao de UF permanecem na URL.

**Endpoints e consultas**

- GET /base/api/mapa/: agregacao nacional/por UF com filtros status OTRS, status ARION, responsavel e procedencia.
- GET /base/api/mapa/uf/: aba, busca e pagina/tamanho de Tickets. Nenhuma rota de integracao nova foi criada.
- /chamados/?open_ticket=<ID>: abre o drawer atual de Chamados, que consulta /chamados/api/<ID>/ dentro do Data Scope.

**Regra, escopo e desempenho**

Data Scope e aplicado antes de filtros, catalogos, agregacoes, detalhes e opcoes de responsavel. O universo FIX 04 permanece NOC Atacado, estados aceitos e responsabilidade procedente/indeterminada. Motivos usam resolved_at e terminal_state_at apenas como fallback. Links afetados continuam IDs distintos conforme FIX 03.

Filtros de status, prioridade, tracking, responsavel, provedor, UF e data restringem as consultas no banco. O detalhe pagina a resposta para 25/50/100 Tickets e carrega o tracking apenas dos itens da pagina. A busca por identidade do Ticket e aplicada no servidor apos interpretar os titulos/relacoes da UF; nenhum conjunto completo de Tickets e enviado ao navegador.

**Arquivos**

- Backend: aplicativos/mapa/services/analytics.py, aplicativos/base_links/views.py, aplicativos/chamados/views.py.
- Interface: templates/mapa/_cmdb.html, frontend_src/js/mapa/mapa-real.js, frontend_src/js/chamados/chamados.js, static/css/mapa/mapa.css.
- Testes: aplicativos/mapa/tests.py e aplicativos/chamados/tests.py.
- Documentacao: docs/funcionalidades/mapa.md e este patch.

**Banco e deploy**

Sem migration. Worker, OTRS FAST/FULL, sincronizacao e CMDB nao foram alterados. Frontend build necessario por mudancas de JavaScript e CSS.

- `git pull`: SIM.
- `migrate`: NAO.
- `npm ci`: SIM.
- `npm run build:production`: SIM.
- `collectstatic --noinput`: SIM.
- Reiniciar web: SIM.
- Reiniciar Worker: NAO.

**Testes**

Testes novos para paginacao e busca server-side, filtros ARION/responsavel, opcao de responsaveis limitada pelo Data Scope, modo Projetos com periodo, reincidencia por Link e abertura no drawer Chamados.

- PASS - Django check.
- PASS - makemigrations --check (`No changes detected`).
- PASS - py_compile dos modulos e testes de Mapa/Chamados.
- PASS - sintaxe JavaScript do Mapa e Chamados via esbuild nativo.
- PASS - git diff --check.
- NAO EXECUTADOS - testes MySQL: `test_arion_geo` e `arion_geo_test` nao existem e o usuario da aplicacao nao pode criar schema. Nao foi usado SQLite; administrador deve preparar `test_arion_geo` com grants limitados, conforme FIX 03.
- NAO EXECUTADO - build de producao local: Node/npm nao estao instalados. Build continua necessario no deploy.

**Refinamento visual final do Mapa**

- Tooltip de hover permanece compacto junto ao cursor; hover não altera a UF selecionada nem abre o drawer. O resumo nacional segue independente do hover.
- A UF selecionada fica identificada pelo breadcrumb e pelo título do drawer; o chip redundante `UF: BA` saiu da lista de filtros ativos, que fica abaixo dos controles.
- Drawer desktop ampliado para 480 px, com cabeçalho e abas fixos, conteúdo central rolável e ações inferiores persistentes. A lista de Chamados separa identificador/data, cliente/projeto, link/local/provedor, título, estado/prioridade e responsável/ação.
- Clique fora do drawer não descarta a seleção; ela é removida pelo X ou pelo breadcrumb Brasil, e pode ser trocada diretamente ao selecionar outra UF.
- `ticket_search` continua aplicado no servidor depois de UF, projeto canônico, período, cliente, provedor, status OTRS, prioridade, status ARION e responsável. Foi acrescentado caso de teste que busca dentro de BA + BRADESCO e exclui SP + BRADESCO e BA + Correios.
- Popover de Mais filtros usa duas colunas, largura máxima de 520 px e rolagem vertical. O controle visual de Procedência foi removido; a regra de responsabilidade procedente/indeterminada no backend permanece.
- Auditoria somente leitura encontrou 27 nomes distintos no snapshot: 08040412, ALGAR.MG., AMBEV, Arcos Dourados, ASSOCIAÇÃO BOM JESUS, Banco do Brasil, BDN BRADESCO, BOSCH, BOTICARIO, Bradesco, Caixa Econômica Federal, Carrefour, COPA ENERGIA, Correios, Diversos, ENERGISA, Escolas Conectadas, FORUM RIO NOVO, Fust, MART MINAS, O BOTICÁRIO, PETROBRÁS, PRODAM, PROJETO BB, SERILON, SHERWIN e TRIBUNAL DA JUSTIÇA DO ESTADO DE MINAS GERAIS.
- Aliases canônicos acrescentados com base nesse snapshot: `BDN BRADESCO` → `BRADESCO`, `PROJETO BB` → `BANCO DO BRASIL` e `BOTICARIO` → `O BOTICÁRIO`. Variantes `BDN-BRADESCO` e `BDN / BRADESCO` não apareceram e não foram adicionadas. Projetos de seguros permanecem separados.
- Arquivos adicionais: `integracoes/services/project_names.py`, `mapa/tests.py` e este patch. Sem alteração de analytics, Data Scope, OTRS/CMDB, Worker ou regras de negócio.


### FIX 07 — Cabeçalho fixo do modal da Base

**Problema**

O cabeçalho do modal de Links podia sair da área visível durante a rolagem do conteúdo, dificultando fechar a janela e identificar o contexto em telas menores.

**Solução**

O modal existente mantém cabeçalho e ação de fechar fixos, corpo rolável e limite de altura adaptado à viewport móvel, incluindo safe areas. O foco e o fluxo existentes do modal foram preservados.

**Impacto**

Base / Links, especialmente em viewports móveis e orientação landscape.

**Arquivos**

- `arion_geo/static/css/base-links.css`

**Banco e migration**

Sem alteração de banco e sem migration.

**Testes e build**

Contrato CSS coberto pelo teste `test_mobile_navigation_is_permission_gated_and_ticket_modal_keeps_header_visible`. A abertura/rolagem do modal em dispositivo real ou browser não foi exercitada nesta rodada. O build de produção é necessário pelo JavaScript do FIX 08; não foi executado localmente por ausência de Node/npm.

---

### FIX 08 — PWA e experiência mobile

**Problema**

O produto ainda não oferecia instalação como aplicativo, navegação mobile própria, shell offline seguro ou layout adequado nos principais fluxos móveis.

**Solução**

- PWA integrado ao Django existente, com manifest servido em `/manifest.webmanifest` e Service Worker no escopo `/` servido em `/service-worker.js`.
- Instalação Chromium somente após `beforeinstallprompt` e ação explícita do usuário; Safari/iOS recebe instrução de compartilhamento e “Adicionar à Tela de Início”. Standalone é detectado por `display-mode` e `navigator.standalone`.
- A tela de abertura é curta, por sessão e respeita `prefers-reduced-motion`; usa o símbolo derivado do favicon vetorial atual.
- Cache restrito a assets estáticos públicos e shell neutro. Navegações são sempre network-first; offline exibe uma página genérica. APIs e páginas operacionais não são armazenadas, portanto logout não disponibiliza respostas autenticadas pelo cache do Service Worker.
- Header/drawer mobile mantém links condicionados às permissões já existentes, opções de instalação/tema/conta, safe areas e diálogos acessíveis. Chamados ganhou filtros em folha móvel, chips removíveis e cartões que mantêm os campos operacionais. Mapa ajusta viewport às safe areas. Login usa viewport dinâmica e controles de toque maiores. O modal da Base mantém o cabeçalho fixo.

**Polimento responsivo da Central de Configurações**

- A sidebar desktop permanece; em mobile, a navegação lateral horizontal foi substituída por um seletor recolhível de Seções, mantendo os links sujeitos às mesmas permissões. O menu global do PWA continua como navegação principal.
- Filtros de Usuários empilham em telas estreitas; listas de Usuários, Grupos, Escopos e Auditoria passam a cards no mobile. Auditoria mantém filtros, resumo e exportação acessíveis sem rolagem horizontal da página.
- Minha conta, Segurança e Aparência usam uma coluna em mobile; controles recebem alvo de toque e fonte adequados para evitar zoom automático de formulário no iOS. Pinch zoom permanece habilitado.
- Sem novas regras, migrations, alterações de RBAC/Data Scope ou mudanças no manifest/Service Worker/cache.

**Escopo preservado**

Sem app/API de negócio paralelo. Não foram alterados RBAC, Data Scope, autenticação/CSRF/cookies, Worker, OTRS, FAST/FULL, CMDB ou semânticas operacionais dos FIX anteriores. Nenhuma migration.

**Arquivos**

- PWA/backend: `arion_geo/config/views.py`, `config/urls.py`, `config/middleware.py`, `static/pwa/*`, `static/css/pwa.css`, `static/css/pwa-offline.css`, `frontend_src/js/pwa/*`.
- Shells e páginas: `templates/base.html`, `templates/partials/mobile_navigation.html`, `templates/autenticacao/login.html`, `templates/documentacao/base_documentacao.html`, `templates/integracoes/base_integracoes.html`, `templates/chamados/index.html`, `templates/mapa/_cmdb.html`, `templates/configuracoes/base.html` e páginas administrativas de Usuários, Grupos, Escopos e Auditoria.
- Mobile: `static/css/autenticacao/login.css`, `static/css/chamados/chamados.css`, `static/css/mapa/mapa.css`, `static/css/base-links.css`, `static/css/configuracoes/settings.css`, `static/css/documentacao.css`, `static/css/integracoes/integracoes.css`, `frontend_src/js/chamados/chamados.js`, `frontend_src/js/base-links.js` e `frontend_src/js/configuracoes/settings.js`.
- Testes: `arion_geo/aplicativos/configuracoes/test_pwa.py`.
- Documentação: `docs/ARQUITETURA.md`, `docs/ESTADO-ATUAL.md`, `docs/IMPLANTACAO-E-PRODUCAO.md` e este patch.

**Build e deploy**

Build de produção: necessário e ainda não executado localmente (Node/npm ausentes). No servidor, executar `npm ci`, `npm run build:production`, `collectstatic --noinput` e `check`; reiniciar somente o serviço Web. O Worker de integração não muda. O Service Worker recebe nova revisão a partir do conteúdo do worker, manifest, shell e manifesto do build; workers antigos são substituídos na ativação. A publicação deve ser validada em navegador instalado e navegador normal antes de considerar produção concluída.

**Testes**

- PASS — 9 testes PWA/mobile `SimpleTestCase`, sem preparar ou utilizar banco de dados.
- PASS — sintaxe atual de `mapa-real.js` e `settings.js` via parser do Edge headless.
- PASS — `git diff --check`.
- PASS — Edge headless com emulação mobile: Login medido em 320×720, 360×800, 375×812, 390×844, 412×915, 430×932, 768×1024, 1024×768, 1280×800, 1440×900 e 1920×1080; `scrollWidth` ficou igual à viewport em todos.
- PASS — Edge headless: filtros da Fila medidos em 1366×768, 1440×900, 1920×1080, 390×844 e 320×720; sem overflow horizontal. Título e fechar da folha móvel não aparecem no desktop; no mobile, a folha ocupa a largura do viewport e Mais filtros cabe sem rolagem horizontal.
- PASS — renderização dos shells global, documentação, configurações, integrações e menu compartilhado com permissões de teste; rotas resolvidas.
- PASS — Edge headless: Service Worker registrado/ativo/controlando em `/`; com o servidor parado, navegação inédita exibiu shell offline sem conteúdo operacional. Cache instalado continha somente shell e ícones neutros.
- PENDENTE — revisão visual/funcional em dispositivos reais, abertura/rolagem do modal Base em dispositivo, instalação Android/iOS e build de produção/validação no servidor. A atualização do Service Worker foi exercitada em Chrome local na consolidação abaixo.

## Próximos fixes planejados

- [x] Revisar lógica de Links afetados x quantidade de tickets.
- [x] Remover cancelados, desconhecidos e improcedentes dos resultados operacionais do Mapa.
- [x] Unificar aliases confirmados de projetos no Mapa.
- [x] Melhorar filtros e painéis do Mapa, incluindo motivo de fechamento.
- [x] Fixar título do modal da Base.
- [x] Criar PWA e revisão mobile responsiva.

---

## Alterações técnicas

- Opções de responsáveis são carregadas como usuários unicos por ID, usando uma subconsulta baseada no conjunto de chamados após aplicação do Data Scope.
- A ordenação usa o nome amigável do usuário, com fallback para username.
- A chave assignee=<ID> e os filtros de acompanhamento ARION foram preservados.
- Nenhuma migration e necessária. O build frontend não se aplica ao FIX 01 em separado; o FIX 02 alterou fontes JavaScript e requer build de produção.

## Testes da semana

- FIX 01: PASS - check, makemigrations --check, 88 testes de Chamados e Configurações e git diff --check.
- FIX 02: 258/261 testes das suítes afetadas passaram; três falhas de Data Scope/resumo estão listadas acima. Sintaxe JavaScript passou via esbuild; build de produção não executado por falta de Node/npm.
- FIX 03: código e testes adicionados; execução MySQL pendente do schema seguro `test_arion_geo`, conforme detalhado acima.
- FIX 04: PASS — check do Django, makemigrations --check (`No changes detected`), `py_compile`, sintaxe do JavaScript Map via esbuild nativo e `git diff --check`. Testes não executados porque o schema seguro MySQL ainda não existe. Build de produção não executado: Node/npm não estão instalados neste ambiente.
- FIX 05: PASS — Django check, makemigrations --check (`No changes detected`), `py_compile` e sintaxe atual de `mapa-real.js` via Edge headless. Filtros da Fila medidos nos viewports desktop e mobile listados acima. Testes MySQL de Mapa pendentes do schema separado `test_arion_geo`; não foi usado SQLite. Build de produção não executado localmente por falta de Node/npm.
- PASS - makemigrations --check, sem alterações pendentes.
- FIX 07: cabeçalho fixo/corpo rolável implementados no CSS; contrato coberto por teste, validação visual do modal em browser pendente.
- FIX 08: PWA, shell seguro, menu mobile compartilhado, seletor de Seções e cards responsivos da Central de Configurações implementados. 9 testes sem banco, sintaxe JS via Edge, renderização dos shells, registro/controle/offline real e Login sem overflow em 11 viewports passaram. Filtros da Fila também foram medidos em cinco viewports. A consolidação W44 abaixo acrescenta o update interativo do Service Worker em Chrome; instalação em dispositivos reais, build de produção e validação no servidor permanecem pendentes.

## Procedimento de publicação

No servidor Windows, na raiz do projeto:

```powershell
cd C:/Users/Arion/Desktop/Arion-Atacado-Geo
git pull --ff-only origin main
npm ci
npm run build:production
.venv/Scripts/python.exe arion_geo/gerenciar.py collectstatic --noinput
.venv/Scripts/python.exe arion_geo/gerenciar.py check
./deploy/windows/restart-arion.ps1 -Role web
./deploy/windows/status-arion.ps1
```

Sem `migrate` e sem reinício do Worker. Confirmar manifest, escopo do Service Worker, instalação, navegação/logout e fallback offline antes de atualizar o resultado de produção. A distribuição de nova revisão depende da publicação do worker e do bundle; o botão de atualização pede confirmação antes de ativar e recarrega a página controlada.

## Resultado em produção

Resultado em produção: pendente

## Consolidação da release W44

- O catálogo `docs/08-releases/releases.json` mantém `1.1.0` e a data `2026-10-28` como versão/notas de usuário atuais; ele alimenta `/api/version/`, Documentação → Atualizações e versões e o cache PWA. O resultado da publicação em produção segue pendente.
- A página de releases apresenta as mudanças W44 por Novidades, Melhorias, Correções, Segurança e Infraestrutura. Qualquer usuário autenticado pode consultá-la; documentação técnica e busca continuam protegidas pela permissão administrativa existente. Os detalhes deste patch continuam sendo a referência técnica.
- O aviso PWA compara a versão do HTML com a versão do servidor ao abrir a aplicação, ao retornar ao foco após 15 minutos e a cada 20 minutos. “Depois” dispensa o aviso grande para a versão-alvo durante a sessão, mas o indicador compacto continua disponível na área superior direita da Visão Geral até a versão carregada coincidir com a do servidor. “Atualizar agora” mostra estado de progresso, tenta atualizar o Service Worker, ativa o worker em espera com `SKIP_WAITING` e recarrega uma vez após `controllerchange`; sem worker em espera, faz reload controlado. Rejeições/timeouts mostram erro e habilitam “Tentar novamente”.
- No Mapa desktop/tablet largo, o drawer de estado começa abaixo do cabeçalho global e vai até a base do viewport, cobrindo a parte direita dos filtros; mede 480 px em desktop e 440 px em tablet largo. O conteúdo interno rola, enquanto cabeçalho, abas e rodapé ficam fixos. Ao abrir o drawer, um backdrop fixo escurece o restante da página e o drawer permanece acima dele. A folha inferior mobile existente permanece até 760 px.
- A versão integra o nome dos caches. Ativação limpa apenas caches ARION anteriores e executa `clients.claim()`. Navegações continuam em rede; somente assets públicos permitidos e o shell neutro podem entrar no Cache Storage.
- O build cobre todos os `{% arion_js %}` encontrados nos templates: bundles próprios minificados, ofuscados, sem source maps e com fingerprint. O Service Worker é minificado e fingerprinted em saída especial, sem ofuscação para preservar seus eventos de ciclo de vida.
- Não há migration. O processo Worker de integração, OTRS FAST/FULL e CMDB FULL não foram alterados. Para produção, build, `collectstatic`, check e restart Web ainda precisam ocorrer no servidor.
- Node/npm não estão instalados neste PC; portanto o build de produção continua pendente para o servidor. A revisão de testes desta consolidação roda no SQLite local; testes específicos MySQL permanecem para schema isolado.
- As duas falhas históricas do Mapa foram fechadas: o detalhe do modo Projetos agora lista os Links CMDB do projeto/UF selecionados mesmo sem chamados associados; o teste de identidade foi atualizado para `BRADESCO`, saída canônica confirmada pelo serviço de identidade, em vez do rótulo antigo `Bradesco`.
- PASS — 242 testes das suítes de Configurações, Mapa, Chamados, Base, Painel e Rankings em SQLite. Após os ajustes finais do aviso, 31 testes direcionados de PWA e Documentação passaram novamente após o ajuste final do estado de versão.
- PASS — Chrome headless: página de releases e aviso sem overflow em 1920×1080, 1440×900, 1366×768, 1024×768, 390×844 e 320×720 nos temas claro/escuro; 12 combinações verificadas. “Depois” ocultou o aviso; o clique de atualização enviou `SKIP_WAITING` e houve um reload após `controllerchange`.
- PASS — atualização real do Service Worker local: os caches `arion-shell-*`/`arion-static-*` W44 foram substituídos pelos da nova versão; as URLs armazenadas permaneceram em `/static/`, sem dados operacionais.
- PASS — Django `check`, `makemigrations --check` (sem alterações), `py_compile` e `git diff --check`. Sem migration. Sem commit ou push.

### Validação final dos refinamentos PWA e Mapa

- PASS — 102 testes focados de PWA, Documentação, Configurações e Mapa; depois da correção do timeout de `controllerchange`, os 14 testes PWA foram executados novamente.
- PASS — Playwright com o JavaScript PWA real: dispensa do aviso mantendo indicador, `SKIP_WAITING`/`controllerchange` com um reload, erro recuperável para timeout e rejeição, nova tentativa, reload controlado sem worker em espera e ocultamento quando as versões coincidem.
- PASS — Playwright com o template real do Mapa em 1366×768, 1440×900, 1920×1080, 1024×768 e 390×844, nos temas claro/escuro: drawer desktop/tablet alinhado entre cabeçalho e base do viewport, larguras de 480/440 px, rolagem interna, filtros cobertos apenas na faixa direita, folha inferior mobile e sem overflow horizontal.

## Atualização de escopo em 29/10/2026 — revisão local, ainda não publicada

As notas dos FIXs anteriores descrevem o estado na data de cada correção. A revisão local posterior ampliou o escopo e passa a ser a referência para a próxima publicação:

- **Central de Relatórios / Claro Multas:** datas automáticas e efetivas ficam separadas; baseline automático é insumo, não decisão final. Pausas com motivo/evidência, TMP efetivo, overrides, decisão humana, auditoria e resolução legível de ISP foram documentados e implementados no workspace.
- **Cockpit V2:** contextos Suporte (`NOC - ATACADO`), Cancelamentos e Geral; processo de Cancelamentos classificado por fila/estado; agregação geral deduplicada; métricas de estoque, backlog, Aging, tempo médio, série temporal, granularidade e modos visuais foram ampliados. A série recebeu refinamentos de leitura, responsividade, rótulos e apresentação do backlog.
- **OTRS e sincronização:** FAST/FULL agora cobrem a fila de Suporte e as três filas operacionais de Cancelamentos. FULL reconcilia a cobertura selecionada; FAST exige a cobertura completa aplicada. OTRS continua somente leitura e as telas usam o mirror local.
- **Estado de sincronização:** zero operacional só é exibido com FULL aplicado para a cobertura esperada; fonte sem essa cobertura é apresentada como não sincronizada.
- **Roadmap:** a próxima frente é a evolução da página de Rankings, preservando o ranking absoluto e acrescentando análise planejada de exposição por Links e incidência proporcional. Requisitos e pendências estão em [Roadmap](..\01-projeto\roadmap.md).
- Esta revisão inclui a migration `0013_penaltytreatmentpause_evidence_and_more` e mudanças em OTRS FAST/FULL e `integration_worker`. Os blocos anteriores que diziam “sem migration” ou “Worker não mudou” continuam históricos para os FIXs a que se referiam; para a publicação futura deste conjunto, ver [Implantação e Produção](..\02-arquitetura\implantacao-e-producao.md).
- **Não publicado:** não houve deploy ou alteração de produção nesta revisão; a versão 1.1.0 em `releases.json` não foi substituída. Não fazer commit ou push como parte desta atualização.
