# Rastreabilidade — de onde vem esta informação?

Esta matriz liga os dados de origem ao espelho, à transformação e aos módulos que os exibem. “Interface” indica consumidores identificados no código; filtros ou permissões podem alterar o universo retornado.

| Informação | Origem OTRS | Transformação | Destino local | Interface/consumidor |
|---|---|---|---|---|
| Identidade interna do ticket | ticket.id | Conversão para ID local externo; unicidade validada antes da reconciliação | OTRSTicket.external_id | Chamados, Cockpit e serviços analíticos |
| Número do ticket | ticket.tn | Preservado como string; unicidade local validada | OTRSTicket.ticket_number | Chamados, Cockpit, relatórios |
| Fila | queue.id e queue.name | Resolução por nome exato das filas configuradas | OTRSTicket.queue_external_id e queue_name | Escopo de sincronização e filtros operacionais |
| Estado atual | ticket.ticket_state_id; ticket_state.id, name e type_id | Nome normalizado para business_status; estado bruto permanece disponível | OTRSTicket.external_state_* e business_status | Chamados, Cockpit, Qualidade e rankings |
| Prioridade e tipo | ticket.ticket_priority_id / ticket.ticket_type_id e tabelas de dimensão | Nome e ID associados ao ticket | Campos de prioridade e tipo do OTRSTicket | Detalhes e filtros de Chamados |
| Criação | ticket.create_time | Conversão de timezone e normalização do valor temporal | OTRSTicket.source_created_at | Períodos, tendências e relatórios |
| Última alteração | ticket.change_time | Conversão temporal; janela de segurança alimenta o cursor FAST | OTRSTicket.source_changed_at e IntegrationSyncState | Incremental FAST e freshness |
| Data de resolução | Dynamic Field ticketResolvido, coluna value_date | Leitura do valor de data sem inferir resolução por estado | OTRSTicket.resolved_at | Indicadores e relatórios que usam data de resolução |
| Data de estado terminal | ticket_history.create_time com ticket_history_type StateUpdate | Maior evento compatível com o estado terminal atual | OTRSTicket.terminal_state_at | Linha do tempo e análises temporais |
| Motivo de fechamento | Dynamic Field CodigoFechamento | Preservação do valor bruto; tradução local quando disponível | OTRSTicket.closing_code_raw e campo derivado local | Chamados, Qualidade e análises de motivo |
| Relação ticket-CI | link_relation, link_object, link_type, link_state | Resolve nomes de objetos/tipo/estado, normaliza orientação e deduplica relação | OTRSTicketConfigItem | Chamados, Mapa, Rankings e Qualidade |
| CI associado a link local | configitem.id e referência externa | Procura source_config_item_id no espelho CMDB; relação não resolvida continua sem link local | OTRSTicketConfigItem.config_item_id, quando localizado | Recortes que dependem do link/CMDB |
| Data de resolução usada em um recorte | OTRSTicket.resolved_at ou critério da consulta consumidora | O serviço determina campo, janela e status separadamente | Agregação calculada, não um novo fato OTRS | Cockpit, rankings, relatórios |
| Reincidência por CI | external_id de tickets associados ao CI e source_created_at | Agrupa tickets distintos em episódios com janela de 30 dias e estabilização | Resultado calculado pelo serviço de recorrência | Qualidade, Cockpit e rankings |
| Correlação de TQ | article_data_mime.a_body com QID completo; ticket.tn e fila | Corpo transitório, extração exata do QID e validação contra fila e unicidade de vínculo | QualityTreatment.quality_ticket_external_id / number e snapshots | Qualidade |

## Referências de implementação

- Projeções, filtros e mapeamento do mirror: aplicativos/integracoes/services/otrs_sync.py.
- Identidade e campos locais: OTRSTicket e OTRSTicketConfigItem em aplicativos/integracoes/models.py.
- Consultas de Qualidade: OTRSReadOnlyClient em aplicativos/integracoes/clients/otrs.py.
- Agregações e recortes de tickets: TicketQueryService e serviços dos módulos consumidores.
- Reincidência por CI: aplicativos/qualidade/services/recurrence.py.
- Relação de TQ: aplicativos/qualidade/services/tq_correlation.py e tq_otrs_correlation.py.

Os tipos SQL documentados vêm de um snapshot local identificado pela data 23/10/2026, exceto article_data_mime.a_body, cujo tipo não aparece no estudo sanitizado. O ambiente externo atual não foi consultado. Campos mostrados nas telas podem combinar OTRS com CMDB ou valores derivados; consulte [consumidores](otrs-consumidores.md) e [indicadores](otrs-indicadores.md).
