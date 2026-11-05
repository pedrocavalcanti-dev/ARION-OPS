# Estado atual do ARION OPS

Revisão estrutural em 05/11/2026 (Atualização incremental 1.3.1). A matriz descreve capacidades **IMPLEMENTADAS** no código, separadas explicitamente do que permanece **PLANEJADO** no [Roadmap](roadmap.md), sem publicar contagens voláteis de uma base específica.

Versão interna registrada em `docs/08-releases/releases.json`: **1.3.1**. A distribuição em produção depende do build e do procedimento de implantação do ambiente de destino.

## Matriz de módulos (IMPLEMENTADO)

| Módulo | Estado | Fonte | Escrita | Observação |
|---|---|---|---|---|
| Dashboard | IMPLEMENTADO | `CMDBLinkMirror`, `OTRSTicket`, relações e queries locais | Sem escrita CMDB/OTRS | KPIs atuais e séries por criação têm recortes diferentes. |
| Chamados | IMPLEMENTADO | Tickets atuais da fila NOC + `TicketTracking` | Acompanhamento apenas no ARION OPS | OTRS somente leitura. |
| Cockpit | IMPLEMENTADO | Espelhos OTRS/CMDB; filas de Suporte e Cancelamentos | `SavedAnalyticsView` local; exportação de leitura | Contextos Suporte, Cancelamentos e Geral; classificação por etapa e Data Scope no backend. |
| Mapa | IMPLEMENTADO | Espelhos e relações Ticket–ConfigItem | Sem edição da fonte | Agrega por UF; não geocodifica. |
| Rankings | IMPLEMENTADO | Snapshot atual CMDB/Base Links + chamados válidos do espelho OTRS no período | Exportação de leitura | Exposição por links ativos e incidência proporcional; Índice ARION é operacional, não SLA nem prova de causalidade. |
| Qualidade Operacional | IMPLEMENTADO (Fases 1–4 + envio controlado de TQ) | OQs, checklist, contatos, ciclos, TQs locais e espelhos CMDB/OTRS | Registros locais auditados; envio Brevo somente por autorização e confirmação individual; sem escrita OTRS | Inclui alinhamento visual refinado na aba Tratativas, botões padronizados, resumo expansível, template institucional HTML/texto, pré-visualização, correlação por ID persistente no corpo (QID) e migrations 0014/0015 validadas. |
| Base | IMPLEMENTADO | `CMDBLinkMirror`, `CMDBOperatorMirror` | Consulta/exportação | Importador XLSX legado não é fluxo normal. |
| Central de Relatórios | IMPLEMENTADO | Original Claro e espelhos locais OTRS/CMDB em enriquecimento explícito | Tratativa, pausas, overrides, decisão e auditoria no ARION OPS | Cálculo automático é baseline; decisão final é humana. |
| ARI I.A | IMPLEMENTADO (Preview local) | Models locais de conversas, anexos e artefatos | Dados locais de conversa/anexo | Opera em modo Preview local; integração LLM externa permanece PLANEJADA. |
| CMDB Mirror | IMPLEMENTADO | ServiceUp Search/Get | Escrita no banco ARION OPS | Sincronizado por processo separado. |
| OTRS Mirror | IMPLEMENTADO | MySQL OTRS somente leitura; quatro filas do Cockpit | Escrita no banco ARION OPS | FAST incremental e FULL reconciliador; FAST exige FULL com cobertura completa vigente. |
| Worker | IMPLEMENTADO | `integration_worker` | Atualiza espelhos/estado | Requer processo ativo para freshness; agenda FULL quando a cobertura configurada muda. |
| RBAC / Data Scope | IMPLEMENTADO | Django Groups/Permissions e `GroupDataScope` | Administração protegida | Superuser técnico possui escopo global. |
| Auditoria | IMPLEMENTADO | `AuditEvent` via `AuditService` | Histórico local | Metadata sanitizada; não é debug log. |
| Central documental | IMPLEMENTADO | Markdown estruturado em `docs/` e catálogo em `docs/08-releases/releases.json` | Leitura | Inclui Atualizações e versões, páginas funcionais/arquiteturais e busca; páginas técnicas protegidas por `configuracoes.manage_groups`. |

A Central de Integrações administra APIs externas Brevo e Mapbox. A API Key Brevo é protegida por DPAPI e o teste usa apenas autenticação sem envio; a autorização administrativa de envio real permanece desabilitada por padrão. O Mapbox prioriza token público configurado no banco, com fallback legado controlado pelo ambiente.

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
