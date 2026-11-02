# Estudo real do OTRS — NOC ATACADO

## Resumo executivo

- Execução: somente leitura; duração 10.91 s.
- Fila solicitada: `NOC - ATACADO`; status: **encontrada por nome exato**; queue id: `107`.
- Tickets atualmente na fila: **11960** (atual por ticket.queue_id).
- Relação Ticket ↔ ConfigItem: **confirmada**.
- Cross-check CMDB: amostra real; amostra `50`; chave observada: `ConfigItemID`.
- Este estudo não alterou a Base Arion nem conectou tickets às métricas operacionais.

## Ambiente

- Banco: `otrs`
- Servidor: `10.6.22-MariaDB-0ubuntu0.22.04.1`
- Charset/collation: `utf8mb3` / `utf8mb3_general_ci`
- Tabelas e views visíveis: 176
- Gerado em: 2026-10-23 17:30:01 UTC

## Metodologia e segurança

Consultas sequenciais pelo `OTRSReadOnlyClient` existente; sessão MySQL declarada somente leitura. O executor aceita somente um `SELECT`, sem instruções de escrita ou efeitos colaterais. Nomes usados em SQL são obtidos do metadata do schema. Não foi consultada a coluna `users.pw`, nem selecionados corpos de articles, anexos ou credenciais. Amostras de conteúdo bruto não foram incluídas neste relatório.

## Estrutura descoberta

### Tabelas candidatas

| Tabela/View | Tipo | Linhas estimadas | Correspondência |
|---|---|---:|---|
| `article` | BASE TABLE | 803661 | article |
| `article_data_mime` | BASE TABLE | 924666 | article |
| `article_data_mime_attachment` | BASE TABLE | 1456 | article |
| `article_data_mime_plain` | BASE TABLE | 558 | article |
| `article_data_mime_send_error` | BASE TABLE | 2007 | article |
| `article_data_otrs_chat` | BASE TABLE | 0 | article |
| `article_flag` | BASE TABLE | 2800468 | article |
| `article_search_index` | BASE TABLE | 2928427 | article |
| `article_sender_type` | BASE TABLE | 4 | type, article |
| `auto_response_type` | BASE TABLE | 5 | type |
| `calendar_appointment_ticket` | BASE TABLE | 0 | ticket |
| `cloud_service_config` | BASE TABLE | 0 | service, config |
| `communication_log_object` | BASE TABLE | 5497 | object |
| `communication_log_object_entry` | BASE TABLE | 71487 | object |
| `configitem` | BASE TABLE | 11166 | config, item |
| `configitem_counter` | BASE TABLE | 13 | config, item |
| `configitem_definition` | BASE TABLE | 30 | config, item |
| `configitem_history` | BASE TABLE | 175000 | history, config, item |
| `configitem_history_type` | BASE TABLE | 13 | type, history, config, item |
| `configitem_version` | BASE TABLE | 31001 | config, item |
| `customer_company` | BASE TABLE | 1268 | customer |
| `customer_portal_cards` | BASE TABLE | 2 | customer |
| `customer_portal_inquiry_forms` | BASE TABLE | 0 | customer |
| `customer_portal_notifications` | BASE TABLE | 0 | customer |
| `customer_preferences` | BASE TABLE | 3634 | customer |
| `customer_user` | BASE TABLE | 641 | customer |
| `customer_user_azure_mirror` | BASE TABLE | 0 | customer |
| `customer_user_customer` | BASE TABLE | 0 | customer |
| `custom_form_dynamic_field` | BASE TABLE | 2 | dynamic, field |
| `custom_form_service` | BASE TABLE | 308 | service |
| `dynamic_field` | BASE TABLE | 221 | dynamic, field |
| `dynamic_field_obj_id_name` | BASE TABLE | 8 | dynamic, field |
| `dynamic_field_value` | BASE TABLE | 1543216 | dynamic, field |
| `faq_history` | BASE TABLE | 266 | history |
| `faq_item` | BASE TABLE | 53 | item |
| `faq_state` | BASE TABLE | 3 | state |
| `faq_state_type` | BASE TABLE | 3 | state, type |
| `general_catalog` | BASE TABLE | 138 | catalog |
| `general_catalog_preferences` | BASE TABLE | 50 | catalog |
| `gi_webservice_config` | BASE TABLE | 7 | service, config |
| `gi_webservice_config_history` | BASE TABLE | 43 | service, history, config |
| `group_customer` | BASE TABLE | 0 | customer |
| `group_customer_user` | BASE TABLE | 0 | customer |
| `imexport_mapping_object` | BASE TABLE | 153 | object |
| `imexport_object` | BASE TABLE | 25 | object |
| `link_object` | BASE TABLE | 5 | link, object |
| `link_relation` | BASE TABLE | 25411 | link, relation |
| `link_state` | BASE TABLE | 2 | state, link |
| `link_type` | BASE TABLE | 7 | type, link |
| `mail_queue` | BASE TABLE | 0 | queue |
| `notification_event_item` | BASE TABLE | 820 | item |
| `oauth2_token_config` | BASE TABLE | 0 | config |
| `personal_queues` | BASE TABLE | 80 | queue |
| `personal_services` | BASE TABLE | 0 | service |
| `queue` | BASE TABLE | 180 | queue |
| `queue_auto_response` | BASE TABLE | 95 | queue |
| `queue_preferences` | BASE TABLE | 0 | queue |
| `queue_standard_template` | BASE TABLE | 217 | queue |
| `service` | BASE TABLE | 411 | service |
| `service_customer_user` | BASE TABLE | 440 | service, customer |
| `service_preferences` | BASE TABLE | 3926 | service |
| `service_sla` | BASE TABLE | 173 | service, sla |
| `sla` | BASE TABLE | 41 | sla |
| `sla_preferences` | BASE TABLE | 0 | sla |
| `smime_signer_cert_relations` | BASE TABLE | 0 | relation |
| `survey_queue` | BASE TABLE | 42 | queue |
| `sysconfig_default` | BASE TABLE | 3248 | config |
| `sysconfig_default_version` | BASE TABLE | 3202 | config |
| `sysconfig_deployment` | BASE TABLE | 21 | config |
| `sysconfig_deployment_lock` | BASE TABLE | 0 | config |
| `sysconfig_modified` | BASE TABLE | 252 | config |
| `sysconfig_modified_version` | BASE TABLE | 487 | config |
| `temp_config_item` | BASE TABLE | 2814 | config, item |
| `temp_select_config_item` | BASE TABLE | 16095 | config, item |
| `ticket` | BASE TABLE | 106546 | ticket |
| `ticket_flag` | BASE TABLE | 77711 | ticket |
| `ticket_history` | BASE TABLE | 7689816 | ticket, history |
| `ticket_history_type` | BASE TABLE | 51 | ticket, type, history |
| `ticket_index` | BASE TABLE | 0 | ticket |
| `ticket_invoker_oauth` | BASE TABLE | 0 | ticket |
| `ticket_lock_index` | BASE TABLE | 0 | ticket |
| `ticket_lock_type` | BASE TABLE | 3 | ticket, type |
| `ticket_loop_protection` | BASE TABLE | 43 | ticket |
| `ticket_number_counter` | BASE TABLE | 10 | ticket |
| `ticket_priority` | BASE TABLE | 5 | ticket, priority |
| `ticket_state` | BASE TABLE | 31 | ticket, state |
| `ticket_state_type` | BASE TABLE | 7 | ticket, state, type |
| `ticket_type` | BASE TABLE | 4 | ticket, type |
| `ticket_watcher` | BASE TABLE | 0 | ticket |

## Fila NOC - ATACADO

- Tabela: `queue`
- Queue ID: `107`
- Resolução: encontrada por nome exato
- Candidatos quando não houve igualdade exata: não aplicável; correspondência exata encontrada
- Tickets atuais: 11960

## Ticket

- Tabela: `ticket`
- PK/campo de identificação: `id`
- Colunas e tipos:

| Coluna | Tipo | Nulo | Chave |
|---|---|---|---|
| `id` | bigint(20) | NO | PRI |
| `tn` | varchar(50) | NO | UNI |
| `title` | varchar(255) | YES | MUL |
| `queue_id` | int(11) | NO | MUL |
| `ticket_lock_id` | smallint(6) | NO | MUL |
| `type_id` | smallint(6) | YES | MUL |
| `service_id` | int(11) | YES | MUL |
| `sla_id` | int(11) | YES | MUL |
| `user_id` | int(11) | NO | MUL |
| `responsible_user_id` | int(11) | NO | MUL |
| `ticket_priority_id` | smallint(6) | NO | MUL |
| `ticket_state_id` | smallint(6) | NO | MUL |
| `customer_id` | varchar(150) | YES | MUL |
| `customer_user_id` | varchar(250) | YES | MUL |
| `timeout` | int(11) | NO | MUL |
| `until_time` | int(11) | NO | MUL |
| `escalation_time` | int(11) | NO | MUL |
| `escalation_update_time` | int(11) | NO | MUL |
| `escalation_response_time` | int(11) | NO | MUL |
| `escalation_solution_time` | int(11) | NO | MUL |
| `archive_flag` | smallint(6) | NO | MUL |
| `create_time` | datetime | NO | MUL |
| `create_by` | int(11) | NO | MUL |
| `change_time` | datetime | NO | — |
| `change_by` | int(11) | NO | MUL |

Índices observados:

| Índice | Coluna | Único |
|---|---|---|
| `FK_ticket_change_by_id` | `change_by` | não |
| `FK_ticket_create_by_id` | `create_by` | não |
| `FK_ticket_service_id_id` | `service_id` | não |
| `FK_ticket_sla_id_id` | `sla_id` | não |
| `PRIMARY` | `id` | sim |
| `ticket_archive_flag` | `archive_flag` | não |
| `ticket_create_time` | `create_time` | não |
| `ticket_customer_id` | `customer_id` | não |
| `ticket_customer_user_id` | `customer_user_id` | não |
| `ticket_escalation_response_time` | `escalation_response_time` | não |
| `ticket_escalation_solution_time` | `escalation_solution_time` | não |
| `ticket_escalation_time` | `escalation_time` | não |
| `ticket_escalation_update_time` | `escalation_update_time` | não |
| `ticket_queue_id` | `queue_id` | não |
| `ticket_queue_view` | `ticket_state_id` | não |
| `ticket_queue_view` | `ticket_lock_id` | não |
| `ticket_responsible_user_id` | `responsible_user_id` | não |
| `ticket_ticket_lock_id` | `ticket_lock_id` | não |
| `ticket_ticket_priority_id` | `ticket_priority_id` | não |
| `ticket_ticket_state_id` | `ticket_state_id` | não |
| `ticket_timeout` | `timeout` | não |
| `ticket_title` | `title` | não |
| `ticket_tn` | `tn` | sim |
| `ticket_type_id` | `type_id` | não |
| `ticket_until_time` | `until_time` | não |
| `ticket_user_id` | `user_id` | não |

## Estados

Tabela de estados: `ticket_state`. Distribuição atual da fila por nome:

- `closed successful`: 10930
- `cancelado`: 985
- `Encerrado com viabilidade`: 1
- `em atendimento`: 19
- `resolvido`: 22
- `Pendente`: 3

Distribuição por tipo técnico de estado: `{'closed': 11915, 'pending auto': 23, 'open': 19, 'pending reminder': 3}` (via `ticket_state.type_id` → `ticket_state_type`).

Tickets abertos segundo tipo OTRS: **45**; status da inferência: **forte evidência pelo tipo de estado OTRS**. Critério observado: `ticket_state.type_id com tipo diferente de closed, merged e removed`. pending auto/reminder permanece no conjunto aberto técnico; validar regra operacional de negócio antes de congelar.

## Tickets abertos e ConfigItems

- Com pelo menos um ConfigItem: 44
- Sem ConfigItem: 1
- Tickets abertos com Link (classe CMDB local): 44; sem Link: 1.

## Prioridades, tipos, serviços e SLA

- Prioridades: `ticket_priority`; preenchidos: 11960/11960; distribuição: {'Medio': 10934, 'Alto': 860, 'Critico': 123, 'Muito baixo': 4, 'Baixo': 39}
- Tipos: `ticket_type`; preenchidos: 11960/11960; distribuição: {'Solicitação': 425, 'Restauração': 11517, 'Mudança': 18}
- Serviços: `service`; preenchidos: 11942/11960; distribuição: {'Telefonia': 102, 'Internet': 10701, 'Links não geográfico': 14, 'Wirelles/Hotspot': 31, 'Firewall': 11, 'Links Operadora': 87, 'Suporte Remoto': 6, 'GMUD': 24, 'Core': 46, 'Requisição de material': 1, 'Ponto de rede': 12, 'Projeto especial': 4, 'Relatórios': 27, 'PABX': 5, 'Perfil de usuário': 5, 'Servidor': 4, 'Radiocomunicação falha parcial do sistema': 2, 'Requisição de serviço': 8, 'Radiocomunicação terminal': 1, 'Infraestrutura': 12, 'Microinformatica': 42, 'Link Operadora': 765, 'Monitoramento': 13, 'Wifi (Internet)': 19, 'None': 18}
- SLA: `sla`; preenchidos: 11859/11960; distribuição: {'10 dias 24x7': 92, '24 Horas 24x7': 1940, '4 Horas 24x7': 154, '72 Horas 24x7': 10, 'Sem SLA': 65, '120 Horas 8x5': 1, '48 Horas 24x7': 5, 'None': 101, '3 Horas 24x7': 723, '24 Horas 8x5': 7536, '240 Horas 8x5': 8, '72 Horas 8x5': 20, '45 dias corridos 24x7': 1, '3 Horas 8x5': 603, '60 dias 8x5': 93, '360 Horas 24x7': 54, '360 Horas 8x5': 43, '12 Horas 8x5': 8, '06 Horas 24x7': 23, '06 Horas 8x5': 11, '8 Horas 8x5': 10, '8 Horas corridas (24x7)': 456, '60 dias 24x7': 3}

As distribuições refletem somente os tickets atualmente associados à fila. Nenhum cálculo de SLA ou ranking foi implementado.

## Histórico e articles

- Histórico: `ticket_history`, registros relacionados à amostra de tickets da fila: `1387926`.
- Tipos de eventos de histórico: `{'NewTicket': 13327, 'FollowUp': 2978, 'SendAutoReject': 634, 'SendAutoReply': 10139, 'Forward': 10, 'SendAnswer': 1515, 'SendAgentNotification': 97663, 'SendCustomerNotification': 35903, 'EmailAgent': 453, 'EmailCustomer': 9118, 'PhoneCallCustomer': 118, 'AddNote': 96980, 'Move': 25310, 'Lock': 90033, 'Unlock': 90029, 'CustomerUpdate': 12454, 'PriorityUpdate': 5, 'OwnerUpdate': 38503, 'LoopProtection': 40, 'Misc': 392037, 'SetPendingTime': 28515, 'StateUpdate': 38777, 'TicketDynamicFieldUpdate': 282586, 'WebRequestCustomer': 573, 'TicketLinkAdd': 454, 'Merged': 396, 'ResponsibleUpdate': 20477, 'TypeUpdate': 21606, 'ServiceUpdate': 22526, 'SLAUpdate': 22450, 'EscalationResponseTimeStart': 1770, 'EscalationSolutionTimeStart': 10751, 'EscalationSolutionTimeNotifyBefore': 3372, 'TitleUpdate': 14994, 'PhoneCallAgent': 1120, 'EscalationSolutionTimeStop': 77, 'EscalationResponseTimeNotifyBefore': 171, 'Bounce': 7, 'TicketLinkDelete': 2, 'EscalationResponseTimeStop': 53}`.
- Tickets com passagem registrada em queue history: `12716`; método/status: tickets com evento histórico em queue_id; inclui tickets atuais.
- Articles: `article`, registros relacionados: `144728`.
- Metadados de article (tipo/canal e contagens): `[{'communication_channel_id': 1, 'article_count': 3476, 'article_sender_type': 'agent'}, {'communication_channel_id': 1, 'article_count': 46683, 'article_sender_type': 'system'}, {'communication_channel_id': 1, 'article_count': 12062, 'article_sender_type': 'customer'}, {'communication_channel_id': 2, 'article_count': 1120, 'article_sender_type': 'agent'}, {'communication_channel_id': 2, 'article_count': 118, 'article_sender_type': 'customer'}, {'communication_channel_id': 3, 'article_count': 77495, 'article_sender_type': 'agent'}, {'communication_channel_id': 3, 'article_count': 2822, 'article_sender_type': 'system'}, {'communication_channel_id': 3, 'article_count': 952, 'article_sender_type': 'customer'}]`.
- Somente IDs e contagens foram consultados; corpos e anexos não foram selecionados.

## Dynamic fields

- Tabela de definição: `dynamic_field`
- Definições encontradas: 221; candidatos relevantes: 23.
- Nomes candidatos (sem valores): `AberturaChamadoISP`, `CidadeSectConfigItemRestauracaoSolicitacao`, `CircuitoDB`, `CircuitoSectConfigItemRestauracaoSolicitacao`, `CustomerFeedbackFAQ`, `DataAberturaISP`, `DesignacaoDB`, `DesignacaooSelectCongItemRestauracao`, `DesignacaoSectConfigItemRestauracaoSolicitacao`, `EquipamentoSelectConfigItemTXT`, `IDdoClienteSelectConfigItemProcessoRestauracaoSolicitacao`, `ISP`, `ISPSelectConfigItemProcessoRestauracaoSolicitacao`, `ItemOperacionalSimNao`, `LinkDashboardBI`, `LinksDeInternetOfflineFormPublicoMetro`, `LinkSimOuNao`, `MACAddressSelectConfigItemTXT`, `ObsLinksDeInternetOfflineFormPublicoMetro`, `ReincidenciaDesignacao`, `SGFIDCliente`, `TicketClienteDB`, `ticketResolvido`
- Tabela de valores: `dynamic_field_value`

Os nomes não comprovam semântica de projeto/cliente; fonte oficial de Projeto: **não confirmada**.

## Ticket ↔ ConfigItem — conclusão

### Qual tabela armazena a relação?

`link_relation`.

### Quais tabelas auxiliares participam?

Tabela de objetos: `link_object`; objetos identificados: `['Appointment', 'FAQ', 'ITSMConfigItem', 'Service', 'Ticket']`.

Mapeamento observado: `link_relation.source_object_id=4` (`ITSMConfigItem`) e `source_key` → `configitem.id`; `target_object_id=1` (`Ticket`) e `target_key` → `ticket.id`. A leitura inversa parte de `target_object_id/target_key` e retorna ao ConfigItem.

### Ticket é identificado por qual campo?

`ticket.id`.

### ConfigItem é identificado por qual campo?

Chave da relação: `source_key`; identificador CMDB validado na amostra: `ConfigItemID`.

### Qual é a direção?

`{'ConfigItem→Ticket': 7495}` (interpretada pelos object IDs e object names observados).

### Cardinalidade?

- Relações observadas: 7495, - Tickets com CI: 7491, - Sem CI: 4469, - Exatamente 1 CI: 7487, - Múltiplos CIs: 4, - ConfigItems ligados a mais de um ticket: 1444. Observações de associação:

- `ticket.id=96844` / `ticket.tn=2026081436000381` → `source_key=10164` → `ConfigItemID=10164`, `Number=36131002993`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=97698` / `ticket.tn=2026082836000416` → `source_key=10151` → `ConfigItemID=10151`, `Number=36131002984`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=97747` / `ticket.tn=2026083136000071` → `source_key=10295` → `ConfigItemID=10295`, `Number=36131003059`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=97512` / `ticket.tn=2026082636000241` → `source_key=10347` → `ConfigItemID=10347`, `Number=36131003093`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=97862` / `ticket.tn=2026090136000214` → `source_key=10347` → `ConfigItemID=10347`, `Number=36131003093`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98049` / `ticket.tn=2026090336000131` → `source_key=10350` → `ConfigItemID=10350`, `Number=36131003095`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=97947` / `ticket.tn=2026090236000114` → `source_key=10372` → `ConfigItemID=10372`, `Number=36131003109`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98062` / `ticket.tn=2026090336000265` → `source_key=10372` → `ConfigItemID=10372`, `Number=36131003109`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98831` / `ticket.tn=2026091536000395` → `source_key=10149` → `ConfigItemID=10149`, `Number=36131002982`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98741` / `ticket.tn=2026091436000379` → `source_key=10159` → `ConfigItemID=10159`, `Number=36131002989`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98723` / `ticket.tn=2026091436000191` → `source_key=10193` → `ConfigItemID=10193`, `Number=36131003009`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98868` / `ticket.tn=2026091636000071` → `source_key=10199` → `ConfigItemID=10199`, `Number=36131003012`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=99067` / `ticket.tn=2026091836000657` → `source_key=10206` → `ConfigItemID=10206`, `Number=36131003015`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98182` / `ticket.tn=2026090436000594` → `source_key=10213` → `ConfigItemID=10213`, `Number=36131003017`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=99023` / `ticket.tn=2026091836000219` → `source_key=10221` → `ConfigItemID=10221`, `Number=36131003022`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98130` / `ticket.tn=2026090436000076` → `source_key=10240` → `ConfigItemID=10240`, `Number=36131003029`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98497` / `ticket.tn=2026091036000341` → `source_key=10287` → `ConfigItemID=10287`, `Number=36131003054`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98739` / `ticket.tn=2026091436000351` → `source_key=10287` → `ConfigItemID=10287`, `Number=36131003054`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98737` / `ticket.tn=2026091436000333` → `source_key=10295` → `ConfigItemID=10295`, `Number=36131003059`, `Class=Links de internet` (ConfigItem→Ticket)
- `ticket.id=98306` / `ticket.tn=2026090836000167` → `source_key=10315` → `ConfigItemID=10315`, `Number=36131003073`, `Class=Links de internet` (ConfigItem→Ticket)

### Existem tipos de relação e estados?

- Tipos observados (id): `{'3': 7495}`; nomes: `{'DependsOn': 7495}`; validade configurada: `{'DependsOn': 1}`.
- Estados observados (id): `{'1': 7495}`; nomes: `{'Valid': 7495}`; validade configurada: `{'Valid': 1}`.
- Relações descartadas por tipo/estado inativo: tipo=0; estado=0.

### Cobertura na NOC - ATACADO?

- IDs únicos associados: 2009
- Cobertura de ConfigItems via `configitem.class_id` e General Catalog: 2009 resolvidos; 0 não resolvidos; classes `{'Links de internet': 2009}`.
- Links únicos envolvidos: 2009.
- Cross-check CMDB limitado a no máximo 50 IDs: resolvidos 50, Links de internet 50, outras classes {}, não resolvidos 0.
- Correspondência da amostra com `ConfigItemID`: 50/50; com `Number`: 0/50.
- Estes resultados são de amostra e não são extrapolados para o universo completo.

## Operadoras relacionadas

Na amostra CMDB de Links, 48 IDs ISP distintos foram observados e 48 resolveram via `Operadoras.ConfigItemID`. Classes retornadas: `{'Operadoras': 48}`. Não há extrapolação para todos os Links associados.

## Validação com CMDB e cadeia até Operadora

ConfigItemGet foi usado apenas para uma amostra limitada. A chave escolhida (`ConfigItemID`) foi comparada literalmente com `ConfigItemID` e `Number`. A cadeia amostral Ticket → ConfigItem → Link → ISP → Operadora: **amostra confirmada por ConfigItemID**; amostra de links com ISP=48, operadoras resolvidas=48, não resolvidas=0. Cobertura integral não calculada.

## Histórico atual versus passagem histórica

Fila atual foi medida por `ticket.queue_id`: 11960 tickets. Passagem histórica usa `ticket_history.queue_id`: 12716 tickets distintos com evento; esse conjunto pode incluir tickets atuais.

## Reincidência e datas

`ticket.create_time` existe e possui índice; `link_relation` fornece o vínculo CI reproduzível. Consultas futuras por janela temporal e contagem por Link são tecnicamente viáveis. O campo `close_time` não existe na tabela principal `ticket`; avaliar o histórico de estados antes de definir tempo de resolução. Datas e campos de escalonamento foram inventariados, sem cálculo nesta fase.

## SLA — viabilidade técnica

`ticket.sla_id` referencia `sla.id`; 11859/11960 tickets da fila têm SLA associado. O schema inclui tempos de primeira resposta, atualização e solução em `sla` e campos de escalonamento em `ticket`. Cálculo dentro/fora do SLA depende de calendário e regra de estado e não foi implementado.

## Projeto / Cliente

Fonte oficial de Projeto: **NÃO ENCONTRADA** nesta inspeção. Campos `customer_id`, `customer_user_id`, serviços e dynamic fields são candidatos de análise, sem equivalência assumida.

## Índices e performance

O estudo usou metadata e leituras filtradas pela fila/chaves de ticket; metadados gerais usaram estimativas de linha, sem `COUNT(*)` global. Duração: 10.91 s. Índices críticos da relação: [{'name': 'FK_link_relation_create_by_id', 'unique': False, 'sequence': 1, 'column': 'create_by', 'type': 'BTREE'}, {'name': 'FK_link_relation_state_id_id', 'unique': False, 'sequence': 1, 'column': 'state_id', 'type': 'BTREE'}, {'name': 'FK_link_relation_type_id_id', 'unique': False, 'sequence': 1, 'column': 'type_id', 'type': 'BTREE'}, {'name': 'link_relation_list_source', 'unique': False, 'sequence': 1, 'column': 'source_object_id', 'type': 'BTREE'}, {'name': 'link_relation_list_source', 'unique': False, 'sequence': 2, 'column': 'source_key', 'type': 'BTREE'}, {'name': 'link_relation_list_source', 'unique': False, 'sequence': 3, 'column': 'state_id', 'type': 'BTREE'}, {'name': 'link_relation_list_target', 'unique': False, 'sequence': 1, 'column': 'target_object_id', 'type': 'BTREE'}, {'name': 'link_relation_list_target', 'unique': False, 'sequence': 2, 'column': 'target_key', 'type': 'BTREE'}, {'name': 'link_relation_list_target', 'unique': False, 'sequence': 3, 'column': 'state_id', 'type': 'BTREE'}, {'name': 'link_relation_view', 'unique': True, 'sequence': 1, 'column': 'source_object_id', 'type': 'BTREE'}, {'name': 'link_relation_view', 'unique': True, 'sequence': 2, 'column': 'source_key', 'type': 'BTREE'}, {'name': 'link_relation_view', 'unique': True, 'sequence': 3, 'column': 'target_object_id', 'type': 'BTREE'}, {'name': 'link_relation_view', 'unique': True, 'sequence': 4, 'column': 'target_key', 'type': 'BTREE'}, {'name': 'link_relation_view', 'unique': True, 'sequence': 5, 'column': 'type_id', 'type': 'BTREE'}]. FKs de `ticket`: `[{'constraint': 'FK_ticket_change_by_id', 'column': 'change_by', 'referenced_schema': 'otrs', 'referenced_table': 'users', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_create_by_id', 'column': 'create_by', 'referenced_schema': 'otrs', 'referenced_table': 'users', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_queue_id_id', 'column': 'queue_id', 'referenced_schema': 'otrs', 'referenced_table': 'queue', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_responsible_user_id_id', 'column': 'responsible_user_id', 'referenced_schema': 'otrs', 'referenced_table': 'users', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_service_id_id', 'column': 'service_id', 'referenced_schema': 'otrs', 'referenced_table': 'service', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_sla_id_id', 'column': 'sla_id', 'referenced_schema': 'otrs', 'referenced_table': 'sla', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_ticket_lock_id_id', 'column': 'ticket_lock_id', 'referenced_schema': 'otrs', 'referenced_table': 'ticket_lock_type', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_ticket_priority_id_id', 'column': 'ticket_priority_id', 'referenced_schema': 'otrs', 'referenced_table': 'ticket_priority', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_ticket_state_id_id', 'column': 'ticket_state_id', 'referenced_schema': 'otrs', 'referenced_table': 'ticket_state', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_type_id_id', 'column': 'type_id', 'referenced_schema': 'otrs', 'referenced_table': 'ticket_type', 'referenced_column': 'id'}, {'constraint': 'FK_ticket_user_id_id', 'column': 'user_id', 'referenced_schema': 'otrs', 'referenced_table': 'users', 'referenced_column': 'id'}]`; FKs de `link_relation`: `[{'constraint': 'FK_link_relation_create_by_id', 'column': 'create_by', 'referenced_schema': 'otrs', 'referenced_table': 'users', 'referenced_column': 'id'}, {'constraint': 'FK_link_relation_source_object_id_id', 'column': 'source_object_id', 'referenced_schema': 'otrs', 'referenced_table': 'link_object', 'referenced_column': 'id'}, {'constraint': 'FK_link_relation_state_id_id', 'column': 'state_id', 'referenced_schema': 'otrs', 'referenced_table': 'link_state', 'referenced_column': 'id'}, {'constraint': 'FK_link_relation_target_object_id_id', 'column': 'target_object_id', 'referenced_schema': 'otrs', 'referenced_table': 'link_object', 'referenced_column': 'id'}, {'constraint': 'FK_link_relation_type_id_id', 'column': 'type_id', 'referenced_schema': 'otrs', 'referenced_table': 'link_type', 'referenced_column': 'id'}]`. A semântica da relação também foi conferida pelos IDs/nome em `link_object`.

## Mapeamento recomendado para fase futura

| Origem OTRS | Campo observado | Entidade Arion futura | Situação |
|---|---|---|---|
| `ticket` | `id` | Ticket.external_id | Confirmado como chave local OTRS, sem modelagem nesta fase |
| `ticket` | `tn` | Ticket.number | Confirmar semântica |
| `ticket` | `title` | Ticket.title | Campo não consultado nem persistido |
| `queue` | `id/name` | Ticket.queue | Nome exato confirmado: True |
| `ticket` | `ticket_state_id` → `ticket_state.type_id` | Ticket.status | Confirmado estruturalmente; validar regra operacional |
| `link_relation` | chaves source/target | TicketLink | confirmada |

## Pendências e próxima fase

Confirmar semanticamente tipos e validade dos vínculos; ampliar cross-check CMDB somente após revisar este relatório; validar regra operacional de ticket aberto e fonte de projeto. **Não foram criados modelos, migrations, sincronização ou métricas operacionais reais.**

## Confirmações de segurança

- Execução solicitada em modo somente leitura, com `SET SESSION TRANSACTION READ ONLY` no client existente.
- Nenhum comando de escrita foi aceito pelo executor.
- Senhas, hashes, tokens, sessões, `users.pw`, corpos de article e anexos não foram incluídos no relatório.
- Base Arion e dados OTRS não foram alterados.
