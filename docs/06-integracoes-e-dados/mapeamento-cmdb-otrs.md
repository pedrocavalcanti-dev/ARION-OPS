# Mapeamento de negocio entre CMDB e OTRS

Data da medicao: 2026-10-23. Os nomes de projeto legados sao evidencias historicas, nao fonte operacional.

## Matriz de confianca

| Conceito Arion | Fonte observada | Campo / relacao | Confianca e limite |
|---|---|---|---|
| Projeto / cliente | Nenhuma fonte oficial confirmada | `CMDBLinkMirror.legacy_project` veio do antigo XLSX; `CustomerID` do Link existe em apenas 12/3.435 links. No OTRS, `TicketClienteDB` foi preenchido em 1 ticket NOC; `IDdoClienteSelectConfigItemProcessoRestauracaoSolicitacao` teve 0. | **NAO ENCONTRADO** como mapeamento oficial. Ha 8 nomes distintos no campo legado, em 2.966 links; 469 links sem valor. Nao promove-los a Projeto oficial. |
| Operadora contratada | Nenhuma relacao confirmada no espelho | `DesignacaoOperadora` preenchido em 623/3.435, texto livre; `NumeroContrato` em 808/3.435; `CnpjOperadora` vazio nos 3.435 links. OTRS `service`/`sla` nao prova operadora contratada. | **NAO ENCONTRADO**. Nenhum valor foi inferido de nome do Link, Provedor, contrato ou SLA. |
| Provedor / ISP do Link | CMDB ServiceUp, classe atualmente chamada `Operadoras` | `CMDBLinkMirror.isp_raw` corresponde a `CMDBOperatorMirror.source_config_item_id`; FK `operator` aponta para o cadastro resolvido, cujo `name` e o rotulo humano. | **FORTE EVIDENCIA** para semantica de Provedor/ISP. 3.316 valores resolvidos por ConfigItemID e 17 por nome exato; o catalogo tem 1.301 nomes distintos ligados a Links. A classe/fk nao foi renomeada nesta etapa. |
| Link | CMDB ServiceUp, classe `Links de internet` | `ConfigItemID` identifica o Link; `Number` e o identificador de negocio. OTRS `link_relation.source_key` (`ITSMConfigItem`) resolve ao ConfigItem e `target_key` (`Ticket`) ao Ticket. | **CONFIRMADO** no estudo e no espelho. A relacao local usa `OTRSTicketConfigItem.config_item_id` e FK opcional para o Link. |
| Ticket operacional | OTRS MySQL, fila pelo nome exato `NOC - ATACADO` | `ticket.queue_id` resolvido por `queue.name`; data de criacao `ticket.create_time` -> `source_created_at`; mirror atual usa `queue_name` e `is_present_in_queue`. | **CONFIRMADO**. Consultas operacionais usam a fila pelo nome; o ID observado (107) e apenas evidencial e nao codificado como resolucao. |

## Medicao dos mirrors locais

- Links presentes: 3.435.
- Catalogo CMDB chamado `Operadoras` presente: 1.329; nomes distintos ligados a Links: 1.301.
- ISP/ConfigItemID bruto preenchido em 3.432 links. Resolvidos pela FK: 3.333 (3.316 por ID e 17 por nome exato); 99 nao resolvidos; 3 vazios.
- Projeto oficial encontrado: nenhum. Valor legado preenchido em 2.966 links; ausente em 469; 8 valores legados distintos.
- Operadora contratada confirmada: nenhuma; portanto nenhum Link pode ser contabilizado como contratado por VIVO/CLARO/ALGAR etc. a partir deste espelho.
- Tickets no snapshot local, fila NOC: 11.963 atuais/presentes; OPEN 18, PENDING 3, RESOLVED 21, CLOSED 10.936, CANCELED 985. O backlog atual OPEN+PENDING e 21; RESOLVED fica fora do backlog.
- Relacoes atuais Ticket-ConfigItem da fila: 7.497.

## Conferencia limitada dos Dynamic Fields OTRS

Consulta somente leitura por `OTRSReadOnlyClient`, agregada por nome de campo e limitada a `queue.name = NOC - ATACADO`. Nenhum valor pessoal foi selecionado ou registrado.

| Dynamic Field | Tipo | Tickets NOC com texto preenchido | Valores distintos (prefixo de ate 200 caracteres) |
|---|---|---:|---:|
| `ISP` | Ticket | 7.222 | 1.318 |
| `CircuitoDB` | Ticket | 7.269 | 1.721 |
| `DesignacaoDB` | Ticket | 6.985 | 1.980 |
| `TicketClienteDB` | Ticket | 1 | 1 |
| `ISPSelectConfigItemProcessoRestauracaoSolicitacao` | Ticket | 0 | 0 |
| `DesignacaoSectConfigItemRestauracaoSolicitacao` | Ticket | 0 | 0 |
| `IDdoClienteSelectConfigItemProcessoRestauracaoSolicitacao` | Ticket | 0 | 0 |

Os campos preenchidos de ISP, Circuito e Designacao podem apoiar futuros estudos de correspondencia de ticket/link, mas esta medicao nao estabelece Projeto nem Operadora contratada. O campo de cliente citado tem cobertura quase nula e nao foi interpretado como Projeto.

## Amostras sanitizadas de resolucao de Provedor

`Projeto legado` abaixo e apenas o valor historico congelado. `Operadora contratada` fica sem classificacao. Nomes pessoais foram excluidos; os dois ConfigItemIDs sao tecnicos e apresentados para validacao da relacao.

| Number do Link | Projeto legado | Operadora contratada | Provedor resolvido | Link ConfigItemID | Provedor ConfigItemID |
|---|---|---|---|---:|---:|
| 36131003091 | Bradesco | Nao classificada | GIGA+ | 10344 | 6746 |
| 36131003092 | Bradesco | Nao classificada | GIGA+ | 10345 | 6746 |
| 36131003093 | Bradesco | Nao classificada | BCNET | 10347 | 10346 |
| 36131002997 | Banco do Brasil | Nao classificada | BiteByte | 10169 | 10168 |
| 36131002998 | Banco do Brasil | Nao classificada | DATA CORPORE | 10170 | 7089 |
| 36131003040 | Banco do Brasil | Nao classificada | G&A TELECOM | 10260 | 10259 |
| 36131002993 | Correios | Nao classificada | Network Fibra | 10164 | 10081 |
| 36131002994 | Correios | Nao classificada | Ar Telecom - Nuvyon | 10165 | 9896 |
| 36131002999 | Correios | Nao classificada | NetBR Telecom | 10172 | 10171 |
| 36131003002 | FUST | Nao classificada | Graunet Telecom | 10177 | 10176 |
| 36131003003 | FUST | Nao classificada | SUPER CONNECT | 10179 | 8105 |
| 36131003004 | FUST | Nao classificada | ADRENALINA NET | 10181 | 10180 |

## Regras de apresentacao e consumo

- `provider_name` usa `CMDBOperatorMirror.name`; se nao houver entidade resolvida, apresenta `Nao classificado`. `isp_raw` e `provider_config_item_id` ficam separados como dados tecnicos.
- Nunca usar o `ConfigItemID` numerico como nome de Provedor.
- `project_name` e `contract_operator_name` nao sao sintetizados de campos candidatos nesta fase. `legacy_project` permanece explicitamente legado.
- `TicketQueryService.noc_tickets()` resolve a fila por `queue_name == "NOC - ATACADO"` e `is_present_in_queue=True`. Datas do periodo usam `source_created_at`; backlog e snapshot corrente OPEN+PENDING, sem filtro de data de criacao.
- A busca textual da Base ignora acentos, caixa e espacos repetidos em texto. Identificadores continuam pesquisados sem remover nem reescrever caracteres.

## Encoding

Os arquivos do projeto contem sequencias reconheciveis de UTF-8 previamente interpretadas como Latin-1/Windows-1252 e alguns textos com `?` gravado no lugar de letras acentuadas. Foi corrigida somente a primeira categoria por decodificacao reversivel e os segundos casos encontrados foram corrigidos pontualmente; nao se substituiu `?` globalmente. Templates declaram UTF-8 e `CMDBClient` decodifica respostas JSON como UTF-8. O smoke test verifica paginas principais e strings conhecidas; dados de origem que ja contenham caracteres de substituicao precisam de nova leitura da fonte para recuperar a letra original, nao de heuristica local.
