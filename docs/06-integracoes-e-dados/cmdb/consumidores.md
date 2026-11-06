# 14 · Módulos consumidores e consultas locais

Os consumidores leem os espelhos locais; telas não chamam a ServiceUp em cada request. CMDB-Q001–Q008 identificam oito famílias representativas de consulta Django/ORM documentadas aqui. Os IDs não significam SQL literal e não representam toda consulta presente no repositório.

## Módulos consumidores

| Área | Origem/rule | Uso dos dados CMDB |
|---|---|---|
| Base de Links | aplicativos/base_links/views.py; LinkQueryService.base | Lista, filtro, detalhe, Provider/ISP e exportação do mirror. O modelo CMDBLink separado serve ao fluxo histórico XLSX. |
| Chamados | aplicativos/chamados/views.py e services.py | Escopa tickets pelas relações presentes a Links autorizados; mostra contagens e designações quando CI resolve. |
| Dashboard | aplicativos/painel/services/analytics.py | Usa universo de Links locais com tickets, filtros e métricas de operação. |
| Cockpit | APIs/visões analíticas do app painel e LinkQueryService/TicketQueryService | Cruza tickets OTRS com ConfigItems e agrega ISP, projeto e circuitos sem chamada remota por consulta. |
| Rankings | aplicativos/rankings/services.py · RankingService.build | Inventário por Provider, links ativos, ticket/100 links, Links afetados, concentração e score no período. |
| Mapa | aplicativos/mapa/services/analytics.py | Agrega links por UF/cidade e cruza relações OTRS para chamadas, reincidência e motivos. Geometria é por estado, não coordenada da CMDB. |
| Qualidade | aplicativos/qualidade/services/* | Escopo de Links, ordens, ocorrências, reincidência, identificação do circuito e Provider. |
| Relatórios | aplicativos/relatorios/services/treatment.py | Enriquecimento local de linhas por ticket, relação ConfigItem, ISP e link; mantém candidatos ambíguos explícitos. |

### CMDB-Q001 — Universo elegível de Links

Arquivo: integracoes/services/analytics.py, LinkQueryService.base. Predicado principal is_present_in_source=True; select_related operator; para usuário, aplica DataScopeService antes dos filtros e contagens. Origina o universo para visões de links.

### CMDB-Q002 — Resolução de Provider em lote

Arquivo: integracoes/services/semantics.py, provider_values_for_links/provider_names_for_values. Usa ISP raw, source_config_item_id e, para compatibilidade, PK local numérica; mantém valor original e não converte ID sem correspondência em nome. Resultado alimenta exibição, filtros e relatórios.

### CMDB-Q003 — Inventário e métricas do Ranking

Arquivo: rankings/services.py, RankingService.build. Materializa os Links já escopados e indexa por Provider. Total do inventário é len(links); subconjunto nomeado por Provider requer FK/nome resolvido. Tickets são coletados das relações e deduplicados por ID; a janela e os filtros do ticket são próprios do Ranking.

### CMDB-Q004 — Agregações do Mapa

Arquivo: mapa/services/analytics.py, MapAnalyticsService.query. Primeiro aplica Data Scope, depois cliente, projeto, Provider e UF. Agrega Links e relações de tickets por dimensão geográfica; estados e cidades são normalizados para comparação/apresentação. Há modos base, projects, calls, repeat e motives.

### CMDB-Q005 — Resolução da relação OTRS Ticket–CI

Arquivo: integracoes/services/otrs_sync.py, _upsert_relations. Deduplica por ticket externo + ConfigItemID + tipo; tenta FK por igualdade entre ConfigItemID da relação e source_config_item_id no Link. Relação pode persistir sem Link local.

### CMDB-Q006 — Tickets e Links afetados

Arquivo: integracoes/services/analytics.py, ticket_counts_by_link, ticket_link_relations e count_affected_links. Restringe relação presente, Link presente e universo de tickets válido; usa Count distinct de ticket_id ou cmdb_link_id para evitar multiplicação.

### CMDB-Q007 — Fila de Links prioritários/recorrentes

Arquivo: qualidade/services/priority_links.py e recurrence.py. Parte dos Links presentes e escopados, combina OTRS tickets/relações, QualityOccurrence e QualityOrder, aplica intervalo e prioridade e expõe estado/contagem por Link. A ordenação de atenção não modifica a regra de recorrência.

### CMDB-Q008 — Enriquecimento do relatório de competência

Arquivo: relatorios/services/treatment.py, enrich_competence e cmdb_isp_identity. Busca local por ticket/ConfigItem e resolve Provider através do mirror. remote_lookup é parâmetro opt-in; o fluxo padrão local não faz chamada à ServiceUp.

## CMDB-T001–T008 — Transformações

| ID | Operação | Evidência e destino |
|---|---|---|
| CMDB-T001 | Descompactar atributos | cmdb_sync._flatten_attributes lê folhas de CIXMLData em JSON ou XML; resultado alimenta extração por nome. |
| CMDB-T002 | Sanitizar dados recebidos | CMDBClient.sanitize_study_items protege valores sensíveis e resume estruturas; dados de exibição usam sanitize_items. |
| CMDB-T003 | Escolher valor de origem | _item_fields/_value usam campo direto quando preenchido, depois CIXMLData; normalizam a chave sem diferenciar caixa. |
| CMDB-T004 | Classificar identidade | classify_link_name deriva cliente/projeto/conta/Business ID/confiança de Name sem alterar Name. |
| CMDB-T005 | Resolver ISP | _link tenta ConfigItemID de Operadoras, depois nome exato único; mantém isp_raw e código de resolução. |
| CMDB-T006 | Preservar projeto legado | run associa legacy_project existente ao mesmo source_config_item_id e marca LEGACY_XLSX; não o recalcula da fonte atual. |
| CMDB-T007 | Validar snapshot | fetch/run exige IDs completos e únicos, classe certa, campos identificadores e Numbers sem colisão antes do upsert. |
| CMDB-T008 | Reconciliar presença | _upsert atualiza dados/freshness, marca vistos como presentes e não vistos como ausentes sem apagar registros. |

Essas consultas representam pontos de entrada para rastrear comportamento, não um catálogo exaustivo de cada queryset secundário. Consulte [Indicadores](cmdb-indicadores.md), [Qualidade](cmdb-qualidade.md) e [Rastreabilidade](cmdb-rastreabilidade.md).
