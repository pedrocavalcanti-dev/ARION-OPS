# 18 · Rastreabilidade

O nível desta matriz mede evidência no repositório. “Confirmado pelo código” confirma transformação e destino; não significa que a instância externa atual tenha sido consultada nesta tarefa. O contrato vivo da origem continua pendente de validação autorizada.

| ID | Dado de origem | Transformação | Destino local | Indicador/consumidor | Nível |
|---|---|---|---|---|---|
| CMDB-R001 | ConfigItemSearch.ConfigItemID | Deduplicação e validação de completude | source_config_item_id em ambos os mirrors | Chave de Link/Provider e OTRS Ticket–CI | Confirmado pelo código; envelope da fonte pendente |
| CMDB-R002 | ConfigItem.Number | Trim; vazio vira NULL | source_number | Busca, identificação visual e fallback de exibição | Confirmado pelo código |
| CMDB-R003 | ConfigItem.Name | Preservação literal | name em CMDBLinkMirror/CMDBOperatorMirror | Base, circuito e parser de identidade | Confirmado pelo código |
| CMDB-R004 | Link.Name | Parser de segmentos, aliases e token BD/BL | contracting_client*, business_project, business_account, business_id, confidence/source | Filtros, Data Scope, Ranking, Mapa e Qualidade | Transformação confirmada; classificação externa é derivada |
| CMDB-R005 | CIXMLData.ISP | ID de ConfigItem primeiro; fallback de nome exato único | isp_raw + FK operator e resolution_method | Provider do Ranking, Base, Relatório e Qualidade | Confirmado pelo código; valores atuais não validados |
| CMDB-R006 | CIXMLData.Cidade/Estado/Endereco | Trim e limite da coluna; sem geocode | city/state/address | Base e agregações geográficas do Mapa | Campos/destinos confirmados; sem coordenada |
| CMDB-R007 | CIXMLData.Circuito/Designacao/DesignacaoOperadora | Preservados em colunas separadas | circuit/designation/operator_designation | Nome operacional do circuito e detalhe | Confirmado pelo código |
| CMDB-R008 | CIXMLData.Velocidade/RangeIP/Equipamento/MACAddress | Texto seguro, limite da coluna | speed_raw/ip_range/equipment/mac_address | Detalhe da Base | Confirmado pelo código |
| CMDB-R009 | CIXMLData.NumeroContrato/CustomerID/CNPJ | Campos separados, sem inferência cruzada | contract_number/customer_id/cnpj/operator_cnpj | Base, escopo e relatórios aplicáveis | Confirmado pelo código |
| CMDB-R010 | CurDeplState/CurInciState e campos fallback | Cur* preferido; fallback sem Cur* | deployment_state/incident_state e tipos | Indicador de link em operação e exibição | Regra do código confirmada; enumeração externa pendente |
| CMDB-R011 | CreateTime | source_datetime | source_created_at | Idade e contexto de cadastro em consumidores | Conversão confirmada; sem histórico de versões |
| CMDB-R012 | Relação OTRS Ticket–CI.ConfigItemID | Equality join com source_config_item_id | OTRSTicketConfigItem.cmdb_link | Tickets, links afetados, mapa e Qualidade | Confirmado pelo código; sem join por Number |
| CMDB-R013 | Ausência em snapshot Search/Get validado | Marcação de stale, sem delete | is_present_in_source=False | Todos os universos ativos locais | Confirmado pelo código; depende de Search exaustivo |
| CMDB-R014 | ISP não resolvido ou ambíguo | Preserva texto e não escolhe candidato | isp_raw, FK NULL, UNRESOLVED | Labels “não identificado”/investigação | Confirmado pelo código |
| CMDB-R015 | Projeto legado do mirror | Reassociação por ConfigItemID, sem reimportar XLSX | legacy_project/project_source | Fallback em escopo e telas | Confirmado pelo código; origem histórica |

## Fluxo verificável

§§§mermaid
flowchart LR
E[ServiceUp ConfigItem] --> A[API Search e Get]
A --> X[Parser e sanitização]
X --> D[Campos e identidade derivados]
D --> B[CMDBLinkMirror ou CMDBOperatorMirror]
B --> J[Join local por ConfigItemID]
J --> R[Indicadores e interfaces]
§§§

O catálogo CMDB-Q001–Q008 e transformações CMDB-T001–T008 está em [Módulos consumidores](cmdb-consumidores.md). A relação externa OTRS e seu espelho estão em [OTRS + CMDB](cmdb-otrs.md) e [OTRS · Relacionamentos](otrs-relacionamentos.md).
