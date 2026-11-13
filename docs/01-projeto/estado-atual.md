# Estado atual do ARION OPS

Revisão em 12/11/2026 (código 1.4.0). A matriz descreve capacidades **IMPLEMENTADAS**, separadas do que permanece **PLANEJADO** no [Roadmap](roadmap.md). Contagens DEV de Cancelamentos são evidência datada de validação, não valores fixos da aplicação.

Versão registrada em `docs/08-releases/releases.json`: **1.4.0**, com tag anotada `v1.4.0` preservada. A evolução GEO Intelligence está implementada em DEV, com refinamento [candidato à 1.4.1](../08-releases/v1.4.1-candidata.md), sem alterar a versão anunciada antes da homologação. O [Dashboard Gerencial de Cancelamentos](../05-funcionalidades/cancelamentos-dashboard.md) está implementado. **Publicação não comprova implantação.** O servidor conhecido usa Windows Server/MySQL, domínio público e Web/Worker; não foi acessado nesta revisão. Atualização e carga histórica dependem do [checklist próprio](../08-releases/v1.4.0-implantacao-windows.md).

## Matriz de módulos (IMPLEMENTADO)

| Módulo | Estado | Fonte | Escrita | Observação |
|---|---|---|---|---|
| Dashboard | IMPLEMENTADO | `CMDBLinkMirror`, `OTRSTicket`, relações e queries locais | Sem escrita CMDB/OTRS | KPIs atuais e séries por criação têm recortes diferentes. |
| Chamados | IMPLEMENTADO | Tickets atuais da fila NOC + `TicketTracking` | Acompanhamento apenas no ARION OPS | OTRS somente leitura. |
| Cancelamentos | IMPLEMENTADO EM DEV; histórico reconciliado em SQLite DEV | Entrada do KPI 1 OTRS, vínculos CMDB, registros locais e 162 processos históricos do ArionBroker | 11 Pedidos, 5 Solicitações, 3 Pendências e 143 Concluídos; 2 entradas aguardando triagem; consulta separada de 95 processos com pendências abertas. Tabelas compactas, ações e modais, detalhe por seções, nomes históricos, total informado independente de pagamento, atribuição e fluxo autorizados preservados | Reconciliação posterior por Excel com preview, revisão e replay idempotente: 177 pendências, 426 obrigações, 1.819 eventos e 2.156 referências, incluindo as 2.147 evidências originais imutáveis. Responsável atual independente. Transferência integral para candidato MySQL vazio preparada, ainda sem ensaio MySQL ou deploy. Ver [Cancelamentos](../05-funcionalidades/cancelamentos.md), [migração histórica](../05-funcionalidades/cancelamentos-migracao-arionbroker.md) e [transferência completa](../06-integracoes-e-dados/migracao-completa-arion-mysql.md). |
| Cockpit | IMPLEMENTADO | Espelhos OTRS/CMDB; filas de Suporte e Cancelamentos | `SavedAnalyticsView` local; exportação de leitura | Contextos Suporte, Cancelamentos e Geral; classificação por etapa e Data Scope no backend. |
| Mapa / GEO Intelligence | IMPLEMENTADO EM DEV | Espelhos OTRS/CMDB, catálogo IBGE e cache Open-Meteo | Caches e jobs locais; sem edição da fonte | Cabeçalho GEO com navegação/Apresentar/perfil, legenda superior, resumo contextual esquerdo e superfície direita exclusiva para análises/filtros/detalhes (380–460 px). Análises recolhidas por padrão, siglas nacionais, timeline por modo e apresentação com cliente fixado, projetos pertinentes e catálogos protegidos no backend. Coincidência factual exige classificação técnica conhecida. Central GEO modernizada, bootstrap público sem chave e fontes comercial/self-hosted configuráveis. Coleta real de outubro/2026 no SQLite DEV: 27/27 UFs, 373 células e 100% da área da quadratura/completude em outubro/2026. Dez jobs retomados com 19 HTTP para 130 localizações; orçamento existente preservado. Multi-coordenadas, grade persistente, consumo separado e NASA POWER diária validada em uma célula; aquisição CDS preparada, conversão/importação pendentes. São Paulo/SP possui também cache municipal diário/horário real. Fila nacional com prévia e controles; ícones, rankings climáticos, filtros dependentes e painel direito. Histórico permanente com consolidação após janela da fonte, incrementos diários/horários, revisão manual versionada e inventário na Central. Migrations aditivas integracoes.0018–0021. Worker existente observado ativo no DEV; MySQL DEV pendente. Ver [GEO Intelligence](../05-funcionalidades/geo-intelligence.md). |
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

A Gestão de Acessos 2.0 cria contas sem senha utilizável, com convite **compartilhado manualmente**, válido por 48 horas desde emissão. Renovação obrigatória usa prazo configurável de 90 dias, nova senha/confirmacão e mínimo de oito caracteres, sem OTP nessa rota. O middleware bloqueia também APIs/sessões anteriores. Migration cria a política inativa, mas a operação informou **ativação prévia no servidor conhecido**, ainda não consultada nesta auditoria. Não repetir ativação global em deploy. O grupo Gestão tem defaults gerais de consulta, mas `view_reports` também libera criação/upload/importação: ver limitações abaixo.

No código atual, o espelho OTRS preserva `external_state_name` e normaliza Resolvido/Encerrado como `business_status=RESOLVED`; backlog e aging do Cockpit consideram somente `OPEN` e `PENDING`; o aging da fila no Dashboard inclui também `SCHEDULED`, como a fila Chamados. A migration de dados `0014_normalize_closed_operational_status` converte linhas locais `CLOSED` sem alterar datas ou estado bruto. A importação histórica de Cancelamentos do ArionBroker foi homologada e executada no SQLite DEV em 09/11/2026 com `--allow-sqlite-dev` após backup consistente (`arion_geo_dev-before-import-20261109-150029.sqlite3`, SHA-256 `bbb2c0c2140b338d60c70e8e5fe7f8f5d64f87c33a0dc043a35c3076cb25a5b9`), dry-run consistente e reconciliação integral de 2.147 referências (162 processos, 1.813 fatos, 172 pendências, 421 obrigações financeiras), preservando os 120 intakes e contas existentes. Integração diferencia saúde da conexão de falha de processamento; o supervisor Windows carrega `.env` da raiz com precedência do ambiente herdado e valida o processo filho.

A Visão geral pessoal está disponível para qualquer usuário autenticado. A permissão `configuracoes.view_dashboard` continua protegendo a Dashboard gerencial; sem ela, o portal não consulta nem exibe KPIs executivos. Contagens na Visão geral autorizada e no diagnóstico da Central de Integrações respeitam Data Scope antes das agregações.

A Central de Integrações organiza conexão, último teste, sincronização, registros, estado do worker e diagnósticos em tema verde e responsivo. Grupos e acessos oferecem busca, módulos recolhíveis, resumo de grants e aviso de alterações não salvas sem mudar as permissões provisionadas. Chamados abre com 50 itens por página e mantém paginação server-side.

O ranking inclui Score Operacional comparativo com amostra mínima e métricas normalizadas por exposição; SLA não é inferido. As páginas analíticas e de Chamados compartilham consultas locais escopadas, reduzem agregações repetidas e não carregam todos os tickets antecipadamente. A Gestão de Acessos acrescenta `autenticacao.0001_initial`, `configuracoes.0010_alter_groupdatascope_options` e `configuracoes.0011_seed_management_group`; a migration de dados `integracoes.0014_normalize_closed_operational_status` permanece parte do plano de publicação.

O Cockpit Suporte reconstrói a evolução de backlog pelos eventos reais de estado, fila e procedência OTRS, incluindo reaberturas e tickets que saíram da fila. A migration aditiva `integracoes.0016` cria o cache de intervalos; `sincronizar_historico_backlog` e o worker existente atualizam somente essa base local. A série reconstruída é distinta das capturas FULL, preserva lacunas como indisponíveis e aplica Data Scope antes das contagens. Detalhes e limitações estão na [documentação do Cockpit](../05-funcionalidades/cockpit.md).

## Banco e fontes

**Autoria:** Concepção e desenvolvimento por **Pedro Cavalcanti** para apoio às operações do **NOC ATACADO** da ARION Serviços de Telecomunicações.

A configuração exige MySQL local em produção; o checkout DEV atual utiliza SQLite. Não há fallback automático de produção para SQLite nem homologação MySQL obtida por testes SQLite. Não publicar host privado, usuário, senha ou connection string. Consulte [Banco de Dados](..\02-arquitetura\banco-de-dados.md) para precedência e bootstrap.

A preparação de produção V1 inclui modo Django explícito (`ARION_ENV=production`), Waitress em loopback (`127.0.0.1`), supervisor Windows para Web/Worker, build de assets de produção, `collectstatic`, `production-check`, preparação do serviço Cloudflare Tunnel e backup MySQL. Consulte [Implantação e Produção](..\02-arquitetura\implantacao-e-producao.md).

OTRS e CMDB são as fontes oficiais externas. `CMDBLinkMirror`, `CMDBOperatorMirror`, `OTRSTicket` e `OTRSTicketConfigItem` são espelhos locais. `TicketTracking`, `QualityOrder`, `PenaltyCompetence`, `AuditEvent`, `GroupDataScope` e `UserPreference` são registros próprios do ARION OPS.

## Frontend responsivo e PWA

O frontend Django oferece navegação dedicada em desktop, tablet e mobile, com adaptações por módulo. O PWA possui manifest (`/manifest.webmanifest`), ícones oficiais, instalação Chromium/iOS, modo standalone e aviso de atualização sob confirmação explícita do usuário. A versão e as notas de release ficam em `docs/08-releases/releases.json`, que alimenta `/api/version/` e a página **Atualizações e versões**. Todos os entrypoints JS referenciados nos templates passam pelo build de produção (`npm run build`) com fingerprint, minificação e ofuscação em `arion_geo/static/arion_build/`; o Service Worker é minificado separadamente sem ofuscação e nunca armazena páginas autenticadas nem payloads operacionais em cache offline.

## O que permanece PLANEJADO (não anunciar como implementado)

Antes de ampliar acessos ou fazer novo rollout, tratar as pendências verificadas na [auditoria documental 1.4.0](../08-releases/v1.4.0-auditoria-documental.md): Relatórios não segrega competências por Data Scope e `view_reports` inclui escrita de importação; a permission de contatos da Qualidade existe mas falta no seletor central; o comando global de senhas não exclui convites pendentes da coorte. Carga histórica produtiva é bloqueada pelo importador atual e MySQL de ensaio ainda precisa de homologação. Estas pendências não foram corrigidas por mudanças em Markdown.

- Automação de abertura de TQ por e-mail ou escrita automática de tickets/artigos/notas no OTRS.
- Playbooks automáticos por nível R1–R5 na Qualidade.
- Integração LLM externa ativa no módulo ARI I.A.
- Indicadores contratuais formais de SLA sem regra/fonte homologada.
- Geocodificação de endereços ou coordenadas exatas por instalação no Mapa.
- Persistência histórica completa da sequência de eventos `Move`/`StateUpdate` do OTRS por etapa.
- Infraestrutura distribuída (Redis, Celery, Kubernetes, load balancer ou microsserviços).

Estudos em `docs/07-estudos/` são snapshots históricos datados. Para a evolução prevista, consulte o [Roadmap](roadmap.md).
