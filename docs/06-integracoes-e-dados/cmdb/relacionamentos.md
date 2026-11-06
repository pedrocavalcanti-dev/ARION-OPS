# 07 · Relacionamentos

## Chaves e cardinalidades

| Origem | Destino | Cardinalidade local | Chave e regra |
|---|---|---|---|
| Link CMDB | Operadora/Provider | Muitos Links para zero ou uma Operadora | ISP bruto é tratado primeiro como ConfigItemID; fallback por nome case-insensitive somente quando exato e único. |
| Ticket OTRS | ConfigItem CMDB | Uma relação por ticket, ConfigItemID e relation_type | A chave lógica é ticket externo + ConfigItemID + tipo de relação. |
| Relação OTRS–CI | Link mirror | Zero ou uma FK local | Igualdade do ConfigItemID entre OTRSTicketConfigItem.config_item_id e CMDBLinkMirror.source_config_item_id. |

Number não participa do join Ticket–CI. Name pode resolver o Provider somente no fallback específico do ISP; não é chave global de ConfigItem. A constraint local de OTRSTicketConfigItem impede duplicar a mesma tripla ticket/CI/tipo de relação.

§§§mermaid
flowchart TD
T[OTRSTicket] --> R[OTRSTicketConfigItem]
R -->|ConfigItemID igual| L[CMDBLinkMirror]
L -->|ISP resolvido| P[CMDBOperatorMirror]
§§§

## Relação Link–Provider

O sync primeiro prepara as Operadoras do snapshot e constrói mapas por ConfigItemID e nome exato em casefold. Para cada Link, ISP vazio resulta EMPTY; ID igual resolve CONFIG_ITEM_ID; nome único resolve NAME; caso contrário preserva isp_raw, deixa operator nulo e marca UNRESOLVED. Uma FK nullable com SET_NULL evita apagar o Link caso um Provider local seja removido. A próxima sincronização reconcilia os dois conjuntos por ID de origem.

## Relação Ticket–ConfigItem

O sync OTRS persiste as relações remotas de Ticket e ConfigItem separadamente. Quando o ConfigItemID já existe no mirror, a relação recebe cmdb_link; quando ainda não existe, a relação continua no espelho OTRS com FK nula. A reconciliação posterior pode resolver o Link sem alterar a chave da relação. Portanto, total de tickets, total de ConfigItems e total de vínculos Ticket–CI são medidas diferentes.

## Integridade e ausência

Relações locais não são apagadas por falta de resolução. Campos de origem ausentes ficam vazios, relações incompletas permanecem representáveis e is_present_in_source distingue os itens vistos na reconciliação atual. Veja [CMDB + OTRS](cmdb-otrs.md), [Espelho local](cmdb-espelho-local.md) e [Dicionário](cmdb-dicionario.md).
