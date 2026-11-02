# NOC Atacado — campos e semântica observados no OTRS

**Fotografia:** 24/10/2026. **Escopo:** somente a fila `NOC - ATACADO`. **Modo:** leitura somente.

## 1. Universo e metodologia

A fila foi resolvida por igualdade exata do nome e retornou **queue ID 107**. OTRS retornou **11.971 tickets atualmente na fila** (`ticket.queue_id=107`). A tabela `ticket_history` contém **12.727 tickets distintos** com pelo menos um evento registrado com `queue_id=107`; esse valor é histórico de eventos, inclui tickets atuais e não significa 12.727 tickets ativos nem prova, sozinho, uma passagem completa pela fila.

As consultas foram executadas com o cliente `OTRSReadOnlyClient` existente, dentro de sessão MySQL `READ ONLY` e usando apenas `SELECT`. O estudo agregou estados, dimensões, histórico, campos dinâmicos e relações. Nenhum corpo de artigo, anexo, nome/e-mail de usuário, `customer_user`, senha ou valor livre de detalhe foi selecionado. Para Dynamic Fields foram retornadas somente contagens de tickets e valores distintos; para códigos de fechamento foram exibidas apenas opções de um campo `Dropdown` que correspondiam a categorias seguras pesquisadas. Nenhum valor bruto de campo de texto foi incluído.

As relações, Projeto legado, UFs e reincidência abaixo são agregados dos mirrors locais Arion, restritos a tickets presentes na NOC e relações atuais. O total local (11.971) coincide com a contagem consultada na fila fonte.

## 2. Estados atuais e categoria de negocio aplicada

As categorias de negocio sao mapeadas explicitamente pelo nome operacional, sem inferencia pelo tipo tecnico OTRS. Percentuais usam 11.971 tickets.

| Estado | ID | Tipo técnico OTRS | Tickets | % | Categoria de negocio |
|---|---:|---|---:|---:|---|
| `closed successful` | 2 | closed | 10.949 | 91,46% | ENCERRADO |
| `cancelado` | 15 | closed | 986 | 8,24% | CANCELADO |
| `resolvido` | 14 | pending auto | 18 | 0,15% | RESOLVIDO |
| `agendado` | 13 | pending auto | 7 | 0,06% | AGENDADO / SCHEDULED |
| `em atendimento` | 10 | open | 6 | 0,05% | ABERTO |
| `Pendente` | 11 | pending reminder | 4 | 0,03% | PENDENTE |
| `Encerrado com viabilidade` | 31 | pending auto | 1 | 0,01% | ENCERRADO |
| **Total** |  |  | **11.971** | **100%** |  |

O tipo tecnico nao representa sozinho a semantica de negocio: `resolvido` e `Encerrado com viabilidade` sao tecnicamente `pending auto`; `cancelado` e tecnicamente `closed`. O mapeamento explicito preserva `agendado` como categoria propria `SCHEDULED`, distinta de `PENDING`, e inclui ambos no backlog operacional.

### Backlog e cancelados

- Backlog neste snapshot: **em atendimento (6) + Pendente (4) + agendado (7) = 17** tickets, ou 0,14% do snapshot.
- `resolvido` (18), `closed successful` (10.949), `Encerrado com viabilidade` (1) e `cancelado` (986) ficam fora do backlog.
- Foi encontrado **um** estado de cancelamento na definição atual: `cancelado` (ID 15, tipo `closed`). Há 986 tickets nesse estado agora.
- O histórico tem 1.017 eventos `StateUpdate` para `cancelado`, envolvendo **1.010 tickets distintos** da fila atual; 24 desses tickets estão hoje em outro estado. Cancelamento histórico e estado atual são conceitos diferentes.
- Cancelado não deve contar como RESOLVIDO, ENCERRADO, sucesso operacional ou backlog.

## 3. Fechamento e Pendência Financeira

O estado atual informa a situação, mas não explica sozinho o motivo. Foi localizado um Dynamic Field estruturado `CodigoFechamento` (`Dropdown`, objeto Ticket): preenchido em **10.310 tickets (86,1%)**, com **54 valores distintos**. `DetalheCodFechamento` é `Text`, preenchido em **7.432 (62,1%)**, com **4.675 valores distintos**; sua cardinalidade e natureza livre tornam inadequado agrupá-lo ou expô-lo como filtro sem normalização e revisão de privacidade.

Reconsulta do catalogo confirmou que o mesmo `dynamic_field` ID 101 `CodigoFechamento` foi alterado em lugar em agosto/2026; nao foi criado um novo campo de motivo. O mirror tem 54 raws, 48 categorias canonicas depois de seis variantes excedentes agrupadas explicitamente. `FalhaMassiva` e `Defeito Massivo` compoem a mesma categoria confirmada `Falha Massiva`; o raw continua preservado. Inventario mensal completo, labels de catalogo e casos ambiguos estao em [motivos-de-fechamento-otrs.md](motivos-de-fechamento-otrs.md).

## Reclassificacao TELMEX no mirror local

Antes da reclassificacao, 80 links presentes tinham `business_project=TELMEX`; nenhum tinha `contracting_client=TELMEX`, pois a regra do cliente ja canonizava o token como Claro. Em 25/10/2026, `classificar_identidade_cmdb --apply` recalculou os campos derivados dos 3.437 nomes originais: Projeto TELMEX caiu para 0, sem editar `CMDBLinkMirror.name` nem escrever no CMDB. Depois, 273 nomes contendo TELMEX estao sob Cliente Claro; 192 preservam o contexto derivado `CLARO / TELMEX`, 174 dos 273 tem Projeto Bradesco. O total Claro/Bradesco e 288. Tokens regionais `CLARO.UF` nao sao promovidos a Projeto.

Nos titulos OTRS, `TicketBusinessIdentityService` aplica a mesma regra em runtime: TELMEX/Claro e Cliente, Bradesco e Projeto quando explicito; titulo `CLARO - TELMEX` preserva o contexto raw combinado sem alterar o titulo do OTRS. Nenhum titulo foi regravado no mirror.

Opções seguras de `CodigoFechamento` encontradas na busca por categorias de encerramento:

| Fonte/campo | Valor categórico | Tickets |
|---|---|---:|
| Dynamic Field `CodigoFechamento` | `Pendência Financeira` | 448 |
| Dynamic Field `CodigoFechamento` | `PendenciaFinanceira` | 122 |
| Dynamic Field `CodigoFechamento` | `Cancelado` | 441 |
| Dynamic Field `CodigoFechamento` | `ChamadoCancelado` | 146 |
| Dynamic Field `CodigoFechamento` | `CancelamentoServico` | 5 |

Logo, foram confirmados **570 tickets** cujo código contém as variantes de Pendência Financeira acima. A busca agregada no campo `DetalheCodFechamento` encontrou menção a `finance` em 326 tickets; em **289** o mesmo valor também contém termo de pendência. `Especifique` teve uma ocorrência correspondente. Não foram retornados os textos livres.

Não há Dynamic Field com nome que corresponda a financeiro/pendência/inadimplência; o achado veio dos valores de `CodigoFechamento`, `DetalheCodFechamento` e `Especifique`. A categoria estruturada é informação real e útil; o detalhe livre precisa permanecer fora de agrupamentos até ser tratado. `ticket_history` fornece eventos `StateUpdate` e IDs de estado, mas não um campo estruturado próprio de motivo de encerramento. Não foi inferido motivo a partir de artigo.

Para analytics futuro, considerar “resolvidos/encerrados sem cancelados” como os estados de negócio `RESOLVIDO` e `ENCERRADO`, nunca como todo estado técnico `closed`:

```python
OTRSTicket.objects.filter(
    is_present_in_queue=True,
    business_status__in=("RESOLVED", "CLOSED"),
)
```

Equivalente conceitual no SQL do mirror: `is_present_in_queue = 1 AND business_status IN ('RESOLVED','CLOSED')`. Isso retorna o snapshot atual; não é uma série histórica de fechamento.

## 4. Dimensões da fila

As contagens e percentuais abaixo têm como denominador os 11.971 tickets atuais.

### Serviço

| Serviço | Tickets | % |
|---|---:|---:|
| Internet | 10.712 | 89,48% |
| Link Operadora | 765 | 6,39% |
| Telefonia | 102 | 0,85% |
| Links Operadora | 87 | 0,73% |
| Core | 46 | 0,38% |
| Microinformatica | 42 | 0,35% |
| Wirelles/Hotspot | 31 | 0,26% |
| Relatórios | 27 | 0,23% |
| GMUD | 24 | 0,20% |
| Wifi (Internet) | 19 | 0,16% |
| Sem valor | 18 | 0,15% |
| Links não geográfico | 14 | 0,12% |
| Monitoramento | 13 | 0,11% |
| Infraestrutura | 12 | 0,10% |
| Ponto de rede | 12 | 0,10% |
| Firewall | 11 | 0,09% |
| Requisição de serviço | 8 | 0,07% |
| Suporte Remoto | 6 | 0,05% |
| PABX | 5 | 0,04% |
| Perfil de usuário | 5 | 0,04% |
| Projeto especial | 4 | 0,03% |
| Servidor | 4 | 0,03% |
| Radiocomunicação falha parcial do sistema | 2 | 0,02% |
| Radiocomunicação terminal | 1 | 0,01% |
| Requisição de material | 1 | 0,01% |

**Avaliação: POUCO ÚTIL como filtro principal.** O serviço `Internet` concentra 89,48%; pode permanecer como dimensão secundária, mas não justifica espaço de destaque no Cockpit.

### SLA

| SLA | Tickets | % |
|---|---:|---:|
| 24 Horas 8x5 | 7.537 | 62,96% |
| 24 Horas 24x7 | 1.948 | 16,27% |
| 3 Horas 24x7 | 723 | 6,04% |
| 3 Horas 8x5 | 603 | 5,04% |
| 8 Horas corridas (24x7) | 458 | 3,83% |
| 4 Horas 24x7 | 154 | 1,29% |
| Sem valor | 101 | 0,84% |
| 60 dias 8x5 | 93 | 0,78% |
| 10 dias 24x7 | 92 | 0,77% |
| Sem SLA | 65 | 0,54% |
| 360 Horas 24x7 | 54 | 0,45% |
| 360 Horas 8x5 | 43 | 0,36% |
| 06 Horas 24x7 | 23 | 0,19% |
| 72 Horas 8x5 | 20 | 0,17% |
| 06 Horas 8x5 | 11 | 0,09% |
| 72 Horas 24x7 | 10 | 0,08% |
| 8 Horas 8x5 | 10 | 0,08% |
| 12 Horas 8x5 | 8 | 0,07% |
| 240 Horas 8x5 | 8 | 0,07% |
| 48 Horas 24x7 | 5 | 0,04% |
| 60 dias 24x7 | 3 | 0,03% |
| 120 Horas 8x5 | 1 | 0,01% |
| 45 dias corridos 24x7 | 1 | 0,01% |

**Avaliação: ÚTIL como filtro secundário.** Há variedade de SLA, mas o valor associado não prova cumprimento. Não apresentar KPI de SLA cumprido sem calendário, regra de pausa e critério de resolução acordados.

### Prioridade

| Prioridade | Tickets | % |
|---|---:|---:|
| Medio | 10.945 | 91,43% |
| Alto | 860 | 7,18% |
| Critico | 123 | 1,03% |
| Baixo | 39 | 0,33% |
| Muito baixo | 4 | 0,03% |

**Avaliação: POUCO ÚTIL como filtro principal.** Pode ser filtro compacto para destacar Alto/Crítico; a forte concentração em Médio reduz seu valor para análise geral.

### Tipo

| Tipo | Tickets | % |
|---|---:|---:|
| Restauração | 11.528 | 96,30% |
| Solicitação | 425 | 3,55% |
| Mudança | 18 | 0,15% |

**Avaliação: NÃO RECOMENDADO como filtro de destaque.** Restauração domina a fila.

## 5. Dynamic Fields presentes nos tickets NOC

Foram agregados apenas campos com `object_type=Ticket` e valores ligados a tickets da fila atual. Percentual usa 11.971 como denominador. Valores brutos não foram consultados ou documentados. Campos cadastrais que podem identificar clientes continuam sem exemplos nem valores.

| Dynamic Field | Preenchidos | % | Valores distintos | Leitura para analytics |
|---|---:|---:|---:|---|
| SLASolucao | 11.968 | 100,0% | 2 | Indicador binário candidato; validar semântica antes de KPI |
| SLAPrimeiraResposta | 11.964 | 99,9% | 2 | Indicador binário candidato; validar semântica |
| ticketResolvido | 11.913 | 99,5% | 11.898 | DateTime de resolução potencial; usar como data, não categoria, após validação |
| FirstResponse | 11.907 | 99,5% | 1 | Sem variação observada; não serve como filtro |
| Localidade | 11.902 | 99,4% | 26 | Localidade é útil só após validação de escopo e normalização |
| SGFServicos | 11.452 | 95,7% | 8 | Serviço de sistema; validar contra `service` antes de substituir dimensão |
| SGFLocalidade | 11.401 | 95,2% | 70 | Não usar sem definição de negócio/documentação |
| SGFIDLocalidade | 11.306 | 94,4% | 57 | Identificador; não exibir como dimensão |
| SGFSubServico | 11.198 | 93,5% | 22 | Candidato secundário após validação |
| estadoResolvido | 10.984 | 91,8% | 1 | Sem variação; não serve como filtro |
| SGFRazaoSocial | 10.908 | 91,1% | 514 | Identifica organização; excluir de agrupamentos operacionais gerais |
| SGFNomeFantasia | 10.878 | 90,9% | 532 | Identifica organização; não duplicar Provedor/Link |
| SGFPrimeiroAtendimento | 10.617 | 88,7% | 11 | Candidato a métrica, mas sem definição validada |
| CodigoFechamento | 10.310 | 86,1% | 54 | **Útil:** código categórico de encerramento |
| AberturaChamadoISP | 7.662 | 64,0% | 2 | Dropdown; candidato para análise de acionamento ISP |
| DetalheCodFechamento | 7.432 | 62,1% | 4.675 | Texto livre; não agrupar nem mostrar cru |
| EnviarN1 | 7.430 | 62,1% | 1 | Sem variação; não serve como filtro |
| CircuitoDB | 7.275 | 60,8% | 1.721 | Campo técnico; preferir associação CMDB canônica |
| ISP | 7.228 | 60,4% | 1.320 | Campo textual; preferir Provedor resolvido via Link CMDB |
| CidadeDB | 7.224 | 60,3% | 1.466 | Campo técnico; preferir geografia do Link CMDB |
| DesignacaoDB | 6.991 | 58,4% | 1.981 | Campo técnico; preferir Link CMDB associado |
| AtivodeRede2 | 6.857 | 57,3% | 1.983 | Alto cardinal; não usar como filtro geral |
| DataAberturaISP | 6.474 | 54,1% | 6.473 | DateTime potencial para duração de acionamento; validar regra |
| SGFIDCliente | 4.817 | 40,2% | 286 | Identificador de cliente; não exibir |
| Anexo | 2.994 | 25,0% | 1 | Sem variação; não serve como filtro |
| ChamadoResolvido | 2.766 | 23,1% | 2 | Indicador parcial; `ticketResolvido` tem cobertura maior |
| SGFEndereco | 2.503 | 20,9% | 222 | Endereço; excluir da análise e da interface |
| RvalidacaoProcedenteImprocedente | 1.848 | 15,4% | 2 | Candidato binário com cobertura parcial; validar semântica |
| ProcessManagementActivityID | 1.786 | 14,9% | 6 | Campo de workflow, não dimensão do Cockpit |
| ProcessManagementProcessID | 1.786 | 14,9% | 5 | Campo de workflow, não dimensão do Cockpit |
| ReincidenciaDesignacao | 1.528 | 12,8% | 2 | Indicador parcial; não substitui reincidência calculada por Link |
| ZDescription | 682 | 5,7% | 46 | Conteúdo de integração livre; não exibir |
| ZEventID | 682 | 5,7% | 682 | Identificador; não agrupar |
| ZHostName | 682 | 5,7% | 216 | Host técnico; fora do Cockpit geral |
| ZID | 682 | 5,7% | 1 | Sem variação |
| ZLastValue | 682 | 5,7% | 42 | Valor técnico de integração |
| ZPriorityName | 682 | 5,7% | 4 | Não substitui prioridade OTRS |
| ZTriggerID | 682 | 5,7% | 226 | Identificador de evento |
| ZTriggerStatus | 682 | 5,7% | 4 | Campo técnico de integração |
| ViaAtendente | 495 | 4,1% | 1 | Sem variação |
| IdentificacaoBRAD | 471 | 3,9% | 100 | Identificador; não exibir |
| AtivoRede | 366 | 3,1% | 301 | Alto cardinal e baixa cobertura |
| ViaChat | 338 | 2,8% | 1 | Sem variação |
| Especifique | 72 | 0,6% | 60 | Texto livre; não agrupar nem mostrar cru |
| Filial | 44 | 0,4% | 15 | Cobertura muito baixa |
| enviarNotificacao | 39 | 0,3% | 1 | Sem variação |
| MUDAprovacao | 18 | 0,2% | 1 | Sem variação |
| GMUDAtualizardocumentacao | 10 | 0,1% | 1 | Sem variação |
| DesignacaooSelectCongItemRestauracao | 2 | <0,1% | 2 | Cobertura residual |
| EquipamentoSelectConfigItemTXT | 2 | <0,1% | 1 | Cobertura residual |
| LinkSimOuNao | 2 | <0,1% | 2 | Cobertura residual |
| MACAddressSelectConfigItemTXT | 2 | <0,1% | 1 | Cobertura residual |
| MasterSlave | 2 | <0,1% | 1 | Cobertura residual |
| MACAddress | 1 | <0,1% | 1 | Identificador técnico; não exibir |
| TicketClienteDB | 1 | <0,1% | 1 | Sem cobertura para analytics |

`ItemOperacionalSimNao` existe como definição `FieldSet`, mas não teve nenhum valor associado aos tickets NOC: **0 preenchidos, 11.971 vazios**. Os campos destacados no pedido:

| Campo | Preenchidos | Vazios | Serve para analytics? |
|---|---:|---:|---|
| ISP | 7.228 | 4.743 | Provedor CMDB é preferível; campo textual tem 1.320 variantes |
| CircuitoDB | 7.275 | 4.696 | Somente referência; associação CMDB é a dimensão correta |
| DesignacaoDB | 6.991 | 4.980 | Somente referência; alto cardinal |
| TicketClienteDB | 1 | 11.970 | Não |
| ReincidenciaDesignacao | 1.528 | 10.443 | Não como métrica oficial; cobertura parcial |
| ticketResolvido | 11.913 | 58 | Potencial como timestamp; não como categoria |
| AberturaChamadoISP | 7.662 | 4.309 | Candidato secundário, validar definição |
| DataAberturaISP | 6.474 | 5.497 | Potencial para duração de acionamento, validar definição |
| ItemOperacionalSimNao | 0 | 11.971 | Não |

## 6. Relações com CMDB, Projeto, UF e reincidência

Universo de relações: tickets atuais da NOC e relações atuais presentes no mirror.

| Indicador | Resultado |
|---|---:|
| Tickets com Link local | 7.501 (62,66%) |
| Tickets sem Link local | 4.470 (37,34%) |
| Tickets com Provedor resolvido | 7.371 (61,58%) |
| Tickets sem Provedor resolvido | 4.600 (38,42%) |
| Provedores distintos | 848 |
| Tickets com `project_legacy` preenchido | 0 |
| Tickets sem Projeto legado | 11.971 |
| Valores distintos de `project_legacy` | 0 |

`project_legacy` permanece uma referência local legada e não é Projeto oficial. Nenhum ticket da fotografia atual tinha esse campo preenchido.

Top UFs por Links distintos relacionados à NOC: **SP 403, MG 278, GO 142, BA 128, RJ 123, PR 103, CE 102, PA 93, PE 92, MT 75**. A contagem é de Links únicos por UF, não de tickets.

### Reincidência

Definição aplicada: mesmo Link local associado a **dois ou mais tickets distintos**, criados nos 365 dias anteriores à fotografia, que permanecem atualmente na NOC. Resultado: **1.007 Links reincidentes**. Tickets por Link no Top 10, em ordem: **28, 24, 22, 21, 21, 21, 19, 19, 18, 18**. Identificadores e nomes dos Links foram omitidos deste documento; a classificação usou `cmdb_link_id` e `source_number`, sem expor nomes de clientes ou circuitos.

`ReincidenciaDesignacao` tem somente 2 valores distintos e cobertura de 12,8%; o indicador calculado por relação Link↔tickets é mais reproduzível e deve ser a métrica oficial.

## 7. Últimos 12 meses por criação

Janela móvel: 24/10/2025 a 24/10/2026. Outubro/2025 e outubro/2026 são meses parciais. **A tabela agrupa tickets criados no mês por seu estado atual hoje; não reconstrói o estado que tinham no mês de criação.** Total na coorte: **5.881 tickets**.

| Mês de criação | Criados | ENCERRADO atual | RESOLVIDO atual | CANCELADO atual | PENDENTE atual | ABERTO atual | OUTRO atual |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2025-10 (parcial) | 85 | 76 | 0 | 9 | 0 | 0 | 0 |
| 2025-11 | 329 | 284 | 0 | 45 | 0 | 0 | 0 |
| 2025-12 | 398 | 363 | 0 | 35 | 0 | 0 | 0 |
| 2026-01 | 433 | 393 | 0 | 40 | 0 | 0 | 0 |
| 2026-02 | 433 | 391 | 0 | 42 | 0 | 0 | 0 |
| 2026-03 | 484 | 447 | 0 | 37 | 0 | 0 | 0 |
| 2026-04 | 500 | 455 | 0 | 45 | 0 | 0 | 0 |
| 2026-05 | 518 | 471 | 0 | 47 | 0 | 0 | 0 |
| 2026-06 | 422 | 388 | 0 | 34 | 0 | 0 | 0 |
| 2026-07 | 491 | 438 | 0 | 53 | 0 | 0 | 0 |
| 2026-08 | 526 | 478 | 0 | 48 | 0 | 0 | 0 |
| 2026-09 | 643 | 555 | 0 | 88 | 0 | 0 | 0 |
| 2026-10 (parcial) | 619 | 497 | 18 | 87 | 4 | 6 | 7 |
| **Total** | **5.881** | **5.236** | **18** | **610** | **4** | **6** | **7** |

Dentro dessa coorte, `RESOLVIDO + ENCERRADO` atuais somam 5.254; cancelados são contados à parte e não entram como sucesso.

## 8. Histórico e viabilidade de tempos

Nos tickets atualmente associados à NOC foram contadas **38.843 ocorrências `StateUpdate`**. Estados de destino observados no histórico incluem `em atendimento`, `resolvido`, `closed successful`, `Pendente`, `cancelado`, `pendente operadora`, `agendado`, `pendente outros`, `pendente cliente local fechado`, `new` e `Encerrado com viabilidade`.

Eventos relevantes observados (contagem de eventos, não de tickets):

| `history_type` | Eventos | Uso futuro possível |
|---|---:|---|
| `NewTicket` | 13.338 | Marco inicial, junto de `ticket.create_time` |
| `StateUpdate` | 38.843 | Transições; combinar `ticket_history.state_id` e `create_time` |
| `Move` | 25.334 | Mudanças de fila via `queue_id`; validar a semântica antes de medir entrada/saída |
| `TicketDynamicFieldUpdate` | 282.910 | Mudanças em campos; valores livres não devem ser importados sem regra de privacidade |
| `SetPendingTime` | 28.569 | Agendamento de pendência |
| `ServiceUpdate` | 22.548 | Mudanças de serviço |
| `SLAUpdate` | 22.472 | Mudanças de SLA |
| `PriorityUpdate` | 5 | Mudanças de prioridade |
| `TypeUpdate` | 21.628 | Mudanças de tipo |
| `TicketLinkAdd` / `TicketLinkDelete` | 454 / 2 | Mudanças de relações |

Estruturalmente, eventos `StateUpdate` com estado e data permitem ordenar transições por ticket e estimar entradas em estados e duração entre transições. Antes de publicar tempos, é necessário validar completude/ordem dos eventos, transições duplicadas, tickets mesclados e o fuso horário. `Move` e `queue_id` são candidatos a reconstruir entrada/saída da fila, mas esta fotografia não validou a semântica anterior/depois de cada movimento.

O histórico sozinho identifica o estado de destino dos eventos de estado, mas **não um motivo de encerramento estruturado**. `CodigoFechamento` é a fonte categórica observada para motivo; `DetalheCodFechamento` é texto livre. Não se deve inferir motivo pelo texto de artigos. Nenhuma tabela/corpo de artigo foi consultado neste estudo.

## 9. Recomendação para o Cockpit futuro

### Filtros

| Dimensão | Manter? | Motivo |
|---|---|---|
| Status de negócio | Sim | Backlog e terminais dependem da classificação explícita |
| Situação OTRS | Sim, secundário | Permite distinguir o estado real, inclusive `agendado` |
| Motivo de encerramento | Sim, categórico | `CodigoFechamento` tem 54 valores e 86,1% de preenchimento; não usar detalhe livre |
| Provedor | Sim | 848 provedores distintos resolvidos pela relação CMDB |
| Projeto | Não | `project_legacy` vazio nos 11.971 tickets e não oficial |
| UF | Sim | Há distribuição geográfica útil por Link |
| Serviço | Secundário ou remover do destaque | 89,48% em `Internet` |
| SLA | Secundário | Diversidade real, mas associação não prova cumprimento |
| Prioridade | Opcional/compacto | 91,43% Médio; destaque só para Alto/Crítico |
| Tipo | Não como destaque | 96,30% Restauração |
| Mês de criação | Sim | Volume de tickets criados; deixar claro que o estado do gráfico é o atual |

### Métricas e agrupamentos

- Métricas atuais: criados no período, backlog, em atendimento, pendentes, resolvidos, encerrados, cancelados, resolvidos+encerrados excluindo cancelados, tickets com Link/Provedor, Links afetados e Links reincidentes.
- Snapshot observado em 24/10/2026 apos FULL: backlog 17; resolvidos 18; encerrados 10.950; cancelados 986; desconhecidos 0.
- Agrupamentos recomendados: status de negócio, situação OTRS, código de fechamento, Provedor, UF e mês de criação. Serviço, SLA e prioridade podem ser secundários. Não agrupar por detalhe livre nem por campos identificadores de cliente.
- Remover do destaque: serviços genéricos como filtro principal, Projeto legado, `TicketClienteDB`, dados de endereço/razão social, campos de integração Z* e texto livre de fechamento. `ticketResolvido`/`DataAberturaISP` são datas, não categorias.

## 10. Critério de uso

Este documento registra uma fotografia agregada para orientar uma próxima tarefa do Cockpit. Não altera semântica, dados ou interface do Arion. O OTRS continua sendo fonte oficial; os mirrors locais são uma camada de consulta e análise.
