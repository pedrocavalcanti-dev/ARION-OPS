# 11 · CMDB + OTRS

## Responsabilidades diferentes

OTRS é origem de tickets e relações de ticket com ConfigItem. CMDB ServiceUp é origem dos ConfigItems e seus atributos. ARION sincroniza os dois conjuntos separadamente e resolve uma relação local quando os identificadores coincidem. Uma fonte não substitui a outra.

§§§mermaid
flowchart LR
O[OTRS Ticket e relação CI] --> R[OTRSTicketConfigItem]
C[ServiceUp ConfigItem] --> L[CMDBLinkMirror]
R -->|ConfigItemID| L
L --> P[Provider e atributos do Link]
§§§

## Chave de correlação

OTRS SyncService extrai ConfigItemID da relação externa e localiza CMDBLinkMirror por source_config_item_id. OTRSTicketConfigItem retém config_item_id mesmo se a FK cmdb_link for nula. O vínculo não usa Number, designação do circuito, nome ou Business ID. Ticket pode ter várias relações CI e um ConfigItem pode estar em várias relações/tickets.

## Semântica do vínculo

A relação ticket–ConfigItem é uma entidade própria e guarda relation_type, relation_state e timestamps. Há unicidade local por ticket, ConfigItemID e tipo. A deduplicação de entrada preserva a primeira ocorrência da mesma chave lógica. Campos is_present_in_source da relação e do Link são usados para excluir dados que não aparecem mais na última reconciliação válida.

## Como um ticket recebe ISP ou circuito

O ticket recebe suas relações do OTRS. Quando a relação resolve ao Link local, consumidores podem ler operator, isp_raw, designation, circuit, business_project e localização a partir do mirror. Não existe enriquecimento remoto por request do ticket à ServiceUp. Se a FK não resolveu, a relação ainda informa ConfigItemID, mas atributos de Link/Provider podem não estar disponíveis.

## Métricas não intercambiáveis

| Contagem | O que conta |
|---|---|
| ConfigItems/Links | Linhas de CMDBLinkMirror presentes no snapshot. |
| Tickets | Linhas OTRSTicket dentro do universo e filtros do consumidor. |
| Relações Ticket–CI | Linhas OTRSTicketConfigItem presentes e deduplicadas por chave lógica. |
| Links afetados | ConfigItem/Link distintos relacionados a tickets que atendem à regra. |

O serviço analytics usa Count distinct para evitar multiplicar Link por várias relações. Confira também a [documentação OTRS](otrs-relacionamentos.md), [indicadores](cmdb-indicadores.md) e [rastreabilidade](cmdb-rastreabilidade.md).
