# Estado atual do ARION OPS

Revisão estrutural em 08/11/2026 (Atualização incremental 1.3.1). A matriz descreve capacidades **IMPLEMENTADAS** no código, separadas explicitamente do que permanece **PLANEJADO** no [Roadmap](roadmap.md), sem publicar contagens voláteis de uma base específica.

Versão interna registrada em `docs/08-releases/releases.json`: **1.3.1**. A distribuição em produção depende do build e do procedimento de implantação do ambiente de destino.

## Matriz de módulos (IMPLEMENTADO)

| Módulo | Estado | Fonte | Escrita | Observação |
|---|---|---|---|---|
| Dashboard | IMPLEMENTADO | `CMDBLinkMirror`, `OTRSTicket`, relações e queries locais | Sem escrita CMDB/OTRS | KPIs atuais e séries por criação têm recortes diferentes. |
| Chamados | IMPLEMENTADO | Tickets atuais da fila NOC + `TicketTracking` | Acompanhamento apenas no ARION OPS | OTRS somente leitura. |
| Cancelamentos | IMPLEMENTADO EM DEV; importação histórica executada em SQLite DEV | Entrada do KPI 1 OTRS, vínculos CMDB, registros locais e 162 cancelamentos históricos importados do ArionBroker | Seis KPIs (162 oficiais), triagem reconciliada do KPI 1 (2 entradas), Pendências por etapa (2), consulta separada de pendências abertas (93), tabelas compactas, menu Ações na camada superior, modais com atualização parcial, detalhe compacto por seções e 7 identificações históricas recuperadas do Excel (151 responsáveis, 172 autores de pendências, autoria de 1.813 eventos); status, tratativa, atribuição, movimentos e reabertura autorizada | Importação histórica ArionBroker executada e validada em SQLite DEV (162 processos, 1.813 eventos/fatos, 172 pendências, 421 obrigações financeiras, 2.147 referências). Origem histórica e responsável legado preservados sem interferir na operação OTRS/CMDB. Ver [Cancelamentos](../05-funcionalidades/cancelamentos.md) e [migração](../05-funcionalidades/cancelamentos-migracao-arionbroker.md). |
| Cockpit | IMPLEMENTADO | Espelhos OTRS/CMDB; filas de Suporte e Cancelamentos | `SavedAnalyticsView` local; exportação de leitura | Contextos Suporte, Cancelamentos e Geral; classificação por etapa e Data Scope no backend. |
| Mapa | IMPLEMENTADO | Espelhos e relações Ticket–ConfigItem | Sem edição da fonte | Agrega por UF; não geocodifica. |
| Rankings | IMPLEMENTADO | Snapshot CMDB atual + chamados validos OTRS | Consulta e exportacao de leitura | Score comparativo de 0-100, melhores/atencao/mais links, regras de amostra e periodos documentados; Data Scope no backend. |
| Qualidade Operacional | IMPLEMENTADO (Fases 1–4 + envio controlado de TQ) | OQs, checklist, contatos, ciclos, TQs locais e espelhos CMDB/OTRS | Registros locais auditados; envio Brevo somente por autorização e confirmação individual; sem escrita OTRS | Inclui alinhamento visual refinado na aba Tratativas, botões padronizados, resumo expansível, template institucional HTML/texto, pré-visualização, correlação por ID persistente no corpo (QID) e migrations 0014/0015 validadas. |
| Base | IMPLEMENTADO | `CMDBLinkMirror`, `CMDBOperatorMirror` | Consulta/exportação | Importador XLSX legado não é fluxo normal. |
| Central de Relatórios | IMPLEMENTADO | Original Claro e espelhos locais OTRS/CMDB em enriquecimento explícito | Tratativa, pausas, overrides, decisão e auditoria no ARION OPS | Cálculo automático é baseline; decisão final é humana. |
| ARI I.A | IMPLEMENTADO (Preview local) | Models locais de conversas, anexos e artefatos | Dados locais de conversa/anexo | Opera em modo Preview local; integração LLM externa permanece PLANEJADA. |
| CMDB Mirror | IMPLEMENTADO | ServiceUp Search/Get | Escrita no banco ARION OPS | Sincronizado por processo separado. |
| OTRS Mirror | IMPLEMENTADO | MySQL OTRS somente leitura; quatro filas do Cockpit | Escrita no banco ARION OPS | FAST incremental e FULL reconciliador; FAST exige FULL com cobertura completa vigente. |
| Worker | IMPLEMENTADO | `integration_worker` | Atualiza espelhos/estado | Requer processo ativo para freshness; agenda FULL quando a cobertura configurada muda. |
| RBAC / Data Scope | IMPLEMENTADO | Django Groups/Permissions e `GroupDataScope` | Administração protegida | Superuser técnico possui escopo global; Gestão de Acessos 2.0 adiciona convites, ciclo de senhas e bloqueio backend. |
| Auditoria | IMPLEMENTADO | `AuditEvent` via `AuditService` | Histórico local | Metadata sanitizada; não é debug log. |
| Central documental | IMPLEMENTADO | Markdown estruturado em `docs/` e catálogo em `docs/08-releases/releases.json` | Leitura | Inclui Atualizações e versões, páginas funcionais/arquiteturais e busca; páginas técnicas protegidas por `configuracoes.manage_groups`. |

A Central de Integrações administra APIs externas Brevo e Mapbox. As credenciais Brevo são protegidas por DPAPI e o teste de conexão não envia mensagens. O envio real deve ser autorizado para códigos de segurança; sem configuração ou autorização, o fluxo falha fechado. O Mapbox prioriza token público configurado no banco, com fallback legado controlado pelo ambiente.

A Gestão de Acessos 2.0 está implementada localmente: novos usuários são criados sem senha e recebem convite individual de 48 horas; a troca obrigatória de senha (inicial ou por expiração de 90 dias) valida nova senha e confirmação com mínimo de 8 caracteres diretamente no backend, sem exigência de código por e-mail no fluxo obrigatório; a política de renovação usa 90 dias configuráveis e o middleware bloqueia contas pendentes também nas APIs e sessões antigas. A migration `autenticacao.0001_initial` marca contas preexistentes sem mudar hashes, mas a política global permanece inativa até a ativação explícita e auditada pelo comando `ativar_politica_senhas`. O grupo de consulta `Gestão` é provisionado com permissões conservadoras. Não houve implantação ou alteração em produção.

No código atual, o espelho OTRS preserva `external_state_name` e normaliza Resolvido/Encerrado como `business_status=RESOLVED`; backlog e aging operacional consideram somente `OPEN` e `PENDING`. A migration de dados `0014_normalize_closed_operational_status` converte linhas locais `CLOSED` sem alterar datas ou estado bruto. A importação histórica de Cancelamentos do ArionBroker foi homologada e executada no SQLite DEV em 09/11/2026 com `--allow-sqlite-dev` após backup consistente (`arion_geo_dev-before-import-20261109-150029.sqlite3`, SHA-256 `bbb2c0c2140b338d60c70e8e5fe7f8f5d64f87c33a0dc043a35c3076cb25a5b9`), dry-run consistente e reconciliação integral de 2.147 referências (162 processos, 1.813 fatos, 172 pendências, 421 obrigações financeiras), preservando os 120 intakes e contas existentes. Integração diferencia saúde da conexão de falha de processamento; o supervisor Windows carrega `.env` da raiz com precedência do ambiente herdado e valida o processo filho.

A Visão geral pessoal está disponível para qualquer usuário autenticado. A permissão `configuracoes.view_dashboard` continua protegendo a Dashboard gerencial; sem ela, o portal não consulta nem exibe KPIs executivos. Contagens na Visão geral autorizada e no diagnóstico da Central de Integrações respeitam Data Scope antes das agregações.

A Central de Integrações organiza conexão, último teste, sincronização, registros, estado do worker e diagnósticos em tema verde e responsivo. Grupos e acessos oferecem busca, módulos recolhíveis, resumo de grants e aviso de alterações não salvas sem mudar as permissões provisionadas. Chamados abre com 50 itens por página e mantém paginação server-side.

O ranking inclui Score Operacional comparativo com amostra mínima e métricas normalizadas por exposição; SLA não é inferido. As páginas analíticas e de Chamados compartilham consultas locais escopadas, reduzem agregações repetidas e não carregam todos os tickets antecipadamente. A Gestão de Acessos acrescenta `autenticacao.0001_initial`, `configuracoes.0010_alter_groupdatascope_options` e `configuracoes.0011_seed_management_group`; a migration de dados `integracoes.0014_normalize_closed_operational_status` permanece parte do plano de publicação.

## Banco e fontes

**Autoria:** Concepção e desenvolvimento por **Pedro Cavalcanti** para apoio às operações do **NOC ATACADO** da ARION Serviços de Telecomunicações.

A configuração suporta MySQL local (padrão operacional e obrigatório em produção) e SQLite para contingência/testes isolados. Não publicar nome de schema produtivo, host privado, usuário, senha nem connection string. Consulte [Banco de Dados](..\02-arquitetura\banco-de-dados.md) para precedência e fallback.

A preparação de produção V1 inclui modo Django explícito (`ARION_ENV=production`), Waitress em loopback (`127.0.0.1`), supervisor Windows para Web/Worker, build de assets de produção, `collectstatic`, `production-check`, preparação do serviço Cloudflare Tunnel e backup MySQL. Consulte [Implantação e Produção](..\02-arquitetura\implantacao-e-producao.md).

OTRS e CMDB são as fontes oficiais externas. `CMDBLinkMirror`, `CMDBOperatorMirror`, `OTRSTicket` e `OTRSTicketConfigItem` são espelhos locais. `TicketTracking`, `QualityOrder`, `PenaltyCompetence`, `AuditEvent`, `GroupDataScope` e `UserPreference` são registros próprios do ARION OPS.

## Frontend responsivo e PWA

O frontend Django oferece navegação dedicada em desktop, tablet e mobile, com adaptações por módulo. O PWA possui manifest (`/manifest.webmanifest`), ícones oficiais, instalação Chromium/iOS, modo standalone e aviso de atualização sob confirmação explícita do usuário. A versão e as notas de release ficam em `docs/08-releases/releases.json`, que alimenta `/api/version/` e a página **Atualizações e versões**. Todos os entrypoints JS referenciados nos templates passam pelo build de produção (`npm run build`) com fingerprint, minificação e ofuscação em `arion_geo/static/arion_build/`; o Service Worker é minificado separadamente sem ofuscação e nunca armazena páginas autenticadas nem payloads operacionais em cache offline.

## O que permanece PLANEJADO (não anunciar como implementado)

- Automação de abertura de TQ por e-mail ou escrita automática de tickets/artigos/notas no OTRS.
- Playbooks automáticos por nível R1–R5 na Qualidade.
- Integração LLM externa ativa no módulo ARI I.A.
- Indicadores contratuais formais de SLA sem regra/fonte homologada.
- Geocodificação de endereços ou coordenadas exatas por instalação no Mapa.
- Persistência histórica completa da sequência de eventos `Move`/`StateUpdate` do OTRS por etapa.
- Infraestrutura distribuída (Redis, Celery, Kubernetes, load balancer ou microsserviços).

Estudos em `docs/07-estudos/` são snapshots históricos datados. Para a evolução prevista, consulte o [Roadmap](roadmap.md).
