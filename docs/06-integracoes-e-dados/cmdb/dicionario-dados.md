# 05 · Dicionário de dados

O catálogo abaixo cobre os 65 campos nas constantes OPERATOR_FIELDS (24) e LINK_FIELDS (41) de cmdb_sync.py. Ele inclui dados de origem, valores derivados e campos de relação calculados pelo sync. ConfigItemID é a chave externa persistente; Number é identificador de exibição e não substitui a chave técnica. Tipos e tamanhos locais vêm dos modelos Django.

## CMDBOperatorMirror · 24 campos sincronizados

| Campo local | Tipo local | Entrada / regra |
|---|---|---|
| source_config_item_id | Char(40), único | ConfigItemID; obrigatório para identificar o ConfigItem. |
| source_number | Char(40), único anulável | Number; vazio vira NULL. |
| name | Char(300) | Name; obrigatório no item de Operadoras. |
| category | Char(160) | CIXMLData.CategoriaOperadora. |
| cnpj | Char(80) | CIXMLData.CnpjOperadora. |
| business_hours | Char(300) | CIXMLData.HorarioOperadora. |
| phone | Char(300) | CIXMLData.TelefoneOperadora. |
| whatsapp | Char(300) | CIXMLData.OperadoraWhatsApp. |
| opening_email | Char(300) | CIXMLData.EmailAbertura. |
| followup_email | Char(300) | CIXMLData.EmailAcompanhamento. |
| portal_url | Char(500) | CIXMLData.PortalOperadora; URL de acesso, sem credencial. |
| escalation_1 | Char(500) | CIXMLData.1EscalonamentoOperadora. |
| escalation_2 | Char(500) | CIXMLData.2EscalonamentoOperadora. |
| escalation_3 | Char(500) | CIXMLData.3EscalonamentoOperadora. |
| escalation_4 | Char(500) | CIXMLData.4EscalonamentoOperadora. |
| escalation_5 | Char(500) | CIXMLData.5EscalonamentoOperadora. |
| deployment_state | Char(160) | CurDeplState; fallback DeplState. |
| deployment_state_type | Char(100) | CurDeplStateType; fallback DeplStateType. |
| incident_state | Char(160) | CurInciState; fallback InciState. |
| incident_state_type | Char(100) | CurInciStateType; fallback InciStateType. |
| definition_id | Char(40) | DefinitionID. |
| version_id | Char(40) | VersionID. |
| last_version_id | Char(40) | LastVersionID. |
| source_created_at | DateTime anulável | CreateTime; convertido por source_datetime. |

Cada campo da whitelist é comparado no upsert.

## CMDBLinkMirror · 41 campos sincronizados

| Campo local | Tipo local | Entrada / regra |
|---|---|---|
| source_config_item_id | Char(40), único | ConfigItemID; identidade estável do Link. |
| source_number | Char(40), único anulável | Number; identificador de exibição. |
| name | Char(500) | Name original, preservado antes da classificação. |
| city | Char(200) | CIXMLData.Cidade. |
| state | Char(100) | CIXMLData.Estado; valor literal de origem. |
| address | Char(500) | CIXMLData.Endereco. |
| contracting_client_raw | Char(300), índice | Segmento identificado no Name. |
| contracting_client | Char(300), índice | Nome canônico classificado pelo parser. |
| business_project | Char(300), índice | Projeto classificado a partir de Name; separado do cliente. |
| business_account | Char(300) | Segmento remanescente inequívoco de Name. |
| business_id | Char(80) | Token BD/BL reconhecido em Name. |
| business_identity_confidence | Char(16) | HIGH, MEDIUM, LOW ou estado inicial; classificação, não dado externo. |
| business_identity_source | Char(32) | CMDB_EXPLICIT_CLIENT, CMDB_PROJECT_RULE ou UNKNOWN. |
| circuit | Char(300) | CIXMLData.Circuito. |
| designation | Char(300) | CIXMLData.Designacao. |
| operator_designation | Char(300) | CIXMLData.DesignacaoOperadora. |
| identification | Char(300) | CIXMLData.Identificacao. |
| link_type | Char(160) | CIXMLData.Tipo. |
| speed_raw | Char(160) | CIXMLData.Velocidade; não convertido para número no sync. |
| ip_range | Char(300) | CIXMLData.RangeIP. |
| equipment | Char(300) | CIXMLData.Equipamento. |
| mac_address | Char(160) | CIXMLData.MACAddress. |
| cnpj | Char(100) | CIXMLData.CNPJ. |
| operator_cnpj | Char(100) | CIXMLData.CnpjOperadora. |
| contract_number | Char(200) | CIXMLData.NumeroContrato. |
| business_hours | Char(300) | CIXMLData.HorarioFuncionamento. |
| isp_notes | Text | CIXMLData.ObservacaoISP; limitado a 10.000 caracteres. |
| customer_id | Char(200) | CIXMLData.CustomerID; não é business_account. |
| isp_raw | Char(300) | CIXMLData.ISP, mantido literal para rastreabilidade. |
| operator_id | FK anulável | Resolução local de ISP para CMDBOperatorMirror; armazenamento pelo attname do FK. |
| operator_resolution_method | Char(20), índice | CONFIG_ITEM_ID, NAME, UNRESOLVED ou EMPTY. |
| legacy_project | Char(300) | Valor legado existente no mirror, preservado pelo source_config_item_id. |
| project_source | Char(20) | LEGACY_XLSX somente quando legacy_project tem valor. |
| deployment_state | Char(160), índice | CurDeplState; fallback DeplState. |
| deployment_state_type | Char(100) | CurDeplStateType; fallback DeplStateType. |
| incident_state | Char(160), índice | CurInciState; fallback InciState. |
| incident_state_type | Char(100) | CurInciStateType; fallback InciStateType. |
| definition_id | Char(40) | DefinitionID. |
| version_id | Char(40) | VersionID. |
| last_version_id | Char(40) | LastVersionID. |
| source_created_at | DateTime anulável | CreateTime; conversão centralizada em source_datetime. |

## Metadados não incluídos na contagem 65

Ambos os modelos também têm source_last_seen_at (DateTime indexado), source_synced_at (DateTime) e is_present_in_source (Boolean). CMDBLinkMirror possui ainda legacy_operator_classification, utilizado por classificação histórica, não atualizado pelo sync atual. Class é validada na resposta e não persistida como campo do mirror. Primary key Django também não é uma coluna de origem.

## Atributos de origem ignorados

O sync define whitelist de campos diretos e dinâmicos. Campos fora dela são ignorados e o log registra apenas o nome não mapeado. UsuarioPPOE pode ser observado pelo estudo de schema, mas não tem destino no mirror atual. Nomes de atributo que indiquem senha, credential, secret, token, auth, cookie, sessão ou usuário de portal são protegidos pelo client e não persistidos. Os valores nunca devem ser copiados para logs ou documentação.

Ver [Estruturas de resposta](cmdb-respostas.md), [Espelho local](cmdb-espelho-local.md) e [Segurança](cmdb-seguranca.md).
