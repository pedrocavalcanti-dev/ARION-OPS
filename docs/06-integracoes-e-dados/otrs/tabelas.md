# Catálogo de tabelas OTRS

## Escopo da contagem

O catálogo lista as 21 tabelas de domínio cujo conteúdo é selecionado por sync, estudo ou correlação. `information_schema` é contado separadamente como metadado MySQL, e não como tabela de negócio OTRS. A introspecção pode enumerar outras tabelas, mas isso não significa que o ARION OPS leia seus registros.

| ID | Tabela OTRS | Caminho que lê conteúdo | Colunas e finalidade comprovada |
|---|---|---|---|
| T01 | `ticket` | FULL/FAST, estudo, Qualidade | Identidade, TN, fila, estado, dimensões, escalonamento e datas |
| T02 | `queue` | FULL/FAST, estudo, Qualidade | Resolve o ID pelo nome exato; nome atual do ticket |
| T03 | `ticket_state` | FULL/FAST, estudo, Qualidade | ID, nome e `type_id`; classifica o estado no ARION |
| T04 | `ticket_state_type` | FULL/FAST, estudo | Nome do tipo técnico associado ao estado |
| T05 | `ticket_priority` | FULL/FAST, estudo | ID e nome de prioridade |
| T06 | `ticket_type` | FULL/FAST, estudo | ID e nome de tipo |
| T07 | `service` | FULL/FAST, estudo | ID e nome de serviço |
| T08 | `sla` | FULL/FAST, estudo | ID e nome de SLA; não calcula cumprimento do SLA |
| T09 | `link_relation` | FULL/FAST, estudo | Relação polimórfica entre objetos, tipo, estado e data |
| T10 | `link_object` | FULL/FAST, estudo | Traduz nomes `Ticket` e `ITSMConfigItem` para IDs |
| T11 | `link_type` | FULL/FAST, estudo | Resolve pelo nome `DependsOn`; estudo lê também validade |
| T12 | `link_state` | FULL/FAST, estudo | Resolve pelo nome `Valid`; estudo lê também validade |
| T13 | `dynamic_field` | FULL/FAST, estudo | Identifica por nome e tipo os campos aprovados |
| T14 | `dynamic_field_value` | FULL/FAST | Valores de `CodigoFechamento`, `ticketResolvido` e campo opcional de procedência |
| T15 | `ticket_history` | FULL/FAST, estudo | Último `StateUpdate` para um estado terminal; estudo usa também contagens |
| T16 | `ticket_history_type` | FULL/FAST, estudo | Resolve o tipo de evento `StateUpdate` e agrega tipos no estudo |
| T17 | `article` | Estudo, Qualidade | ID, ticket, horário do artigo e metadados de canal/remetente no estudo |
| T18 | `article_data_mime` | Qualidade | `a_body` transitório para buscar o QID; não é espelhado |
| T19 | `article_sender_type` | Estudo | Traduz IDs de remetente em rótulos para contagem agregada |
| T20 | `configitem` | Estudo | ID e classe usados para classificar o ConfigItem |
| T21 | `general_catalog` | Estudo | Nome da classe por `configitem.class_id` |

As rotinas de leitura de conteúdo identificadas estão em `OTRSSyncService`, `OTRSStudyService` e `OTRSReadOnlyClient`. Os campos exatos, tipos históricos e destino local estão no [dicionário](otrs-dicionario.md). As famílias SQL têm IDs estáveis no [catálogo de consultas](otrs-consultas-sql.md).

## Metadata MySQL fora das tabelas de domínio

O conector consulta `information_schema.SCHEMATA`, `TABLES`, `COLUMNS`, `STATISTICS` e `KEY_COLUMN_USAGE` para descobrir bancos, tabelas, colunas, índices e FKs declaradas. Essas leituras retornam metadados, não conteúdo dos tickets. `get_sample_rows` é uma rotina genérica de estudo separada; a tabela precisa ter sido descoberta, a página é limitada a 50 linhas e nomes contendo `article` ou `customeruser` são recusados.

## Chaves e cautelas

`ticket.id` é a chave usada pelo mirror como ID externo; `ticket.tn` é o número público e é unique no snapshot local de schema. A tabela `link_relation` guarda IDs de objetos e chaves textuais polimórficas: não se deve tratá-la como uma FK SQL direta para `ticket` ou `configitem`. Ver [relacionamentos](otrs-relacionamentos.md).

O nome de uma tabela no catálogo não afirma que todas as suas colunas foram lidas. A lista de campos está limitada aos 80 pares identificados no código e no estudo delimitado.
