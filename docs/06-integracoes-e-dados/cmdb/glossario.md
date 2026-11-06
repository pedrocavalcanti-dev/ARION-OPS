# 19 · Glossário

## Termos CMDB e ARION

| Termo | Significado nesta integração |
|---|---|
| CMDB | Fonte oficial de ConfigItems e atributos técnicos consultados pelo ARION. |
| ServiceUp | Instância/fonte CMDB configurada para acesso por GenericInterface; não inspecionada remotamente nesta tarefa. |
| GenericInterface | Camada OTRS exposta pelo cliente como operações JSON POST sobre HTTPS. |
| ConfigItem / CI | Item de configuração externo identificado tecnicamente por ConfigItemID. |
| ConfigItemID | Identificador técnico usado pelo upsert e pelo join Ticket–CI. |
| Number | Número de exibição/negócio do ConfigItem; não é chave da relação OTRS–CI. |
| Name | Nome externo preservado e usado como entrada do parser de identidade. |
| CIXMLData | Conteúdo de atributos dinâmicos do ConfigItem em estrutura JSON/XML, flatten localmente. |
| Links de internet | Classe CMDB mapeada para CMDBLinkMirror. |
| Operadoras | Classe CMDB mapeada para CMDBOperatorMirror; representa Provider/ISP na interface. |
| Cliente contratante | Campo derivado contracting_client; não é o ISP/Provider nem CustomerID. |
| Projeto | business_project derivado do Name; legacy_project é fallback histórico separado. |
| Conta | business_account derivada apenas quando o segmento remanescente é inequívoco. |
| Business ID | Token BD/BL derivado de Name; não é ID do ConfigItem. |
| ISP raw | Texto literal do atributo ISP antes de resolver Provider. |
| Mirror | Representação local reconstruível dos valores permitidos para leitura operacional. |
| Snapshot FULL | Leitura das duas classes que só reconcilia presença após validação integral. |
| Stale/ausente | Linha local que não apareceu no último snapshot válido; preservada, marcada ausente. |
| Data Scope | Restrição backend de Links por operador contratante e projeto para limitar dados visíveis. |
| OTRSTicketConfigItem | Espelho da relação de ticket com ConfigItem, mantida separada do próprio Link. |
| Reincidência | Métrica local derivada das relações/tickets elegíveis por Link; não atributo da CMDB. |
| Geocodificação | Não implementada no mirror CMDB atual; cidade e UF são strings, sem lat/lon. |

Para as regras de agrupamento veja [Normalização](cmdb-normalizacao.md); para relações e fontes, [Relacionamentos](cmdb-relacionamentos.md) e [CMDB + OTRS](cmdb-otrs.md).
