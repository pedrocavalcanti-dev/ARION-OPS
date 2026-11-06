# Chamados

O módulo Chamados é a mesa operacional diária do NOC Atacado. A fila e os detalhes consultam somente os mirrors locais OTRS/CMDB. O OTRS continua como fonte oficial; o acompanhamento ARION é local e não altera dados remotos.

## Fila e fonte

A fila inicial contém tickets atuais da fila `NOC - ATACADO` nos estados OTRS `OPEN`, `PENDING` e `SCHEDULED`. `RESOLVED` (incluindo o alias local legado `CLOSED`), `CANCELED` e `UNKNOWN` não são ativos. Cada ticket aparece uma vez, mesmo quando relacionado a vários ConfigItems; os links associados ficam disponíveis no detalhe.

O número comercial (`ticket.tn`, exibido na fila) é diferente do ID interno (`ticket.id`). O mirror guarda o ID interno em `OTRSTicket.external_id`; o sincronizador já o preenchia nos modos FAST e FULL. O link oficial é composto no backend como `AgentTicketZoom;TicketID=<external_id>`, usando `OTRS_WEB_BASE_URL`. A fila oficial usa `OTRS_NOC_ATACADO_QUEUE_URL`. Ambos abrem em outra aba e dependem da autenticação do próprio OTRS. Sem ID interno válido, o link do ticket é omitido.

Estado e prioridade oficiais vêm de `OTRSTicket` e aparecem no drawer, em uma seção própria. A tabela operacional não tem coluna permanente de Status OTRS: ela destaca o Status ARION e o responsável. Cliente, Projeto, links, Provider/ISP e localização vêm dos mirrors CMDB relacionados. Cliente e Provider são dimensões distintas.

| Dado da tela | Fonte | Persistência local / interpretação |
|---|---|---|
| ID técnico, número comercial, título, estado/prioridade oficiais e datas | OTRS | `OTRSTicket`; `external_id` mantém `ticket.id` e `ticket_number` mantém `ticket.tn` |
| Ticket–ConfigItem | Relação OTRS `DependsOn`/`Valid` | `OTRSTicketConfigItem`; mantém `ConfigItemID` mesmo se ainda não houver Link CMDB correspondente |
| Link, Provider/ISP e localização | CMDB | `CMDBLinkMirror`, `CMDBOperatorMirror` e FK local; Cliente/Projeto podem incluir interpretação derivada do `Name` |
| Status/priority ARION, responsável e próxima ação | ARION | `TicketTracking`; registro é criado na primeira alteração, nota ou atribuição |
| Nota e histórico | ARION | `TicketTrackingNote` e `TicketTrackingEvent`; append-only |
| Filtros salvos | ARION | `TicketQueueView`, privado ao usuário dono |

Leia `ticket.id` como chave técnica e `ticket.tn` como número de apresentação. Não use `Number` para correlacionar Ticket–CI; o caminho é `ConfigItemID` ↔ `source_config_item_id`.

## Busca, filtros e recortes

Busca, filtros, recortes e paginação são atendidos no backend e permanecem na URL. A busca inclui número, título, ID interno OTRS, designação, CI, link, cliente, projeto, provider, cidade e UF. Os filtros principais são Status ARION, Cliente, Projeto, Provider/ISP, UF e Responsável. Status OTRS, prioridade, motivo, aging, reincidência e ordenação ficam em **Mais filtros**; links antigos com `otrs_status` continuam aceitos. O padrão inicial é 50 linhas; seleções explícitas de 25, 50 ou 100 são preservadas ao paginar e em filtros salvos.

As abas aplicam recortes cumulativos com os demais filtros e seus contadores são calculados no backend para o recorte inteiro, não apenas para a página visível:

- **Todos:** os três estados OTRS ativos.
- **Em atendimento:** `OPEN`.
- **Pendentes:** `PENDING`.
- **Agendados:** `SCHEDULED`.
- **Meus:** acompanhamento ARION atribuído ao usuário atual.
- **Sem responsável:** acompanhamento ARION sem responsável.

Selects e checkboxes aplicam ao mudar; a busca usa debounce e continua sendo uma navegação GET server-side. **Limpar** remove os filtros e abre Todos, por maior aging e 50 por página. A URL envia `ignore_default=1` para não reaplicar a fila padrão pessoal nesse acesso.

Categorias de encerramento reutilizam `ClosingReasonCanonicalizer`.

## Acompanhamento ARION

| Campo local | Valores e comportamento |
|---|---|
| `internal_priority` | Vazio (Sem prioridade interna), `LOW` (Baixa), `NORMAL` (Normal), `HIGH` (Alta), `CRITICAL` (Crítica) |
| `next_action_at` | Data/hora opcional ARION; não é prazo nem SLA OTRS |
| `revision` | Revisão otimista; update com revisão antiga é recusado para evitar sobrescrever alteração concorrente |
| `TicketTrackingEvent.event_type` | `FIELD_CHANGED` ou `NOTE_ADDED`; eventos guardam metadados de alteração sem duplicar o corpo da nota na auditoria |
| `TicketTrackingNote.body` | Texto interno ARION até 2.000 caracteres, criado como nota append-only |

`TicketTracking` usa o ID OTRS estável e não duplica dados oficiais. Sem registro, o acompanhamento é apresentado virtualmente como **Em Aberto**; o registro só é criado na primeira mudança ou nota. O catálogo ARION é: Em Aberto (`OPEN`), Resolvido (`RESOLVED`), Em atendimento pela ISP (`ISP_IN_PROGRESS`), Pendente financeira (`FINANCIAL_PENDING`), AG. Visita Técnica (`TECH_VISIT`), Em análise pelo Pós-Vendas (`POST_SALES_ANALYSIS`), Troca de titularidade (`OWNERSHIP_CHANGE`), Cancelamento do Link (`LINK_CANCELLATION`), Aguardando validação (`WAITING_VALIDATION`), CGNAT, Túneis e Massiva. As chaves são estáveis e os rótulos não controlam regras de negócio. `RESOLVED` significa resolução interna ARION e não fecha nem altera o chamado no OTRS; todos os status ARION são independentes do estado oficial.

A migration converte `Novo` em `OPEN`, `Aguardando operadora` em `ISP_IN_PROGRESS`, `Finalizado no ARION` em `RESOLVED` e mantém `Aguardando validação`. Outros estados legados sem equivalência direta passam a `OPEN`; o rótulo anterior fica preservado em evento de histórico. Eventos existentes não são reescritos.

A fila permite alterar rapidamente o **Status ARION**, assumir um chamado sem responsável e desassumir o próprio chamado. O nome do responsável permanece visível; chamados atribuídos a outra pessoa não ganham ação rápida para troca. O drawer mantém edição completa de responsável, prioridade ARION e próxima ação. Todas essas gravações usam o mesmo `TicketTrackingService`, POST com CSRF, permissão de edição, revalidação do Data Scope e revisão otimista. Mudanças geram eventos append-only e auditoria; desassumir registra **Responsável removido**. Notas ARION são append-only, limitadas a 2.000 caracteres, validam revisão otimista e registram o evento sem guardar o corpo no log de auditoria.

O drawer organiza o detalhe, o link direto para AgentTicketZoom, título, Status oficial OTRS, Prioridade OTRS, criação, aging e motivo de fechamento quando disponível; depois identidade e links CMDB; então Status ARION, responsável, prioridade e próxima ação; notas e histórico ARION. A tabela usa `OTRSTicket.external_id` para compor `TicketID` na URL oficial. Os links para a Base usam o ConfigItem exato; a pesquisa no Cockpit usa o número comercial do chamado. Artigos, mensagens, anexos e e-mails do OTRS não são consultados nem apresentados.

## Filas pessoais

Usuários com `manage_operational_tickets` podem salvar, aplicar, renomear, definir como padrão e excluir filas pessoais. `TicketQueueView` guarda usuário, nome, filtros JSON validado, indicador de padrão e datas. Cada usuário só consulta e altera suas próprias filas. O servidor aceita uma lista de filtros autorizados (`tab`, `q`, `otrs_status`, `arion_status`, `client`, `project`, `provider`, `uf`, `priority`, `assignee`, `reason`, `aging`, `recurrent`, `sort` e `per_page`); valores são validados e chaves extras enviadas ao salvar são rejeitadas.

Sem parâmetros na URL, a fila padrão do usuário é aplicada. Parâmetros explícitos prevalecem. Limpar filtros usa `ignore_default=1`, inclusive ao atualizar aquela URL, e não reaplica imediatamente a preferência padrão.

## Escopo e acesso

`LinkQueryService.base(user)` aplica Data Scope antes das consultas da fila, KPIs, contadores, busca, opções de filtros e relações Ticket-ConfigItem. Detalhe e endpoints de atualização repetem a validação de escopo e estado ativo; tickets fora do escopo retornam como não encontrados. Permissões Django separam visualização (`view_operational_tickets`) e edição (`manage_operational_tickets`). Assumir e desassumir usam POST, CSRF, revisão otimista, serviço central e auditoria. O espelho OTRS e o mirror CMDB são somente leitura. Filas salvas são isoladas pelo usuário e, quando aplicadas, continuam sujeitas ao escopo atual.

## Aging e recorrência

Aging é `agora - source_created_at`. O marcador operacional `>24h` não representa SLA contratual. Reincidência exige ao menos dois tickets distintos em estados operacionais válidos (`OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED`) relacionados ao link nos últimos 30 dias, usando a regra compartilhada pela analytics operacional. `CLOSED` é somente um alias local legado de `RESOLVED` durante compatibilidade; não é uma categoria adicional. Cancelados e desconhecidos não contam.

## Sincronização e limites

O cabeçalho mostra a última sincronização OTRS bem-sucedida disponível em `IntegrationSyncState`. Um erro mais recente é informado sem interromper a leitura do mirror. Chamados não consulta OTRS nem CMDB durante requests. Não altera status, fila, owner, responsável, prioridade, Dynamic Fields, artigos, notas ou fechamento no sistema oficial. A fila depende da cadência do worker atual; não há histórico de estados OTRS nem fila histórica nesta versão.

## Canonicalização de motivos

Motivos de encerramento usam `ClosingReasonCanonicalizer` compartilhado. Por exemplo, `FalhaMassiva`, `Falha Massiva` e `Defeito Massivo` são apresentados como Falha Massiva; o valor bruto é preservado no mirror.

Veja também [Data Scope](usuarios-grupos-e-escopos.md), [OTRS](..\06-integracoes-e-dados\otrs.md), [CMDB](..\06-integracoes-e-dados\cmdb.md), [Worker](..\02-arquitetura\worker-e-sincronizacao.md), [Banco de Dados](..\02-arquitetura\banco-de-dados.md) e [Ranking de Provedores](rankings.md).

## Tickets ativos e Links afetados

Os cartões da Fila são calculados no backend para o conjunto inteiro, sem depender da página visível. Tickets ativos são distintos e pertencem à fila atual `NOC - ATACADO`, presentes no snapshot e nos estados `OPEN`, `PENDING` ou `SCHEDULED`. Links afetados são Links CMDB locais distintos, presentes e no Data Scope, relacionados a pelo menos um ticket do conjunto contado. Vários tickets no mesmo Link contam um Link; um ticket ligado a dois Links válidos conta dois Links.

Busca, Cliente, Projeto, Provedor, UF, Status ARION, Responsável, Prioridade, Motivo, aging e reincidência reduzem o conjunto de Tickets dos cartões. Quando um filtro de dimensão seleciona um Ticket por um Link correspondente, `Links afetados` conta todos os Links distintos e autorizados associados aos Tickets selecionados, ainda que outro Link do mesmo Ticket tenha outra dimensão. Os filtros `otrs_status` e as abas `OPEN`/`PENDING`/`SCHEDULED`/`Meus`/`Sem responsável` são aplicados à tabela depois dos cartões e não alteram seus totais. A paginação também não altera os totais. Assim, sem filtros adicionais os dois cartões correspondem aos snapshots atuais do Dashboard; com recorte de fila, refletem o conjunto filtrado descrito acima.

No escopo global, tickets sem ConfigItem e ConfigItems sem Link local continuam sendo Tickets, mas não contam como Links afetados. Em `CUSTOM`, tickets sem relação com Link autorizado não podem ser atribuídos ao escopo e ficam fora dos resultados. Filtros por Cliente/Projeto/Provedor/UF contam cada Ticket selecionado uma vez, sem multiplicação por joins; o contador de Links considera as relações locais distintas do Ticket selecionado dentro do escopo autorizado.

