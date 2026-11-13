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

OQ pode se relacionar ao `CMDBLinkMirror` e mantém snapshots de designação, ConfigItem, cliente, projeto, ISP e UF. Referências de tickets usam o mirror `OTRSTicket` quando disponível e preservam identificadores e rótulos locais. Consultas de tela são locais. Envios Brevo são POSTs explícitos autorizados; não há escrita direta no OTRS/CMDB.

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

Para a mecânica de envio e correlação pelo QID, consulte [Qualidade e TQ no OTRS](otrs-qualidade-tq.md). As regras compartilhadas de estado e indicadores estão na [referência OTRS](otrs.md).

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

- Links prioritários é uma carteira derivada dos espelhos locais de tickets OTRS relacionados a links CMDB. A visão padrão mostra episódios ativos e links com OQ aberta, mesmo quando antiga; o filtro de estado permite Ativos, Estabilizados e Todos. Estabilizados saem da carteira padrão, mas permanecem acessíveis no filtro e no histórico. A janela gerencial padrão é 30 dias, com opções de 7, 15, 30, 60, 90 dias e Personalizado. Dashboard, lista e detalhe usam os mesmos limites inclusivos por dia local. Links antigos para janelas anteriores continuam reconhecidos. A janela não altera a duração do RREIP nem os episódios.
- A carteira usa a validade compartilhada do mirror: estados operacionais reconhecidos, sem responsabilidade improcedente, cancelamento, duplicidade declarada no código de encerramento ou exclusão manual em `QualityOccurrence` dentro do escopo do usuário. Cada ocorrência válida é ordenada por data e agrupada em episódios: intervalo de até 30 dias mantém o episódio; intervalo maior inicia outro. Uma reincidência ativa exige ao menos duas ocorrências no episódio corrente e ocorrência mais recente nos últimos 30 dias. O histórico permanece consultável mesmo após o reset. A janela gerencial filtra volumes, reincidências na janela, confirmação de mesma causa e histórico sem alterar essa regra. O ordenamento operacional de maior atenção continua considerando o episódio atual; a ordenação por reincidências na janela usa o período escolhido. A mesma causa só é contada quando revisada pela Qualidade; não é inferida do motivo do ticket. Repetições observadas no espelho podem gerar um candidato, mas não criam OQ automaticamente.
- Cada circuito da carteira abre um detalhe pelo ConfigItem estável. A página reutiliza o mesmo recorte, a validação e os totais da lista, preserva o período, pagina tickets espelhados e mostra válidos e desconsiderados com classificações manuais de `QualityOccurrence`, quando disponíveis. A sequência da janela é apenas uma posição de investigação e não altera o nível formal da OQ. O detalhe é somente leitura para OTRS/CMDB.
- O detalhe mostra a OQ ativa relacionada e permite abrir sua página atual. Quando não há OQ ativa, usuários com `qualidade.manage_quality_orders` podem iniciar a criação pelo modal existente, mantendo as validações de escopo e duplicidade.
- A fila de OQs apresenta busca, filtros, contadores, prazos e atividade. Links e ordens são filtrados pelo Data Scope no backend antes de agregações e paginação.
- O ordenamento “Maior atenção” usa critérios explícitos: reincidência ativa, prioridade de negócio, reincidências no episódio atual, recência e prioridade formal da OQ. A configuração prioriza Claro/Banco do Brasil, Claro/Correios, Vivo/Bradesco e Vivo/Arcos Dourados; FUST continua visível com prioridade relativa menor. Essa classificação só ordena/destaca e nunca altera contagens. A carteira rápida separa Claro prioritárias, Vivo prioritárias e outras.
- Os filtros principais seguem operadora contratante, projeto canônico, macrorregião IBGE, UF e período. Projetos dependem dos vínculos reais da operadora na CMDB/Qualidade; UFs são normalizadas por sigla/nome oficial e limitadas à região. Alterações incompatíveis limpam projeto/UF com orientação. ISP, carteira, prioridade, nível, motivo e situação continuam nos controles avançados existentes. Filtros compatíveis seguem na navegação, paginação e links analíticos; Limpar volta a 30 dias e seleções gerais. Chips representam o recorte aplicado, e estados vazios distinguem ausência de candidatos de falha de integração.
- As carteiras estratégicas usam **cliente contratante**, independentemente do ISP. A contratante vazia não é inferida pelo provedor. A carteira é um filtro adicional: uma combinação incompatível é explicada no backend e impedida na seleção. Prioridades relativas e regra FUST permanecem. BB/PROJETO BB/Banco do Brasil e BDN/Bradesco reutilizam a normalização compartilhada; não foi criada equivalência McDonald's/Arcos Dourados sem confirmação cadastral.
- OQs abertas e ciclos RREIP em andamento são estoque operacional, sem corte pela idade. A janela afeta resultados gerenciais, ciclos encerrados pela data efetiva de encerramento e Histórico de auditoria; não encerra nem oculta pendências antigas. Eventos automáticos sem usuário aparecem como Sistema. O detalhe da OQ e sua timeline operacional permanecem integrais.
- A criação manual de OQ está disponível para `qualidade.manage_quality_orders`, exige link CMDB existente, inicia em `NOVA`, registra evento local e auditoria central e bloqueia outra OQ ativa para o mesmo link dentro da transação. A criação não grava no OTRS.
- Responsável e prioridade podem ser alterados na fila por usuários com a permissão de gerenciamento; cada alteração gera `QualityAuditEvent` e `AuditService`.
- As filas possuem tabela desktop, cards em telas menores, filtros server-side e páginas de 25, 50 ou 100 itens. Tema claro/escuro reutiliza os tokens atuais do ARION OPS.

A migration `0002_qualityorder_manage_permission` registra a permissão de gestão das OQs. As migrations `0003` e `0004` adicionam o gerenciamento de contatos, os campos de evidência financeira, o checklist simples e o rótulo final de ciclo RREIP; `0005` acrescenta observações ao contato operacional.

## Limites atuais

- TQs podem ser preparadas e enviadas por Brevo SMTP/API mediante configuração e autorização. O envio não comprova criação de ticket: a correlação exata por QID e fila confirma o vínculo no espelho local. Nenhuma criação, alteração ou encerramento pela API do OTRS é executado pelo módulo. Veja [Qualidade e TQ no OTRS](../06-integracoes-e-dados/otrs/qualidade-tq.md).
- O ciclo RREIP é iniciado e avaliado manualmente. Reincidência exige ticket válido do mesmo circuito, ocorrido após início e até fim previsto, descrição e confirmação humana de mesma causa raiz. Sucesso exige fim previsto atingido, análise final, validação de estabilidade e revisão textual das ocorrências; o código não infere sucesso automaticamente nem impõe um veto genérico a toda ocorrência de outra causa. Reincidência/cancelamento retornam OQ em Monitoramento para Tratamento. Sucesso mantém avaliação final pendente; concluir explicitamente a TQ fecha a OQ somente sem outras tratativas ativas. As decisões ficam auditadas, sem escrita OTRS.
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
- O início exige TQ em tratamento com ticket OTRS vinculado, confirmação de análise das ações e resumo. Só pode haver um ciclo ativo por OQ. Sucesso depende do fim previsto e da avaliação humana; reincidência exige ticket válido do mesmo ConfigItem no intervalo e confirmação de mesma causa. Cancelamento/reincidência fazem retorno local ao Tratamento; sucesso aguarda conclusão explícita da TQ. Nenhum resultado movimenta o ticket remoto.
- Para OQ com motivo financeiro, o ciclo exige confirmação explícita da Qualidade e evidência textual de pagamento ou regularização definitiva. Desbloqueio não equivale a regularização.
- O Histórico da OQ organiza eventos da ordem, ocorrências, tratativas e monitoramento. O Histórico global consulta eventos centrais de OQs e contatos visíveis ao usuário, oferece busca/filtros e paginação server-side e resolve Designação, projeto canônico e `provider_name`. Em MySQL, `action` e os IDs de objeto comparados à auditoria central são normalizados para `utf8mb4_unicode_ci` no ponto da consulta; não há conversão ampla do schema.
- Contatos e escalonamentos possuem busca, filtros compactos, paginação, tabela adaptável, drawer de detalhes com observações, criação, edição, ativação/desativação e recomendação contextual no detalhe da OQ. As alterações exigem `qualidade.manage_quality_contacts` e geram auditoria. No escopo CUSTOM, cliente/operadora e projeto canônico devem pertencer à interseção permitida no backend.
- Filtros e páginas globais aplicam RBAC e Data Scope no servidor. Contatos e eventos fora do escopo não são retornados por consulta ou escrita direta.

## Regra central de reincidência

`build_quality_recurrence_analysis()` resolve tickets válidos por ConfigItem, ordena a data de criação operacional e calcula os episódios completos, inclusive estado ativo, estabilizado, novo episódio e ocorrência isolada. Cada ocorrência recebe episódio, posição, sequência operacional e indicação de reabertura após estabilidade. A revisão local não é necessária para inclusão; ela pode enriquecer motivo/causa ou excluir manualmente o ticket. Causas diferentes continuam sendo reincidência operacional se o intervalo entre ocorrências válidas for de até 30 dias. “Mesma causa” permanece uma classificação separada, dependente de revisão humana.

## Fluxo operacional atual

Ticket válido → episódio por ConfigItem → Link Prioritário → abertura humana da OQ → revisão/triagem → TQ local → prévia e envio Brevo autorizado ou associação manual elegível → confirmação do ticket OTRS por QID/fila → análise das ações → RREIP de 30 dias → resultado humano → conclusão explícita da TQ/OQ ou retorno ao tratamento. O envio aceito não confirma ticket criado. Não existe escrita direta na API/banco OTRS; o recebimento do e-mail pelo OTRS é evento externo. R1–R5 e causa raiz continuam decisões humanas.

Para avaliação visual local, `seed_qualidade_demo` cria OQs em datas diferentes, ocorrências revisadas, referências locais a tickets existentes, ciclos, checklist, contatos e auditoria marcados como demonstração. Os níveis de demonstração são escolhidos manualmente e não são derivados da contagem de reincidências. O comando exige `DEBUG=True`, ambiente não produtivo e MySQL em host local; `seed_qualidade_demo --clear` remove somente OQs/contatos com o marcador e eventos com `metadata.seed = "quality-demo-v1"`. O seed não cria nem altera registros dos espelhos CMDB/OTRS.

## Canonicalização de projetos

Qualidade reutiliza `integracoes.services.project_names`, já compartilhado com o Cockpit. Aliases reconhecidos, como `PROJETO BB`, `BB` e `BANCO DO BRASIL`, são apresentados como “Banco do Brasil” e reunidos nos filtros/agregações. O valor original pode permanecer nos snapshots; o service resolve o nome canônico para interface e filtros. A ordem de atenção lê a configuração compartilhada de prioridade de negócio e não filtra carteiras por padrão.

## Solicitacao de tratativa por e-mail

A TQ local recebe codigo legivel `TQ-AAAA-NNNNNN` e ID de correlacao `ARION-QID-<32 hex>` imutavel. O assunto segue `SOLICITACAO DE TRATATIVA DE QUALIDADE | {TQ_CODE} | {DESIGNACAO_LINK}`; o QID aparece somente no corpo HTML/texto. A mensagem reune codigos da TQ/OQ, designacao, cliente/projeto, operadora/provedor, reincidencias resumidas, motivo/tratativa e chamado de suporte relacionado quando houver.

A pre-visualizacao funciona sem credencial Brevo e nao envia mensagem. O metodo pode ser API REST ou SMTP Relay no host fixo `smtp-relay.brevo.com:587`, com STARTTLS obrigatorio e validacao de certificado. A API Key REST e a chave SMTP ficam criptografadas separadamente por DPAPI. O teste SMTP realiza EHLO/STARTTLS/AUTH/QUIT sem enviar e-mail; autenticacao aceita nao confirma entrega.

O envio exige integracao habilitada, credencial do metodo protegida, remetente/destinatario configurados, endereco da fila Qualidade validado no OTRS, autorizacao administrativa e confirmacao individual por POST. Antes do primeiro envio controlado deve ser usada uma caixa de testes autorizada. A criacao da TQ nunca dispara e-mail automaticamente. Tentativas simultaneas sao bloqueadas; cada tentativa registra metodo e UUID local. SMTP nao inventa `messageId`. Falhas confirmadas permitem nova tentativa explicita, enquanto resultado incerto fica bloqueado para reconciliacao.

A aceitação Brevo não confirma criação do ticket. A correlação implementada usa QID completo no corpo do artigo da fila de Qualidade, com validação e checkpoint próprios. O ARION não escreve no OTRS.
## Envio Brevo e correlação automática

A TQ possui prévia HTML/texto e confirmação individual por POST. O método SMTP Relay é o padrão quando não há preferência salva; API REST permanece disponível. To, Cc e Reply-To são campos distintos e editáveis. Cc é enviado como destinatário de cópia por SMTP e pela API REST; o roteamento pode criar outro ticket e ainda precisa de confirmação operacional. O envio continua bloqueado sem autorização, credencial recuperável, remetente/destinatário válidos e confirmação administrativa do endereço de entrada da fila.

O worker existente agenda leitura read-only de artigos por lote/cursor, sobreposição, lease OTRS e backoff. Cadência, lote e filas aceitam `ARION_QUALITY_OTRS_CORRELATION_INTERVAL_SECONDS`, `ARION_QUALITY_OTRS_CORRELATION_BATCH_SIZE` e `ARION_QUALITY_OTRS_QUEUE_NAMES`. QID completo e fila exata determinam o vínculo; corpo fica em memória, cursor e falha sanitizada são locais. Não se presume readiness produtiva pela existência do código: validar schema/fila, conta autorizada e checkpoint no ambiente conectado, sem afirmar configuração efetiva não consultada. Não há escrita OTRS.

## Fluxo Operacional: OQ → TQ → OTRS → RREIP

O ciclo de vida da tratativa de qualidade segue etapas rigorosamente sequenciadas e auditadas:

1. **OQ (Ordem de Qualidade):** Identificada a reincidência de falhas em um circuito monitorado (episódio de 30 dias na CMDB/OTRS), a Qualidade formaliza a OQ. Cada OQ mantém histórico unificado, ocorrências espelhadas e checklist gerencial.
2. **TQ (Tratativa de Qualidade):** Dentro da aba Tratativas, o operador gera uma TQ local (`TQ-AAAA-NNNNNN`), que recebe um identificador de correlação imutável (`ARION-QID-...`). O chamado de suporte ativo pode ser associado.
3. **Envio via Brevo SMTP:** A solicitação é pré-visualizada (HTML/texto) e enviada por SMTP com STARTTLS para a caixa de entrada da fila de Qualidade do OTRS. Nenhum envio ocorre automaticamente sem confirmação expressa do operador.
4. **OTRS (Correlação e Acompanhamento):** Ao receber o e-mail, o OTRS gera o ticket correspondente. O worker do ARION OPS correlaciona o ticket através do QID contido no corpo do artigo inicial, vinculando o número oficial (`quality_ticket_number`) e a URL do OTRS no ARION OPS.
5. **Tratativa e Checklist:** O operador acompanha a resolução no OTRS, atualiza o resumo gerencial (resumo, situação, próxima ação) via formulário retrátil e avança os itens do Checklist da Qualidade.
6. **Início do Monitoramento RREIP:** Concluída a ação técnica no OTRS, o operador valida que o circuito está operacional, insere o resumo das ações verificadas no OTRS (`otrs_actions_summary`) e inicia o ciclo RREIP de 30 dias.
7. **Desfecho do Ciclo:** 
   - **Sucesso:** Decorridos os 30 dias sem nova ocorrência, confirma-se a estabilidade e a TQ é concluída formalmente.
   - **Reincidência:** Se houver nova falha válida durante a janela, registra-se a reincidência e a OQ retorna para tratamento com histórico preservado.

## Interface da Aba Tratativas (Versão 1.3.1)

A aba **Tratativas** da Ordem de Qualidade foi refinada para máxima clareza e alinhamento:
- **Alinhamento dos Cards:** O card da **Tratativa Atual** e o card do **Checklist da Qualidade** iniciam rigorosamente na mesma altura do grid, sem cabeçalhos repetidos e com espaçamentos consistentes.
- **Ações e Botões Padronizados:** Botões "Abrir no OTRS", "Atualizar resumo", "Salvar atualização", "Iniciar monitoramento RREIP", "Pré-visualizar solicitação" e "Enviar solicitação" utilizam altura mínima de 36px, ícones SVG dedicados, estados visuais nítidos (hover, focus-visible e disabled) e estilo corporativo.
- **Formulário Retrátil:** O formulário de atualização do resumo gerencial permanece recolhido inicialmente em elemento `<details>`, abrindo sob clique de "Atualizar resumo" com campos bem distribuídos e ação explícita de gravação.

## Procedimento de Banco de Dados, Migrations e Atualização do Servidor

### SQLite em DEV vs MySQL em Produção
- O ambiente de desenvolvimento (DEV) utiliza exclusivamente **SQLite** (`arion_geo/db.sqlite3`). A migração de DEV para provedores externos foi descartada em favor da estabilidade e rapidez local.
- O ambiente de produção utiliza **MySQL 8.4**.

### Histórico de Migrations 0014 e 0015 (Qualidade)
- **`0014_qualitymonitoringcycle_active_order_key_and_more.py`**: Adiciona índices e colunas estruturais base.
- **`0015_qualitymonitoringcycle_otrs_actions_summary_and_more.py`**: Adiciona `otrs_actions_summary` no `QualityMonitoringCycle`, bem como reconcilia `active_order_key`, `support_ticket_id`, `support_ticket_number` e `correlation_id`.
- Ambas foram aplicadas no SQLite DEV e são 100% compatíveis com a sintaxe DDL do MySQL 8.4.

### Procedimento de Atualização em Produção
1. Realizar backup prévio do banco MySQL de produção (`mysqldump`).
2. Atualizar o código no servidor a partir do commit da versão 1.3.1.
3. Executar verificação prévia com `python arion_geo/gerenciar.py showmigrations`.
4. Aplicar as migrações com `python arion_geo/gerenciar.py migrate qualidade`.
5. Validar integridade e ausência de pendências com `python arion_geo/gerenciar.py check`.
6. Reiniciar os serviços Web e Worker.

## Arquivamento logico de OQ

O detalhe da OQ oferece arquivamento administrativo somente para superuser ou para quem possui `qualidade.delete_qualityorder`, permissao disponivel no editor de Grupos e acessos. A confirmacao exige motivo e repeticao do codigo da OQ via POST. O registro recebe data, usuario e motivo de arquivamento e permanece consultavel por quem possui a permissao de exclusao; ocorrencias, TQs, ciclos, checklist e auditoria nao sao apagados.

OQs arquivadas saem das consultas operacionais, indicadores e rankings. O arquivamento e bloqueado enquanto houver envio pendente/incerto, TQ em acompanhamento, ticket de Qualidade sem encerramento confirmado ou ciclo RREIP ativo. A operacao e transacional e registrada no historico local e na auditoria central.


## Homologacao final de TQ e roteamento

- Cada OQ aceita somente uma TQ ativa. A restricao usa transacao e chave unica local. Depois de conclusao formal, um novo ciclo exige uma reincidencia valida; reincidencia durante RREIP encerra a TQ como `Reincidiu` e preserva o historico. OQ e TQ mantem sequencias globais independentes e cada TQ conserva seu ARION-QID.
- O RREIP move a mesma TQ para monitoramento. Sucesso conclui a TQ; reincidencia conclui como `Reincidiu` e permite novo ciclo a partir da ocorrencia vinculada. Cancelamento do RREIP retorna a TQ para tratativa.
- No e-mail, cliente, projeto, operadora contratante (`contracting_client`) e ISP/provedor resolvido pela relacao CMDB ficam separados. Responsavel da OQ e responsavel da TQ aparecem em campos distintos. O assunto inclui OQ, TQ, projeto, operadora e designacao, sem QID; o QID completo fica no corpo.
- O envio TQ exige credencial do metodo protegido, teste de autenticacao SMTP bem-sucedido (quando SMTP esta ativo), integracao habilitada, destinatarios validos, roteamento aprovado, autorizacao administrativa, OTRS somente leitura configurado, leitura incremental bem-sucedida de cada fila de Qualidade e confirmacao individual. A aceitacao SMTP nao confirma entrega nem ticket.
- **Testar roteamento de e-mail** e uma acao administrativa separada. Configure primeiro uma caixa controlada no campo de destinatario de homologacao. Cada envio exige a caixa de confirmacao da pagina, recebe `ARION-ROUTE-<32 hex>`, nao inclui Cc/Reply-To nem QID de TQ, e nao altera a validacao da fila ou qualquer TQ. Para apontar a homologacao ao destinatario operacional, marque antes a autorizacao administrativa especifica e confirme cada envio individualmente. O resultado aceito pelo relay precisa ser verificado manualmente na caixa e no OTRS.
- A primeira verificacao operacional deve ser To isolado. Cc permanece fora do teste para evitar ticket duplicado; valide Cc em procedimento separado somente depois de confirmar o comportamento do To e aprovar explicitamente a mensagem de homologacao.
- A correlacao le artigos incrementalmente, procura o QID completo no corpo simples/HTML, exige fila exata e registra checkpoints. Nao correlaciona por assunto, circuito ou sequencia. Em 04/11/2026 a integracao OTRS do banco DEV nao esta configurada; portanto, o schema real e a correlacao ponta a ponta continuam sem homologacao.

- OTRS permanece a fonte oficial da tratativa tecnica; o ARION OPS guarda apenas resumo gerencial, situacao, proxima acao e atualizacoes curtas em auditoria.
- Para iniciar o RREIP, o operador autorizado confirma que revisou as acoes no ticket OTRS, informa um resumo curto e declara o circuito apto. Nao ha cadastro tecnico paralelo. O sucesso do RREIP exige avaliacao de estabilidade e, depois, uma acao explicita para concluir a TQ.

## Checklist antes da implantação de Qualidade

O código está publicado no release 1.4.0; o checklist é para a implantação/homologação conectada no servidor, cujo estado não foi inspecionado nesta auditoria. A gestão de contatos exige `manage_quality_contacts`, ainda ausente do seletor operacional: ver [matriz de permissões](usuarios-grupos-e-escopos.md). Atribuição por grupo e restrição de contatos devem ser revistas antes de delegar a operação.

- [ ] Comparar migrations do codigo e `showmigrations` no MySQL de destino; revisar cada migration pendente antes de autorizar qualquer aplicacao.
- [ ] Inventariar arquivos rastreados e nao rastreados, incluindo ECharts 6.1.0 e avisos de licenca; conferir `collectstatic` sem introduzir requisito Node/npm no DEV.
- [ ] Confirmar a identidade Windows do web/worker e acesso DPAPI no servidor sem copiar credenciais entre usuarios.
- [ ] Confirmar credencial SMTP nova do ARION OPS e teste de autenticacao sem envio.
- [ ] Validar roteamento primeiro em caixa controlada; depois autorizar teste individual do To operacional; testar Cc separadamente para detectar duplicidade.
- [ ] Confirmar leitura somente leitura do schema de artigos, fila exata, checkpoint e correlacao pelo QID do corpo; nao executar FULL para essa validacao.
- [ ] Revisar eventuais alterações locais de `deploy/windows/production_check.py`, código versionado, sem confundir checker com arquivo privado de configuração; revisar TLS legado só para o host aprovado.
- [ ] Conferir integridade OQ/TQ, worker, URLs OTRS e staticfiles; validar ponta a ponta autorizada antes da implantação, distinguindo-a da publicação de código já realizada.

## Homologação dos filtros 1.4.1

Revisão em 13/11/2026 em cópia isolada do SQLite DEV, sem uso de produção ou MySQL DEV (configuração indisponível). No recorte Vivo/Bradesco havia 582 circuitos CMDB, 23 candidatos ativos, 80 estabilizados e 421 no estado Todos. Antes da correção, todos recebiam atenção 99 por causa do ISP e a carteira Vivo eliminava os 23 ativos. Depois, os mesmos 23 aparecem com atenção 30, tanto na janela de 30 como na de 60 dias. Sudeste + Vivo/Bradesco + carteira Vivo retorna 8 candidatos. Claro/Banco do Brasil/Norte e Algar/Sul não possuem candidatos ativos nesse snapshot; os vazios são legítimos. São evidências datadas, não quantidades fixas do produto.

Validação: suíte completa `aplicativos.qualidade` com **93 testes aprovados** (filtros, janela de 30 dias/detalhe, Dashboard, filas, reincidência, RBAC/Data Scope, OQs/ciclos antigos, histórico por contratante/auditoria sem usuário e comportamento do popover avançado). Django `check` e verificação de migrations aprovados; nenhuma migration nova. Build com 36 bundles e Service Worker, além de smoke autenticado HTTP `200` nas rotas principais e da Qualidade em cópia isolada (`var/quality-filter-dev.sqlite3`). Inspeção visual prévia em Chromium cobriu 1920×1080, 1440×900, 1366×768, 1024×768 e 390×844, claro/escuro, sem overflow horizontal ou erro JavaScript, com evidências locais e diagnóstico agregado em `var/quality-filters/`, fora do Git. Não houve envio de TQ/e-mail, escrita OTRS/CMDB, coleta climática, deploy ou criação de tag.
