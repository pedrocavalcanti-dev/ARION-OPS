# Espelho local OTRS

## Modelos que materializam a fonte

| Modelo local | Identidade e vínculo | Campos e finalidade |
|---|---|---|
| OTRSTicket | external_id único para ticket.id; ticket_number único para ticket.tn | Snapshot atual de fila, estado bruto, categoria operacional, prioridade, tipo, serviço, SLA, escalonamento, datas, fechamento, presença e timestamps de sync |
| OTRSStateMapping | external_state_id único | Nome/tipo bruto, categoria de negócio, se mapeado e quando visto |
| OTRSTicketConfigItem | Unique(ticket, config_item_id, relation_type) | Relação OTRS, estado/tipo, data e presença; FK opcional para CMDBLinkMirror |
| IntegrationRun | FK para Integration | Uma tentativa de teste, estudo ou sync, estado e contagens |
| IntegrationSyncState | chave única, inclui otrs | Cursor, última FULL, duração, erro e contagem do mirror |
| IntegrationLease | chave única, inclui otrs e worker | Dono opaco, heartbeat e expiração da exclusão mútua |

O estado OTRS é um snapshot, não uma tabela de eventos. source_last_seen_at e source_synced_at são carimbos locais. A relação com CMDB usa config_item_id e identidade local do ConfigItem; se o Link não for encontrado, cmdb_link fica nulo. Se um Link CMDB for removido, SET_NULL preserva a linha OTRS.

## Colunas principais de OTRSTicket

- Identidade: external_id, ticket_number, title.
- Contexto atual: queue_external_id, queue_name, IDs, nomes e tipo do estado.
- Categoria: business_status, is_business_open.
- Dimensões: prioridade, tipo, serviço e SLA com IDs e nomes.
- Responsabilidade: responsible_user_id, customer_id.
- Tempos da fonte: timeout, until_time e valores escalation.
- Datas: source_created_at, source_changed_at, resolved_at, terminal_state_at, canceled_at.
- Qualidade: closing_code_raw, responsibility_raw, responsibility_normalized.
- Mirror: source_last_seen_at, source_synced_at, is_present_in_queue.

Índices/constraints locais observados no model: external_id e ticket_number únicos; índices por fila/presença, estado, prioridade, serviço, SLA, datas e códigos de fechamento. A lista declarada está em integracoes/models.py, classe OTRSTicket.

## Criação, atualização e ausência

FULL reconcilia o conjunto completo das filas e marca is_present_in_queue=False no ticket que não aparece mais nele. FAST só atualiza tickets retornados pela janela de change_time ou IDs previamente conhecidos; não infere ausência por não ter recebido um delta. Relações seguem regra semelhante com is_present_in_source.

Upserts são feitos em lote e os dados externos passam por normalização antes da escrita. Uma violação de unicidade ou TN ligado a outro ID interrompe a reconciliação em vez de escolher um registro arbitrariamente. Relações duplicadas são canonicalizadas e preservam deterministicamente a primeira ocorrência do resultado.

Não há exclusão física de ticket ausente no sync normal. A presença é usada pelos consumers para separar snapshot atual de registros históricos remanescentes.

## Valores derivados e processos laterais

business_status, is_business_open, campos de responsabilidade normalizada e timestamps de sync são calculados/localmente geridos. Fechamento raw vem do Dynamic Field CodigoFechamento; resolved_at vem de ticketResolvido; terminal_state_at/canceled_at vêm de agregação do histórico. O corpo dos artigos não entra neste model.

QualityTreatment guarda a identidade de um ticket de TQ em campos como quality_ticket_external_id, número e snapshots de fila/estado. QualityCorrelationCheckpoint mantém o cursor de artigos por fila. Nenhum deles é uma cópia da tabela OTRS.

## Caminho até a interface

OTRS ticket.id → SELECT FULL/FAST → normalização → OTRSTicket.external_id; ticket.tn → ticket_number; link_relation com ConfigItem → OTRSTicketConfigItem → match de ConfigItem local → serviços do Cockpit, Chamados, Mapa, Rankings, Qualidade e Relatórios.

Leia a [matriz de rastreabilidade](otrs-rastreabilidade.md), [datas](otrs-datas.md) e [consumidores](otrs-consumidores.md).
