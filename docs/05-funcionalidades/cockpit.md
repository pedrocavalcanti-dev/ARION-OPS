# Cockpit Operacional

O Cockpit é a área de análise operacional do ARION OPS. Ele permite consultar o espelho local de tickets OTRS e vínculos CMDB por período, contexto e filtros, com indicadores, fluxo operacional, aging, distribuições, evidências e exportações.

O OTRS/CMDB permanece como fonte oficial. O Cockpit não consulta o OTRS remoto durante uma análise; a página e a API leem os espelhos sincronizados localmente.

## Acesso e proteção dos dados

A página está disponível em `/cockpit/`; a consulta JSON usa `/api/analytics/query/`, as exportações usam `/api/analytics/export/` e as consultas salvas usam `/api/analytics/saved-views/`.

A permissão de acesso ao Cockpit é verificada no backend. O Data Scope é aplicado antes de filtros, agregações, rankings, paginação e exportação. A interface não concede acesso a operadoras, projetos, tickets ou vínculos fora do escopo autorizado.

Consultas salvas pertencem ao usuário autenticado. Seus filtros e opções são reaplicados pelo serviço central sob o scope e as permissões atuais.

## Como a consulta é composta

A API normaliza a consulta e encaminha o mesmo queryset filtrado para métricas, série do período, calendário, agrupamentos e evidências. Os componentes têm semânticas temporais próprias quando indicado abaixo, mas partem do contexto, do Data Scope e dos filtros selecionados.

Os filtros de data formam um intervalo inclusivo no calendário local (`America/Sao_Paulo`). A granularidade do gráfico muda conforme o tamanho do período. Períodos muito extensos são agrupados por mês; os buckets vazios continuam representados.

A busca **Buscar nos tickets** pesquisa somente a tabela de evidências. Ela pode reduzir os registros exibidos nessa tabela, mas não muda KPIs, gráficos, distribuições ou o total da análise. A exportação não aplica essa busca: ela representa a consulta analítica selecionada.

## Contextos

### Suporte

Abrange a fila `NOC - ATACADO`. As métricas de tickets usam estados operacionais conhecidos. Tickets `UNKNOWN` permanecem fora; cancelados e improcedentes são considerados por padrão nas análises históricas aplicáveis, conforme a semântica de cada métrica.

### Cancelamentos

Abrange as filas configuradas para o processo de cancelamento. Os tickets são classificados pelo serviço central de etapas, usando fila e estado disponíveis no espelho. A classificação ainda pode mostrar uma etapa `UNKNOWN` para investigação, mas esses tickets não entram no backlog, aging ou total de estoque operacional.

A classificação usa os perfis operacionais sincronizados do OTRS. Solicitação e Em tratamento podem compartilhar a fila física; a combinação com o estado diferencia as etapas. Os cards representam estoque atual por etapa, e não quatro fluxos independentes.

### Geral

Combina Suporte e Cancelamentos e deduplica tickets pelo identificador externo. A origem é identificada pela fila no espelho. O gráfico pode apresentar o fluxo consolidado ou separado por origem.

## Métricas e datas

| Métrica | Regra temporal e universo |
| --- | --- |
| Tickets criados | `source_created_at`; tickets distintos nos estados válidos do contexto. Cancelados e improcedentes obedecem às opções gerenciais quando compatíveis. |
| Em atendimento, Pendentes e Agendados | `source_created_at` no período e estado atual correspondente. |
| Resolvidos | Categoria operacional `RESOLVED`, incluindo estados OTRS brutos Resolvido e Encerrado, pela data operacional de resolução. |
| Cancelados | Estado `CANCELED`, pela data de cancelamento; métrica específica. |
| Backlog atual | Snapshot somente de `OPEN` e `PENDING`; `SCHEDULED`, `CANCELED`, `UNKNOWN` e improcedentes ficam de fora. A data selecionada não altera o estoque atual. |
| Links afetados | Links CMDB distintos relacionados aos tickets do recorte. Um ticket pode se relacionar a mais de um link. |
| Links reincidentes | Links distintos que atendem à regra de recorrência sobre tickets distintos no período. Não representa o estoque atual de links afetados. |

A evidência apresenta separadamente base_date, created_at (ticket.create_time no OTRS) e resolved_at (a resolução operacional efetiva) no fuso configurado para o ARION, em dd/mm/aaaa hh:mm. A busca, paginação e filtros continuam aplicados ao mesmo queryset e o período analítico não é substituído por essas colunas informativas. CSV e XLSX exportam as duas datas reais junto da data-base.

Para a resolução, o serviço prefere ticketResolvido (resolved_at). Se o estado externo atual for exatamente resolvido ou merged, compara o último StateUpdate compatível com o estado atual para selecionar o ciclo de resolução mais recente após reabertura. Em closed successful, encerrado com sucesso e encerrado com viabilidade, a data terminal pode representar encerramento administrativo posterior e não é usada como fallback: se ticketResolvido não existir, a coluna mostra —. Para estados que não estejam resolvidos também se mostra —, evitando que um timestamp conservado de ciclo anterior pareça a resolução atual.

terminal_at continua disponível no detalhe do ticket como última transição terminal observada; não há campo separado confiável de encerramento administrativo no espelho e ele não é apresentado como Encerrado em. Cancelamentos permanecem em canceled_at e não entram na coluna de resolução. Datas ausentes ou anteriores à criação não são inventadas nem usadas para duração.

A dimensão Provedor pode incluir o mesmo ticket em mais de um grupo quando ele tiver vínculos com provedores diferentes. O total geral continua contando o ticket uma vez. O ranking mostra provedores identificados; tickets sem provedor permanecem no resultado e são apresentados na qualidade dos vínculos.

## Fluxo operacional, backlog e aging

O fluxo usa tickets filtrados no contexto atual. **Novos** usa `source_created_at`; **Resolvidos** inclui a saída operacional `RESOLVED`, que consolida os nomes brutos OTRS Resolvido e Encerrado. A data de saída usa os timestamps operacionais descritos acima.

O Cockpit considera cancelados e improcedentes por padrão nas análises históricas aplicáveis. As opções ficam em **Mais filtros** e permitem excluí-los. Cancelados podem fazer parte do histórico, mas não entram no backlog atual, aging ou tickets ativos.

O atalho **Improcedentes** continua filtrando especificamente os tickets cuja procedência é improcedente. Improcedentes não entram no snapshot atual de backlog ou aging. A métrica **Backlog atual** permanece restrita a `OPEN` e `PENDING`.

O saldo do fluxo e a variação do estoque podem divergir: backlog e aging representam somente tickets `OPEN` e `PENDING`, sem `SCHEDULED`, improcedentes ou desconhecidos.

### Backlog histórico reconstruído e backlog observado

Em **Suporte**, o modo **Backlog** usa os intervalos locais `OTRSBacklogHistory` / `OTRSBacklogInterval`, reconstruídos do `ticket_history` autorizado. A população inclui tickets que passaram pela fila exata `NOC - ATACADO`, inclusive os que já saíram dela. Não se limita aos tickets atualmente presentes no mirror. Os eventos de estado e fila permitem contar entradas, saídas, cancelamentos e reaberturas. O histórico do campo de procedência permite excluir improcedentes no instante em que a classificação era conhecida. Cada instante conta TicketIDs distintos, somente em `OPEN` ou `PENDING`; estados terminais, agendados e desconhecidos não compõem o estoque.

O comando `sincronizar_historico_backlog` usa a integração e o cliente OTRS existentes, com transação de leitura consistente, lotes limitados e lease compartilhado. Lê somente IDs, estados, filas, datas e o campo específico de procedência; não lê artigos, anexos, autores ou títulos. Por padrão faz preview; `--apply` substitui atomicamente apenas o cache local. Falhas preservam a cobertura anterior. O `integration_worker` agenda essa carga a cada hora, com estado e backoff próprios; nenhuma consulta HTTP acessa o OTRS remoto.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_historico_backlog
.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_historico_backlog --apply
```

A migration aditiva `integracoes.0016` cria o cache. A reconstrução parte dos estados realmente registrados, sem supor backlog inicial zero, sem usar o estado atual como estado passado e sem somar Novos menos Resolvidos do mirror para simular um ledger. Os pontos correspondem ao fechamento do intervalo selecionado, limitado ao horário coberto pela extração. O mês corrente pode ter ponto parcial no horário real de cobertura. Granularidade horária, diária, semanal ou mensal segue o período da consulta. Os DATETIME dos eventos seguem o fuso local OTRS já utilizado pelo sincronizador; o limite da carga usa explicitamente `UTC_TIMESTAMP()` em UTC, sem aplicar novamente o deslocamento local ao relógio SQL. As sete tabelas da fonte são verificadas como InnoDB antes da leitura consistente.

Antes da primeira evidência de um ticket ou em uma cauda sem reconciliação com a fonte, o intervalo permanece desconhecido. Se uma dessas lacunas atingir o instante do ponto, o valor é `NULL`, com indicação de indisponibilidade; nunca vira zero. A linha histórica é interrompida nesses trechos. Eventos fisicamente apagados da origem não podem ser recuperados pelo cache; histórico com referências a tickets ausentes impede a publicação silenciosa de uma população incompleta.

Data Scope é aplicado antes da agregação pelos vínculos CMDB atualmente autorizados, inclusive para tickets que deixaram a fila. Provedor, UF, cliente/projeto, serviço, SLA, responsável e motivo usam as dimensões disponíveis atualmente no mirror quando selecionados; esse recorte não prova a atribuição histórica dessas dimensões. Estado, fila, procedência e idade, por sua vez, são avaliados no instante histórico. Um vínculo permitido não amplia as opções de filtro para vínculos não autorizados. Superuser conserva o acesso global.

**Backlog observado** continua separado em `operational.backlog` e nos campos originais da série, com origem e horário FULL. Cada FULL aplicada com sucesso grava um snapshot imutável por dia e assinatura de filas, com as dimensões de ConfigItems capturadas. Esses pontos são fotografias, não fechamentos históricos. Se o cache de eventos ainda não estiver carregado, o gráfico usa as observações disponíveis, preservando o horário de referência no tooltip. Os contextos Geral e Cancelamentos mantêm suas observações existentes. Os indicadores atuais e o aging não são recalculados por esta série.

**Ambos** exibe Novos e Resolvidos; **Novos** e **Resolvidos** isolam as respectivas barras. **Backlog** mostra os chamados que permaneceram abertos no fechamento de cada mês (mês corrente até a última atualização), com legenda simples, valor e data/hora no tooltip. Os valores mensais ficam visíveis nos pontos em telas maiores. Textos técnicos de reconstrução, eventos e origem não aparecem no gráfico; a distinção das fontes permanece nos metadados e nesta documentação. Avisos de histórico indisponível ou observação única são preservados. Capturas FULL não são misturadas na linha reconstruída.

### Diagnóstico da regressão e validação da recuperação

| Versão | Cálculo e consequência |
| --- | --- |
| `c25e6dd` | `AnalyticsQueryService._operational_summary` montava baseline e saldo acumulado por criação e encerramento dos tickets do mirror atual. Dependia de completude de datas; não tinha todos os ciclos de reabertura ou transferências. |
| `ac1c722` | Desativou a reconstrução não comprovada pelo mirror mutável. A correção da normalização de estados tornou as lacunas de datas mais visíveis. |
| `1ebc12c` | Substituiu a origem da série por capturas FULL locais, sem recuperar os eventos históricos da origem. |
| `649b8eb` | Explicou a captura única na interface, mas não recuperou a evolução histórica. |
| Implementação atual | Reconstrói intervalos com eventos reais OTRS e preserva fotografias FULL como métrica separada. |

Na validação DEV, a extração de **10/11/2026 às 09:50:06 (America/Sao_Paulo)** encontrou **13.190 tickets**, **105.183 eventos selecionados** e **17.295 intervalos ativos ou explicitamente desconhecidos**. Os **12 fechamentos calculados**, de dezembro/2025 a novembro/2026, tiveram cobertura suficiente: **9, 13, 12, 26, 11, 17, 19, 11, 13, 3, 12, 31**. Novembro representa o instante final da extração, não o fim futuro do mês. São valores calculados dos intervalos, não apenas 12 rótulos no eixo. A fotografia FULL anterior de **35 em 09/11 às 11:27** continua como observação separada; os horários e as origens diferem. Esses números documentam o recorte conferido; a carga horária pode atualizar o mês corrente.

**Novos** e **Resolvidos** usam as datas disponíveis nas linhas atuais do mirror e contam TicketIDs distintos. Um ticket em `RESOLVED` ou no alias legado `CLOSED` contribui uma vez para a categoria Resolvido; cancelamento permanece separado. Como o mirror não guarda todos os ciclos de reabertura nem tickets que saíram das filas, essas séries podem omitir eventos antigos. Elas não são um ledger histórico completo e não devem ser usadas para reconstruir o estoque. A data de criação não representa entrada na fila. Dimensões de Provedor, UF e Projeto/cliente nos eventos atuais descrevem os vínculos disponíveis no mirror, não comprovam a atribuição histórica na data do evento.

O aging é um snapshot do backlog `OPEN`/`PENDING` em faixas `0-4h`, `4-8h`, `8-24h`, `24-48h`, `48-72h` e `>72h`, calculadas desde `source_created_at`. Não é tempo médio de resolução. A faixa **Acima de 24h** é um filtro de backlog atual, não uma métrica histórica.

O tempo médio de resolução é calculado separadamente para tickets `RESOLVED` com duração válida, incluindo linhas legadas `CLOSED`. Cancelamentos não são tratados como resoluções médias.

## Opções gerenciais e filtros

As opções de registros considerados ficam em **Mais filtros**. Ambas vêm ligadas por padrão e são preservadas na URL e nas consultas salvas:

- **Incluir cancelados**: mantém cancelamentos nas análises históricas aplicáveis. Métricas específicas de estado `RESOLVED` (incluindo o alias legado `CLOSED`) não passam a contar cancelamentos.
- **Incluir improcedentes**: mantém improcedentes nas métricas compatíveis e no histórico. O estoque atual permanece operacional e sem improcedentes.

O estado das opções é preservado na URL e nas consultas salvas. Quando uma opção é desmarcada, o PDF registra a exclusão correspondente.

Os demais filtros incluem Provedor, UF, Status, Status de origem, Serviço, SLA, Prioridade, Motivo, Etapa, Origem, Projeto/cliente, Fila, Procedência, Responsável e Aging. A tabela de evidências apresenta uma única coluna **Status**, com `RESOLVED` e `CLOSED` consolidados como **Resolvido** e **Cancelado** separado. O estado bruto continua no campo de origem para diagnóstico. Data Scope é aplicado antes dos filtros e agrupamentos.

As categorias de motivo usam `ClosingReasonCanonicalizer` para mapear valores equivalentes a uma chave e um rótulo estáveis. O valor raw continua preservado em coluna própria nas exportações de tickets.

## Consultas rápidas

Os atalhos representam consultas reais e preservam contexto e período, salvo os filtros que o próprio atalho substitui:

- **Pendência Financeira** e **Falha Massiva** usam a categoria canônica de motivo.
- **Reincidência** usa a regra de recorrência sobre links e tickets distintos.
- **Improcedentes** e **Procedentes** filtram a procedência do ticket.
- **Alto / Crítico** aplica o conjunto de prioridades correspondente.
- **Acima de 24h** e **Sem responsável** consultam o backlog atual.
- **Em atendimento**, **Pendentes** e **Resolvidos** aplicam as métricas e datas operacionais correspondentes.

## Gráficos, calendário e evidências

A evolução operacional apresenta Novos e Resolvidos com eventos datados do recorte local e desenha backlog apenas nos horários cobertos pelos snapshots FULL. A seleção diária, semanal ou mensal escolhe a última observação dentro de cada intervalo; lacunas permanecem sem ponto e sem interpolação. Cada tooltip informa o horário observado. Cancelados e improcedentes podem compor o universo do fluxo sem criar séries visuais adicionais. Valores positivos são rotulados quando há espaço; zeros não recebem rótulo visual, mas permanecem disponíveis nos dados da consulta. Em séries densas, os rótulos são reduzidos para evitar sobreposição.

O gráfico de análise selecionada e o calendário usam a métrica principal. O calendário mostra o resumo mensal, os valores diários e o pico. A seleção de um dia pode filtrar temporariamente a evidência ou aplicar aquele dia como novo período, sem alterar a regra de Data Scope.

Os gráficos de dimensão mostram até dez grupos e são omitidos quando a dimensão está filtrada ou não tem variação útil. A distribuição completa permanece na tabela recolhível e paginada. A qualidade de vínculos explica tickets sem provedor identificado. O ranking de provedores usa logos estáticos conhecidos e iniciais como fallback.

A evidência lista tickets distintos que compõem o resultado, incluindo estado, datas operacionais, motivo, provedor, UF, prioridade, SLA e vínculos CMDB autorizados. A busca local da evidência não muda os indicadores da consulta.

## Exportações

PDF, CSV e XLSX usam `AnalyticsQueryService` e respeitam os filtros e o Data Scope vigentes. O PDF também registra o contexto, métrica, período, base temporal, agrupamento, opções gerenciais e comparação selecionada.

O PDF inclui o resumo executivo, indicadores operacionais complementares, evolução de Novos/Resolvidos/Backlog, calendário mensal, aging marcado como **Snapshot atual**, distribuições principais e tickets de evidência. Rankings e distribuições limitam-se aos dez primeiros grupos. Os valores das barras aparecem quando não causam sobreposição; zeros permanecem implícitos. O rodapé indica **Página X de Y**.

O anexo PDF limita-se aos primeiros 100 tickets e informa quantos foram exibidos do total analisado. CSV e XLSX contêm todos os tickets do resultado. CSV usa UTF-8 com BOM; XLSX contém as abas `Resumo`, `Distribuição` e `Tickets`. Os formatos preservam o motivo normalizado e seu raw, sem incluir conteúdo de artigos ou credenciais.

## Fonte de dados e limitações operacionais

A disponibilidade indicada na página depende do registro local da sincronização e da última FULL aplicada com a cobertura configurada para as filas do Cockpit. A consulta não inicia uma sincronização. Sem a cobertura inicial necessária, a interface identifica que os dados de etapas ainda não foram sincronizados.

A API é somente leitura em relação ao OTRS. A página não usa conexão remota para preencher indicadores, tickets ou rankings; a origem oficial continua sendo o OTRS/CMDB.

## Código relacionado

- Serviço central: `arion_geo/aplicativos/painel/services/analytics.py`
- Logos de provedores: `arion_geo/aplicativos/painel/services/provider_logos.py`
- Views de consulta e exportação: `arion_geo/aplicativos/painel/views.py`
- Interface: `arion_geo/templates/painel/cockpit.html`
- JavaScript: `frontend_src/js/painel/cockpit.js`
- Estilos: `arion_geo/static/css/painel/cockpit.css`
