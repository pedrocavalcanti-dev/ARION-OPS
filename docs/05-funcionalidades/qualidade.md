# Qualidade Operacional

## Objetivo

O módulo Qualidade Operacional organiza casos de recorrência sem substituir os registros oficiais do OTRS ou da CMDB. O OTRS continua sendo a fonte oficial do ticket; a CMDB continua sendo a fonte oficial dos links e ConfigItems. O banco do ARION conserva as ordens, snapshots e análises locais.

## Conceitos

- **OQ — Ordem de Qualidade:** acompanha um caso de recorrência no ARION OPS.
- **Ocorrência:** referência local a um ticket operacional que integra o histórico de uma OQ.
- **TQ — Ticket de Qualidade:** referência a um ticket OTRS usado na tratativa ou no monitoramento. O módulo ainda não cria nem altera tickets remotos.
- **Reincidência:** evento associado a uma ocorrência; não é status permanente da OQ.
- **RREIP:** ciclo de monitoramento que registra o período de verificação e seu resultado. O padrão é 30 dias (`QUALITY_RREIP_DAYS`).

## Estrutura

- Dashboard / Visão geral
- Links prioritários
- Ordens de Qualidade e detalhe da OQ com Resumo e Ocorrências
- Monitoramento RREIP
- Contatos e escalonamentos
- Histórico

As rotas usam o namespace `qualidade` sob `/qualidade/`. O dashboard combina OQs/ciclos locais com tickets válidos do mirror OTRS; `QualityOccurrence` enriquece a revisão, mas não é requisito para uma ocorrência operacional aparecer. A ausência de registros produz zeros e estados vazios, sem dados demonstrativos.

## Arquitetura e acesso

O app Django `aplicativos.qualidade` contém os models `QualityOrder`, `QualityOccurrence`, `QualityTicket`, `QualityMonitoringCycle`, `QualityContact` e `QualityAuditEvent`. `services/recurrence.py` centraliza a formação de episódios por circuito; dashboard, Links Prioritários, detalhe do Link, detalhe da OQ e monitoramento reutilizam essa política.

OQ pode se relacionar ao `CMDBLinkMirror` e mantém snapshots de designação, ConfigItem, cliente, projeto, ISP e UF. Referências de tickets usam o mirror `OTRSTicket` quando disponível e preservam identificadores e rótulos locais. Requests não chamam os serviços remotos nem escrevem no OTRS ou na CMDB.

A permissão `qualidade.view_quality` protege as páginas e a navegação. `QualityDataScopeService` e o `DataScopeService` existente restringem as OQs e os links no backend antes de filtros, agregações e paginação. Usuários fora do escopo não conseguem abrir uma OQ por URL direta; sem escopo válido, o acesso aos dados é negado. Superuser técnico permanece global.

Eventos da OQ ficam na linha do tempo específica e eventos relevantes também passam por `AuditService`, incluindo sua sanitização de metadata.

## Navegação e filtros

A navegação interna da Qualidade pode ser recolhida no desktop para ampliar o conteúdo e mantém essa preferência entre páginas pelo `localStorage` (`arion_quality_sidebar_collapsed`). No mobile, a navegação responsiva atual permanece ativa. O módulo usa o ícone funcional de checklist com confirmação na navegação global, sidebar e menu mobile. Os popovers de filtros são gerenciados pelo shell compartilhado: clique externo ou `Escape` fecha o painel, clique dentro mantém aberto e `Escape` devolve o foco ao controle.

## Dashboard

O Dashboard mostra resultados do período gerencial, o estoque atual de links reincidentes e OQs, e os ciclos RREIP. Evolução e concentração usam tickets válidos relacionados a links CMDB no mirror OTRS e a mesma análise central de episódios da carteira Prioritária. O gráfico de status usa OQs do escopo, inclusive uma única OQ. Cliente, projeto, ISP e UF filtram o recorte antes das agregações. O período gerencial pode ser 7, 30, 60, 90, 180 ou 365 dias, este mês ou mês anterior; ele nunca altera a janela operacional de 30 dias. “Links mais reincidentes” mostra o Top 5 do episódio ativo; “Estabilizados recentemente” lista até cinco episódios recorrentes que completaram 30 dias sem nova ocorrência. O Top 5 exibe Designação, cliente/projeto, provedor, reincidências do episódio atual e última ocorrência. Os gráficos usam ECharts local e exibem estado vazio quando não há dados.

## Dashboard de resultado operacional

- **Novas reincidências:** ocorrências válidas posteriores à ocorrência inicial dentro de seus episódios centrais, contadas pela data real do ticket no período. A regra do episódio é fixa: diferença de até 30 dias mantém o mesmo episódio; uma diferença maior inicia outro.
- **OQs abertas no período:** ordens do escopo criadas dentro da janela gerencial, pela data `created_at`.
- **OQs concluídas:** ordens concluídas dentro do período, usando `completed_at` e eventos locais `ORDER_CLOSED` ou `STATUS_CHANGED` para `CONCLUIDA`. Não se usa a data de criação como data de conclusão.
- **Em monitoramento:** quantidade atual de ciclos RREIP `EM_ANDAMENTO`, filtrada pelo escopo e pelos filtros da carteira; não depende do período histórico.
- **RREIP com sucesso** e **reincidiram pós-tratamento:** ciclos encerrados com `SUCESSO` ou `REINCIDIU` cuja data `ended_at` cai no período.
- **Efetividade:** sucessos divididos por sucessos mais reincidências entre ciclos concluídos no período. Ciclos em andamento e cancelados não entram. Sem ciclo concluído, a tela mostra “Sem dados suficientes”.
- **Resultado operacional:** série diária para períodos até 31 dias, semanal até 120 dias e mensal acima disso. Compara novas reincidências, OQs concluídas e ciclos que reincidiram após o tratamento. Os tooltips mostram os valores de cada intervalo.
- **Concentração das reincidências:** Top 5 por projeto canônico ou provedor, com ocorrências reincidentes válidas no período e nos filtros selecionados. A prioridade comercial orienta apenas destaque/ordenação dos links, nunca altera contagens ou a regra de reincidência. Aliases de projeto são agregados pelo serviço compartilhado de canonicalização.
- **Precisam de atenção:** mantém OQs críticas e vencidas, ciclos que reincidiram, OQs sem responsável e monitoramentos próximos do fim. A ordenação prioriza criticidade, prazo e resultado de RREIP.

O filtro gerencial define somente o recorte de análise. A reincidência continua sendo resolvida sobre a sequência válida completa do mirror pela regra operacional fixa de 30 dias. Os filtros de Cliente, Projeto, ISP e UF são aplicados após RBAC/Data Scope no backend e antes dos indicadores e agrupamentos.

### Regra de episódios de 30 dias e estabilização operacional

`build_quality_recurrence_analysis()` considera somente ocorrências válidas do espelho OTRS relacionadas ao ConfigItem e as ordena pela data operacional. Uma ocorrência até 30 dias depois da ocorrência válida anterior permanece no mesmo episódio; uma diferença maior abre outro episódio. A primeira ocorrência de cada episódio é inicial. As seguintes são reincidências operacionais R1, R2, R3, R4 e R5+; essa posição é calculada, reinicia no novo episódio e nunca altera `QualityOrder.recurrence_level`, que continua sendo a classificação manual da Qualidade.

O episódio atual é **reincidente ativo** quando tem pelo menos duas ocorrências válidas e ainda não completou 30 dias sem uma nova ocorrência. Com 30 dias completos de inatividade, um episódio que teve reincidência fica **operacionalmente estabilizado**. Uma única ocorrência permanece isolada e não conta como link reincidente estabilizado. Uma ocorrência após um intervalo maior que 30 dias inicia um novo episódio; a ligação com o episódio anterior fica registrada como reabertura após estabilização. O status é calculado a cada leitura e o histórico, ocorrências, OQs, tratativas, TQs e ciclos anteriores não são apagados.

Estabilização operacional é calculada sobre OTRS + CMDB e pode ocorrer sem OQ. Ela não significa que a Qualidade tratou o circuito e não aumenta a efetividade. Sucesso da Qualidade depende exclusivamente de ciclo RREIP concluído com resultado `SUCESSO`; reincidência após tratativa é um ciclo `REINCIDIU`.

No Dashboard, novas reincidências e eventos de estabilização seguem a data real do ticket ou o instante calculado de estabilização. Links reincidentes ativos são um estoque de agora e não mudam quando o período gerencial muda; filtros de Cliente, Projeto, ISP e UF continuam sendo aplicados. O indicador de Links estabilizados conta circuitos distintos que completaram a estabilização de um episódio reincidente no período; a série registra cada transição de episódio. A lista “Estabilizados recentemente” mostra até cinco circuitos cujo episódio atual está estabilizado e cuja transição ocorreu no período. “Reabertos após estabilidade” conta os inícios de episódio posteriores a um episódio reincidente já estabilizado. A carteira Links Prioritários abre em Ativos e permite consultar Estabilizados ou Todos; uma ocorrência isolada aparece em Todos, nunca como reincidência estabilizada.

O detalhe do Link e o Resumo da OQ mostram estado operacional, episódio atual, sequência, início e dias desde a última ocorrência. A sequência operacional R1–R5+ é apresentada separada do nível manual R1–R5 da Qualidade. As timelines identificam o número do episódio e mantêm as ocorrências anteriores.

R1–R5 é uma **classificação manual** de complexidade técnica, criticidade, impacto e necessidade de escalonamento. Não representa quantidade de reincidências: uma OQ pode receber R4 ou R5 desde a primeira análise se o caso exigir. Nenhum algoritmo altera o nível. Os critérios definitivos e o playbook por nível permanecem em definição operacional.

### Granularidade temporal

O período gerencial e a granularidade são controles independentes. `group_by=auto` escolhe Dia para janelas de até 31 dias, Semana até 120 dias e Mês acima disso. A seleção manual permite Dia/Semana em 7 dias; Dia/Semana/Mês em 30, 60 e 90 dias; Semana/Mês em 180 dias; e somente Mês em 365 dias. Este mês e mês anterior usam a quantidade real de dias do intervalo para determinar as opções. A combinação é preservada na query string.

Dia, Semana e Mês agregam os eventos reais processados pelo service do Dashboard; não distribuem nem estimam valores no frontend. Semanas começam na segunda-feira e são cortadas nas datas inicial/final selecionadas. O tooltip mostra o intervalo completo. A granularidade só altera os buckets do gráfico: a análise central continua agrupando ocorrências por ConfigItem e mantendo o limite operacional de 30 dias entre ocorrências válidas.

Efetividade mostra Sucesso, Reincidiu e Total avaliado para ciclos encerrados no período; sua taxa é `SUCESSO / (SUCESSO + REINCIDIU)`. Ciclos em andamento ou cancelados ficam fora. Concentração exibe até cinco grupos; os percentuais usam todas as reincidências do recorte filtrado como denominador. O nome do projeto canônico ou do provedor leva aos Links Prioritários com os filtros correspondentes que a fila aceita.

### Guia “Como funciona”

O mesmo drawer explicativo está disponível no cabeçalho de Visão geral, Links Prioritários, Ordens, Monitoramento, Contatos e Histórico. Ele resume o caminho ocorrência válida → reincidência → Link Prioritário → OQ → triagem → tratamento/TQ → RREIP de 30 dias → sucesso ou retorno ao tratamento. O guia explica que estar na carteira de Links Prioritários não cria OQ, que detalhes e conversas oficiais do TQ permanecem no OTRS e que uma OQ pode iniciar em R4/R5 conforme complexidade mesmo sem histórico formal naquele circuito.

R1–R5 são níveis operacionais definidos manualmente pela Qualidade, não contagem de ocorrências. Os exemplos de R1 (tratativa simples) até R5 (caso crítico/crônico ou de alta complexidade) são orientação conceitual; os critérios definitivos ainda serão formalizados. Problemas de VPN/IPsec, túnel, roteamento ou arquitetura podem justificar R4/R5 desde a primeira OQ. “Mesma causa” também depende de confirmação humana. Em casos financeiros, desbloqueio temporário não equivale à regularização definitiva; a Qualidade registra confirmação e evidência antes do monitoramento.

O guia identifica OTRS como fonte dos tickets/TQs, CMDB/Base como fonte dos dados mestre do circuito e ARION OPS como fonte dos registros locais da Qualidade. A consulta segue o Data Scope do usuário; criação/alteração de OQs e gestão de contatos exigem os respectivos acessos funcionais.

### Fontes dos indicadores do Dashboard

| Indicador | Fonte |
|---|---|
| Novas reincidências | OTRS + CMDB |
| Links reincidentes ativos | OTRS + CMDB |
| Links estabilizados | OTRS + CMDB |
| OQs abertas e concluídas | ARION OPS |
| Em monitoramento | ARION OPS |
| RREIP com sucesso | ARION OPS |
| Reincidiram pós-tratamento | ARION OPS |
| Concentração por projeto ou ISP | OTRS + CMDB |
| Links mais reincidentes e estabilizados recentemente | OTRS + CMDB |
| Efetividade da Qualidade | ARION OPS, somente ciclos RREIP concluídos |

Os indicadores de período respeitam o recorte gerencial. O estoque de links reincidentes ativos é atual; os filtros de circuito reduzem esse estoque, mas o período não. O gráfico operacional apresenta novas reincidências e estabilizações em barras, e OQs concluídas e ciclos que reincidiram após tratamento em linhas. O Dashboard mantém seis cards: novas reincidências, links ativos, links estabilizados, OQs abertas, ciclos em monitoramento e RREIP com sucesso; criação/conclusão de OQ e reincidência pós-tratamento aparecem como contexto secundário nos cards correspondentes.

O build de produção empacota Apache ECharts localmente, sem CDN. O CSS do módulo reutiliza os tokens de tema do ARION OPS. O JavaScript inicializa apenas elementos com dimensões, reutiliza instâncias, atualiza os gráficos no evento `arion:themechange` e redimensiona as instâncias.

## Estado atual

Fundação de models, permissões, escopo, serviços e rotas. Dashboard / Visão geral mantém os indicadores e gráficos já implementados. A etapa 2 concluiu as filas de Links prioritários e Ordens de Qualidade:

- Links prioritários é uma carteira derivada dos espelhos locais de tickets OTRS relacionados a links CMDB. A visão padrão mostra episódios ativos; o filtro de estado permite Ativos, Estabilizados e Todos. Estabilizados saem da carteira padrão, mas permanecem acessíveis no filtro e no histórico. A janela gerencial padrão é 60 dias, com opções de 7, 30, 60, 90, 180 e 365 dias; ela não altera a duração do RREIP nem os episódios.
- A carteira usa a validade compartilhada do mirror: estados operacionais reconhecidos, sem responsabilidade improcedente, cancelamento, duplicidade declarada no código de encerramento ou exclusão manual em `QualityOccurrence` dentro do escopo do usuário. Cada ocorrência válida é ordenada por data e agrupada em episódios: intervalo de até 30 dias mantém o episódio; intervalo maior inicia outro. Uma reincidência ativa exige ao menos duas ocorrências no episódio corrente e ocorrência mais recente nos últimos 30 dias. O histórico permanece consultável mesmo após o reset. A janela gerencial (30/60/90/180/365 dias) filtra volume e histórico sem alterar essa regra. A mesma causa só é contada quando revisada pela Qualidade; não é inferida do motivo do ticket. Repetições observadas no espelho podem gerar um candidato, mas não criam OQ automaticamente.
- Cada circuito da carteira abre um detalhe pelo ConfigItem estável. A página reutiliza o mesmo recorte, a validação e os totais da lista, preserva o período, pagina tickets espelhados e mostra válidos e desconsiderados com classificações manuais de `QualityOccurrence`, quando disponíveis. A sequência da janela é apenas uma posição de investigação e não altera o nível formal da OQ. O detalhe é somente leitura para OTRS/CMDB.
- O detalhe mostra a OQ ativa relacionada e permite abrir sua página atual. Quando não há OQ ativa, usuários com `qualidade.manage_quality_orders` podem iniciar a criação pelo modal existente, mantendo as validações de escopo e duplicidade.
- A fila de OQs apresenta busca, filtros, contadores, prazos e atividade. Links e ordens são filtrados pelo Data Scope no backend antes de agregações e paginação.
- O ordenamento “Maior atenção” usa critérios explícitos: reincidência ativa, prioridade de negócio, reincidências no episódio atual, recência e prioridade formal da OQ. A configuração prioriza Claro/Banco do Brasil, Claro/Correios, Vivo/Bradesco e Vivo/Arcos Dourados; FUST continua visível com prioridade relativa menor. Essa classificação só ordena/destaca e nunca altera contagens. A carteira rápida separa Claro prioritárias, Vivo prioritárias e outras.
- A criação manual de OQ está disponível para `qualidade.manage_quality_orders`, exige link CMDB existente, inicia em `NOVA`, registra evento local e auditoria central e bloqueia outra OQ ativa para o mesmo link dentro da transação. A criação não grava no OTRS.
- Responsável e prioridade podem ser alterados na fila por usuários com a permissão de gerenciamento; cada alteração gera `QualityAuditEvent` e `AuditService`.
- As filas possuem tabela desktop, cards em telas menores, filtros server-side e páginas de 25, 50 ou 100 itens. Tema claro/escuro reutiliza os tokens atuais do ARION OPS.

A migration `0002_qualityorder_manage_permission` registra a permissão de gestão das OQs. As migrations `0003` e `0004` adicionam o gerenciamento de contatos, os campos de evidência financeira, o checklist simples e o rótulo final de ciclo RREIP; `0005` acrescenta observações ao contato operacional.

## Limites atuais

- Tickets TQ são somente associados a partir do espelho local; nenhuma criação, alteração ou encerramento remoto no OTRS é feito pelo módulo.
- O ciclo RREIP é iniciado e seu resultado é confirmado manualmente no ARION OPS. O ticket de reincidência deve ser válido no mirror, pertencer ao mesmo circuito e ter ocorrido após o início e até o fim previsto do ciclo. O sucesso só pode ser confirmado após 30 dias e quando não há ocorrência válida no intervalo. Reincidência retorna a OQ para Tratamento; sucesso conclui a OQ. Ambas as transições ficam na auditoria local e central. Nenhuma escrita é feita no OTRS.
- Não há envio ou integração de mensagens pelo Teams ou Outlook.
- O checklist da OQ é gerencial e manual; não existe motor de playbooks automáticos por nível R1–R5. O nível é uma classificação humana de complexidade/criticidade, independente da quantidade de reincidências; critérios definitivos ainda serão formalizados pela operação.
- Para motivo financeiro, o início do RREIP exige confirmação da Qualidade e registro de evidência; esse controle não substitui um workflow financeiro integrado.

Não há IA operacional nem alteração de registros oficiais da Base/CMDB ou do OTRS.

## Regra de apresentação de dados mestre

A interface prioriza os valores operacionais legíveis da Base/CMDB. A Designação do circuito é a identificação principal; o número interno do registro CMDB não substitui a Designação e, quando necessário, aparece apenas em contexto técnico secundário. O ConfigItem continua disponível para relacionamento e rastreabilidade.

Services separam `provider_id` (identificador técnico) e `provider_name` (nome operacional resolvido pela fonte oficial). Templates, filtros, histórico, busca e modal de criação usam `provider_name`; identificadores numéricos não são rótulos de ISP/Provedor. A busca pode aceitar IDs técnicos, mas os resultados e a seleção mostram Designação e nome do provedor. Quando o espelho não permite resolver um provedor, a interface mostra "Não classificado", nunca o ID numérico.

A regra vale para Links Prioritários, detalhe do link, fila e detalhe da OQ, criação de OQ, dashboard, filtros e histórico. O espelho local consulta a Base/CMDB oficial; o ARION OPS não grava alterações nessa fonte.

## Fase 3 - detalhe da OQ, Resumo e Ocorrências

- A página de detalhe apresenta o caso da OQ, status e prioridade, Designação, contexto de cliente/projeto/provedor, responsável e prazo. O indicador de fase mostra somente a situação atual; não representa percentual nem obriga um fluxo irreversível.
- A aba Resumo mostra contagens de tickets válidos, reincidências (válidos após a ocorrência inicial), mesma causa confirmada, TQs e ciclos RREIP; situação atual e anterior; dados do circuito; gestão; resumo do TQ atual; e ciclo RREIP ativo, quando existentes. Motivo e causa raiz dependem de campos estruturados e não são inferidos do título ou da descrição do ticket.
- A aba Ocorrências consulta tickets locais espelhados do OTRS relacionados ao link. A validade compartilha a política de estado operacional, responsabilidade e exclusões da carteira de Links Prioritários. A timeline separa os eventos válidos e mantém os não contabilizados em seção recolhida; oferece filtros por todas, válidas, desconsideradas e pendentes, ordenação e paginação.
- Usuários com `qualidade.manage_quality_orders` podem revisar uma ocorrência no ARION OPS, classificar inicial/reincidência/desconsiderada, registrar motivo e causa raiz e confirmar mesma causa. Desconsiderar exige motivo. A gravação atualiza `QualityOccurrence`, emite evento local `OCCURRENCE_REVIEWED` e auditoria central; métricas são recalculadas na próxima leitura. A revisão não altera nem cria ticket no OTRS.
- A edição de status, nível, prioridade, responsável, prazo e observação interna exige a permissão de gestão e registra evento por campo na auditoria.
- Tratativas, ciclos RREIP, Histórico e Contatos seguem os fluxos descritos na Fase 4 abaixo. Playbooks automáticos, integração de escrita no OTRS e relatórios específicos permanecem fora do escopo.

## Fase 4 — Tratativas, Monitoramento RREIP, Histórico e Contatos

- A aba Tratativas mostra o TQ atual, TQs anteriores, tipo, fila, estado, datas e resultado usando os snapshots/mirror locais. Ela oferece acesso ao ticket no OTRS sem copiar artigos ou conversas. Quando o mirror não possui o nome oficial do responsável, a interface informa que ele não está disponível em vez de exibir um ID interno.
- O checklist gerencial usa `QualityAction`, pode ser atualizado por usuários com `qualidade.manage_quality_orders` e registra cada adição/conclusão/reabertura na auditoria local e central.
- O monitoramento global apresenta ciclos ativos, terminando em até três dias, reincididos e concluídos, além da fila paginada e filtros por texto, cliente, projeto, provedor, UF, responsável, resultado e datas. A consulta aplica Data Scope antes de contadores, filtros e paginação. A tela da OQ mostra progresso em dias e ciclos anteriores.
- O início do ciclo é uma decisão manual. A conclusão com sucesso só pode ser registrada depois do fim previsto. Uma reincidência exige ticket válido do espelho local, relacionado ao mesmo ConfigItem e ocorrido durante o ciclo. Cancelamento e demais resultados ficam auditados; o resultado do ciclo não muda o status da OQ automaticamente.
- Para OQ com motivo financeiro, o ciclo exige confirmação explícita da Qualidade e evidência textual de pagamento ou regularização definitiva. Desbloqueio não equivale a regularização.
- O Histórico da OQ organiza eventos da ordem, ocorrências, tratativas e monitoramento. O Histórico global consulta eventos centrais de OQs e contatos visíveis ao usuário, oferece busca/filtros e paginação server-side e resolve Designação, projeto canônico e `provider_name`. Em MySQL, `action` e os IDs de objeto comparados à auditoria central são normalizados para `utf8mb4_unicode_ci` no ponto da consulta; não há conversão ampla do schema.
- Contatos e escalonamentos possuem busca, filtros compactos, paginação, tabela adaptável, drawer de detalhes com observações, criação, edição, ativação/desativação e recomendação contextual no detalhe da OQ. As alterações exigem `qualidade.manage_quality_contacts` e geram auditoria. No escopo CUSTOM, cliente/operadora e projeto canônico devem pertencer à interseção permitida no backend.
- Filtros e páginas globais aplicam RBAC e Data Scope no servidor. Contatos e eventos fora do escopo não são retornados por consulta ou escrita direta.

## Regra central de reincidência

`build_quality_recurrence_analysis()` resolve tickets válidos por ConfigItem, ordena a data de criação operacional e calcula os episódios completos, inclusive estado ativo, estabilizado, novo episódio e ocorrência isolada. Cada ocorrência recebe episódio, posição, sequência operacional e indicação de reabertura após estabilidade. A revisão local não é necessária para inclusão; ela pode enriquecer motivo/causa ou excluir manualmente o ticket. Causas diferentes continuam sendo reincidência operacional se o intervalo entre ocorrências válidas for de até 30 dias. “Mesma causa” permanece uma classificação separada, dependente de revisão humana.

## Fluxo operacional atual

Ticket válido no espelho OTRS → episódio calculado por ConfigItem → Link Prioritário → abertura manual da OQ → revisão local das ocorrências → associação manual de TQ existente no espelho → início manual do RREIP de 30 dias → registro manual do resultado e conclusão ou retorno ao tratamento. O fluxo conserva os tickets e links oficiais como somente leitura; revisão, checklist, vínculo de TQ e ciclos são registros locais auditados. A Qualidade ainda decide manualmente a abertura da OQ, causa raiz, associação do TQ e resultado do ciclo. Não há criação ou alteração remota de tickets.

Para avaliação visual local, `seed_qualidade_demo` cria OQs em datas diferentes, ocorrências revisadas, referências locais a tickets existentes, ciclos, checklist, contatos e auditoria marcados como demonstração. Os níveis de demonstração são escolhidos manualmente e não são derivados da contagem de reincidências. O comando exige `DEBUG=True`, ambiente não produtivo e MySQL em host local; `seed_qualidade_demo --clear` remove somente OQs/contatos com o marcador e eventos com `metadata.seed = "quality-demo-v1"`. O seed não cria nem altera registros dos espelhos CMDB/OTRS.

## Canonicalização de projetos

Qualidade reutiliza `integracoes.services.project_names`, já compartilhado com o Cockpit. Aliases reconhecidos, como `PROJETO BB`, `BB` e `BANCO DO BRASIL`, são apresentados como “Banco do Brasil” e reunidos nos filtros/agregações. O valor original pode permanecer nos snapshots; o service resolve o nome canônico para interface e filtros. A ordem de atenção lê a configuração compartilhada de prioridade de negócio e não filtra carteiras por padrão.

## Automação futura de abertura de TQ — PLANEJADO (NÃO IMPLEMENTADO)

Uma fase futura poderá preparar uma solicitação de TQ a partir de número da OQ, nível de reincidência, Designação, cliente/projeto, ISP, ocorrências relacionadas e resumo da solicitação. O módulo atual não envia e-mail, não monta integração de escrita e não cria/edita tickets remotos automaticamente. Toda confirmação nesta fase ocorre manualmente e localmente.
