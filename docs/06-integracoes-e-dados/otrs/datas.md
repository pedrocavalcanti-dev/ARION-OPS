# Datas operacionais OTRS

## Campos e origem

| Marco | Origem OTRS | Mirror/cálculo | Uso e ressalva |
|---|---|---|---|
| Criação do ticket | ticket.create_time | source_created_at | Período de criação, envelhecimento e recorrência |
| Última alteração | ticket.change_time | source_changed_at | Cursor FAST; não é hora de resolução |
| Data de resolução | Dynamic Field ticketResolvido em dynamic_field_value.value_date | resolved_at | Só existe se o campo estiver configurado e houver valor |
| Última entrada no estado terminal atual | MAX(ticket_history.create_time) para StateUpdate e estado atual | terminal_state_at | Só calculada quando o ticket atual está em estado terminal reconhecido |
| Data do cancelamento | Mesmo histórico terminal quando categoria atual é CANCELED | canceled_at | Não equivale a data de resolução |
| Criação de artigo | article.create_time | Selecionada na reconciliação manual | Não é data de resolução |
| Leitura/sync | Relógio do ARION | IntegrationRun, source_last_seen_at, source_synced_at | Marcação local, não timestamp do OTRS |

O código exige que Dynamic Fields CodigoFechamento e ticketResolvido existam para sync terminal e sejam do tipo Ticket. O campo opcional de procedência também é lido quando identificado como dropdown de Ticket. Campo ausente ou valor vazio produz dado local vazio/null; não há data inventada.

## Caminho dos marcos temporais

```mermaid
flowchart TD
SOURCE[Campos e historico OTRS] --> CREATE[create_time para criacao]
SOURCE --> CHANGE[change_time para alteracao e cursor]
SOURCE --> DYNAMIC[ticketResolvido value_date]
SOURCE --> HISTORY[StateUpdate e estado atual]
CREATE --> LOCAL[Conversao de timezone e espelho local]
CHANGE --> LOCAL
DYNAMIC --> LOCAL
HISTORY --> LOCAL
LOCAL --> METRICS[Indicadores com criterio temporal explicito]
```

## Conversão temporal

source_datetime aceita datetime do driver ou string ISO. Se o valor for naive, torna-o aware com o timezone configurado no Django; se já vier aware, conserva o offset. Para FAST, o cursor aware é convertido ao timezone padrão e enviado como datetime naive, como esperado pelo campo DATETIME consultado. O timezone efetivo depende da configuração do ambiente.

O código não lê offset de timezone da coluna OTRS, então não há prova independente de qual timezone o servidor usou ao gravar valores naive. A interpretação usa a configuração do ARION. Esse ponto deve ser considerado ao comparar minutos próximos a limites de período.

## Fallback operacional e estados

Para uma resolução efetiva, a fonte preferencial é resolved_at, derivada de dynamic_field_value.value_date para ticketResolvido. Quando o estado externo atual é explicitamente resolvido ou merged, o serviço usa o último StateUpdate que corresponde ao estado atual; se ele for posterior ao Dynamic Field, isso identifica o ciclo atual após reabertura. Essa regra não altera source_created_at nem cria transição de estado ausente.

terminal_state_at é obtida pelo maior create_time de StateUpdate para IDs terminais e só é aplicada quando state_id coincide com o estado atual. Se o estado atual continua resolvido, esse registro pode identificar uma nova entrada após reabertura. Se o OTRS já está em closed successful, encerrado com sucesso ou encerrado com viabilidade, o último StateUpdate pode ser o encerramento administrativo posterior: nesse caso ele não substitui ticketResolvido. Sem o Dynamic Field confiável, a interface mostra —. Para estado atual não resolvido, a coluna também mostra —, mesmo que o mirror conserve um valor histórico em resolved_at.

O catálogo não possui uma data de encerramento administrativo separada. terminal_state_at é apresentado como **última transição terminal registrada**, nunca rotulado como Encerrado em. O estado bruto continua preservado em external_state_name.

## Período de criação não é período de resolução

“Criado no período e atualmente resolvido” seleciona source_created_at no intervalo e consulta o status atual. “Resolvido durante o período” seleciona a data operacional de resolução no intervalo e exige estado atual operacional RESOLVED. Cockpit, Rankings e Mapa usam campos/datasets conforme cada métrica; não interprete todo total como fluxo de resoluções.

O indicador **Backlog atual** usa somente tickets `OPEN`/`PENDING` do mirror corrente. A linha histórica do Cockpit usa observações persistidas após FULL, nos horários capturados, e não projeta estoque ao fim do período. Novos e Resolvidos usam as datas do mirror corrente e não cobrem necessariamente todas as reaberturas ou tickets que saíram das filas.

## Histórico persistente do Cockpit

O esquema e a consulta documentados neste repositório não sustentam um backfill completo de transições. O serviço lê `ticket_history` para obter o maior `create_time` de StateUpdate que corresponda ao estado terminal atual; isso não recupera sequências de reabertura, todos os ciclos de resolução nem o pertencimento passado às filas. O dicionário não documenta uma chave única de evento adequada para deduplicar um ledger completo. A produção e o schema remoto não foram consultados para esta implementação; portanto não se presume que existam colunas ou garantias adicionais.

O ARION grava `OTRSOperationalSnapshot`, `OTRSOperationalTicketSnapshot` e `OTRSOperationalConfigItemSnapshot` após uma FULL aplicada e bem-sucedida. Cada captura tem uma assinatura das filas e unicidade por data local e assinatura; os detalhes de tickets e ConfigItems também possuem chaves únicas dentro da captura. FAST não grava snapshot e não gera transições inferidas. Repetir captura da mesma cobertura no mesmo dia mantém a primeira observação.

Não há comando de backfill histórico: os dados anteriores ao primeiro snapshot confiável continuam indisponíveis para backlog. Uma falha de captura é registrada no `IntegrationRun`; a próxima FULL pode capturar o dia atual, mas uma data passada sem snapshot não pode ser reconstruída pelo mirror. A implantação requer aplicar as migrations locais do app `integracoes` no banco ARION de cada ambiente, seguida de FULL normal; isso não escreve no OTRS. Não executar migration ou sync de produção sem o procedimento operacional e aprovação próprios.

Links: [estados](otrs-estados.md), [indicadores](otrs-indicadores.md), [limitações](otrs-limitacoes.md).
