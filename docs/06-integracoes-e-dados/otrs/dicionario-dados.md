# Dicionário de dados OTRS

## Como ler

O índice contém 80 pares únicos tabela/coluna encontrados em projeções, filtros ou seleção do código de estudo. Os tipos abaixo vêm do estudo local de schema datado de 23/10/2026, quando presentes; não são garantia do schema atual. “Não documentado” significa que esse snapshot sanitizado não registra o tipo, não que a coluna seja texto.

Identificadores id marcados PK e índices únicos foram observados no snapshot local. A validação de uso, transformação e destino é pelo código em services/otrs_sync.py, services/otrs_study.py e clients/otrs.py. Campos de artigo são tratados em services/tq_otrs_correlation.py.

| ID | Fonte | Campo | Tipo observado | Significado e papel | Transformação ou destino |
|---|---|---|---|---|---|
| OTRS-F001 | ticket | id | bigint(20) PK | ID interno estável do ticket | OTRSTicket.external_id, unique |
| OTRS-F002 | ticket | tn | varchar(50) unique | Número público exibido e usado na busca | OTRSTicket.ticket_number, unique |
| OTRS-F003 | ticket | title | varchar(255) | Título do ticket | OTRSTicket.title, limitado a 500 caracteres no mirror |
| OTRS-F004 | ticket | queue_id | int(11) | ID atual da fila | queue_external_id; o nome vem de queue.id |
| OTRS-F005 | ticket | ticket_state_id | smallint(6) | ID do estado atual | external_state_id; nome e tipo vêm de dimensões |
| OTRS-F006 | ticket | ticket_priority_id | smallint(6) | ID da prioridade | external_priority_id; nome resolvido por ticket_priority.id |
| OTRS-F007 | ticket | type_id | smallint(6) | ID do tipo de ticket | external_type_id; nome resolvido por ticket_type.id |
| OTRS-F008 | ticket | service_id | int(11) | ID do serviço | external_service_id; nome resolvido por service.id |
| OTRS-F009 | ticket | sla_id | int(11) | ID de SLA associado | external_sla_id; nome resolvido por sla.id, sem KPI de cumprimento |
| OTRS-F010 | ticket | responsible_user_id | int(11) | Identificador numérico do responsável OTRS | Mirror mantém ID, sem buscar nome de usuário |
| OTRS-F011 | ticket | customer_id | varchar(150) | Identificador externo de cliente | OTRSTicket.customer_id; dado potencialmente identificável |
| OTRS-F012 | ticket | timeout | int(11) | Campo temporal numérico da fonte | Mirror mantém número; unidade não interpretada no código |
| OTRS-F013 | ticket | until_time | int(11) | Campo temporal numérico da fonte | OTRSTicket.until_time; sem conversão para timestamp |
| OTRS-F014 | ticket | escalation_time | int(11) | Valor numérico de escalonamento | Mirror mantém número; não é duração recalculada |
| OTRS-F015 | ticket | escalation_update_time | int(11) | Campo numérico de escalonamento de atualização | OTRSTicket.escalation_update_time |
| OTRS-F016 | ticket | escalation_response_time | int(11) | Campo numérico de escalonamento de resposta | Mirror mantém número; não confirma SLA atingido |
| OTRS-F017 | ticket | escalation_solution_time | int(11) | Campo numérico de escalonamento de solução | Mirror mantém número; não confirma SLA atingido |
| OTRS-F018 | ticket | create_time | datetime | Criação do ticket na fonte | source_created_at; naive é interpretado no timezone Django |
| OTRS-F019 | ticket | change_time | datetime | Última mudança do ticket na fonte | source_changed_at; FAST usa como critério incremental |
| OTRS-F020 | ticket | user_id | int(11) | ID do criador, apenas no estudo delimitado | Não integra o sync nem o mirror; valores não documentados |
| OTRS-F021 | queue | id | int(11) PK | Identidade numérica da fila | Resolve queue_id; não é hard-coded no serviço |
| OTRS-F022 | queue | name | varchar(200) | Nome exato da fila | Filtro do sync e snapshot do nome no ticket |
| OTRS-F023 | ticket_state | id | smallint(6) PK | ID do estado | OTRSStateMapping.external_state_id |
| OTRS-F024 | ticket_state | name | varchar(200) | Estado bruto, por exemplo “closed successful” | external_state_name; normalização é separada |
| OTRS-F025 | ticket_state | type_id | smallint(6) | Tipo técnico do estado | Resolvido contra ticket_state_type; external_state_type |
| OTRS-F026 | ticket_state | valid_id | smallint(6) | Validade consultada somente pelo estudo | Não participa do status operacional sincronizado |
| OTRS-F027 | ticket_state_type | id | smallint(6) PK | Identidade do tipo técnico | Mapeia ticket_state.type_id |
| OTRS-F028 | ticket_state_type | name | varchar(200) | Nome do tipo técnico | external_state_type; não define sozinho a categoria ARION |
| OTRS-F029 | ticket_priority | id | smallint(6) PK | ID de prioridade | Resolvido em memória a partir de ticket.ticket_priority_id |
| OTRS-F030 | ticket_priority | name | varchar(200) | Nome da prioridade | OTRSTicket.priority_name |
| OTRS-F031 | ticket_type | id | smallint(6) PK | ID do tipo | Resolvido em memória a partir de ticket.type_id |
| OTRS-F032 | ticket_type | name | varchar(200) | Nome do tipo | OTRSTicket.type_name |
| OTRS-F033 | service | id | int(11) PK | ID do serviço | Resolvido em memória a partir de ticket.service_id |
| OTRS-F034 | service | name | varchar(200) | Nome do serviço | OTRSTicket.service_name |
| OTRS-F035 | sla | id | int(11) PK | ID de SLA | Resolvido em memória a partir de ticket.sla_id |
| OTRS-F036 | sla | name | varchar(200) | Nome do SLA associado | OTRSTicket.sla_name; não há cálculo de prazo cumprido |
| OTRS-F037 | link_relation | source_object_id | smallint(6) | ID do objeto no lado de origem | Interpretado com link_object; Ticket ou ConfigItem |
| OTRS-F038 | link_relation | source_key | varchar(50) | Chave do objeto de origem | Ticket ID ou ConfigItem ID conforme o objeto |
| OTRS-F039 | link_relation | target_object_id | smallint(6) | ID do objeto no lado de destino | Interpretado com link_object; direção normalizada em memória |
| OTRS-F040 | link_relation | target_key | varchar(50) | Chave do objeto de destino | Ticket ID ou ConfigItem ID conforme o objeto |
| OTRS-F041 | link_relation | type_id | smallint(6) | ID do tipo de relação | Resolve link_type.name = DependsOn |
| OTRS-F042 | link_relation | state_id | smallint(6) | ID do estado da relação | Resolve link_state.name = Valid; relações inválidas são ignoradas |
| OTRS-F043 | link_relation | create_time | datetime | Criação da relação | OTRSTicketConfigItem.source_created_at |
| OTRS-F044 | link_object | id | smallint(6) PK | ID do objeto polimórfico | Comparado ao ID resolvido pelo nome |
| OTRS-F045 | link_object | name | varchar(100) | Nome do objeto vinculado | O código exige Ticket e ITSMConfigItem |
| OTRS-F046 | link_type | id | smallint(6) PK | ID de tipo de relação | Usado para filtrar DependsOn |
| OTRS-F047 | link_type | name | varchar(50) | Nome do tipo de relação | Identificação por nome, sem ID fixo no código |
| OTRS-F048 | link_type | valid_id | smallint(6) | Validade consultada pelo estudo | Não selecionada pelo sync principal |
| OTRS-F049 | link_state | id | smallint(6) PK | ID do estado da relação | Usado para filtrar Valid |
| OTRS-F050 | link_state | name | varchar(50) | Nome do estado da relação | Identificação por nome, sem ID fixo no código |
| OTRS-F051 | link_state | valid_id | smallint(6) | Validade consultada pelo estudo | Não selecionada pelo sync principal |
| OTRS-F052 | dynamic_field | id | int(11) PK | ID do Dynamic Field | Liga definição e valor via field_id |
| OTRS-F053 | dynamic_field | name | varchar(200) | Nome técnico do campo | Busca CodigoFechamento, ticketResolvido e campo opcional de procedência |
| OTRS-F054 | dynamic_field | field_type | varchar(200) | Tipo do campo | Campo opcional de procedência só é aceito quando dropdown |
| OTRS-F055 | dynamic_field | object_type | varchar(100) | Tipo de objeto associado | Campos obrigatórios precisam ser do tipo Ticket |
| OTRS-F056 | dynamic_field_value | object_id | bigint(20) | ID do objeto dono do valor | Filtrado pelo ticket.id; não salvo como chave nova |
| OTRS-F057 | dynamic_field_value | field_id | int(11) | ID da definição do campo | Define o significado de value_text ou value_date |
| OTRS-F058 | dynamic_field_value | value_text | text | Texto de fechamento ou procedência | closing_code_raw; procedência raw e categoria normalizada |
| OTRS-F059 | dynamic_field_value | value_date | datetime | Data do campo ticketResolvido | resolved_at; conversão via source_datetime |
| OTRS-F060 | ticket_history | history_type_id | smallint(6) | Tipo do evento de histórico | Filtra o ID cujo nome é StateUpdate |
| OTRS-F061 | ticket_history | ticket_id | bigint(20) | Ticket associado ao evento | Agrupa histórico pelo ticket |
| OTRS-F062 | ticket_history | state_id | smallint(6) | Estado associado ao evento | Limita estados terminais e confere estado atual |
| OTRS-F063 | ticket_history | create_time | datetime | Data do evento de estado | MAX(create_time) vira terminal_state_at se corresponder ao estado atual |
| OTRS-F064 | ticket_history | queue_id | int(11) | Fila do evento, opcional no estudo | Conta passagens se houver índice em queue_id |
| OTRS-F065 | ticket_history_type | id | smallint(6) PK | ID do tipo de evento | Resolve StateUpdate pelo nome |
| OTRS-F066 | ticket_history_type | name | varchar(200) | Nome do tipo de evento | Sync exige correspondência única StateUpdate |
| OTRS-F067 | article | id | bigint(20) PK | Identidade e cursor crescente do artigo | QualityCorrelationCheckpoint.last_article_id; não vai ao mirror de ticket |
| OTRS-F068 | article | ticket_id | bigint(20) | Ticket dono do artigo | JOIN de leitura com ticket.id |
| OTRS-F069 | article | create_time | datetime | Data do artigo | Selecionada na reconciliação manual; não é data de resolução |
| OTRS-F070 | article | communication_channel_id | bigint(20) | Canal do artigo, estudo agregado | Somente contagem no estudo |
| OTRS-F071 | article | article_sender_type_id | smallint(6) | Tipo de remetente no estudo agregado | Traduzido por article_sender_type |
| OTRS-F072 | article_data_mime | article_id | bigint(20) | Liga dados MIME ao artigo | JOIN com article.id |
| OTRS-F073 | article_data_mime | a_body | Não documentado no snapshot | Corpo que pode conter o QID | Só no fluxo TQ, em memória para parser; não persistido ou logado |
| OTRS-F074 | article_sender_type | id | smallint(6) PK | ID do tipo de remetente | Traduz article.article_sender_type_id no estudo |
| OTRS-F075 | article_sender_type | name | varchar(200) | Rótulo do remetente | Resultado agregado do estudo; não integra o mirror |
| OTRS-F076 | configitem | id | bigint(20) PK | Identidade do ConfigItem | Classifica chave vinculada sem copiar o objeto CMDB |
| OTRS-F077 | configitem | class_id | int(11) | ID de classe do ConfigItem | JOIN de estudo com general_catalog.id |
| OTRS-F078 | general_catalog | id | int(11) PK | ID de item do catálogo | Liga configitem.class_id ao nome |
| OTRS-F079 | general_catalog | general_catalog_class | varchar(100) | Namespace da classe | Filtra ITSM::ConfigItem::Class |
| OTRS-F080 | general_catalog | name | varchar(100) | Nome da classe | Resultado do estudo; não é atributo espelhado |

## Campos locais derivados

business_status, is_business_open, closing_code_raw, responsibility_normalized, resolved_at, terminal_state_at, canceled_at, source_last_seen_at, source_synced_at e flags de presença são campos locais calculados ou administrativos. resolved_at deriva do Dynamic Field ticketResolvido; terminal_state_at deriva do histórico StateUpdate. Não há evidência de que esses nomes de destino existam no schema OTRS.

source_changed_at é o carimbo ticket.change_time; o cursor global é persistido em IntegrationSyncState.change_cursor. Veja [espelho](otrs-espelho-local.md), [datas](otrs-datas.md) e [rastreabilidade](otrs-rastreabilidade.md).
