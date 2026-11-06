# Indicadores que usam OTRS

## Universo e proteção de dados

O serviço compartilhado TicketQueryService define o universo padrão pela fila NOC - ATACADO e por registros presentes no mirror. Chamadas que agregam outras filas usam explicitamente esse escopo. Para usuários com Data Scope customizado, o código primeiro limita Links CMDB permitidos e só inclui tickets com relação presente a esses Links; a regra é backend e precede filtros, agregação, paginação e exportação.

Counts de ticket devem usar identidade do ticket, não número de relações Ticket–CI. Várias relações no mesmo ticket podem multiplicar linhas se agregadas sem distinct. Para links afetados, a chave de contagem é Link CMDB distinto. Confira a cobertura do mirror antes de comparar um KPI com o OTRS.

## Semântica das principais métricas

| Indicador | Base e fórmula implementada | Data/status e duplicidade |
|---|---|---|
| Tickets criados | Contagem distinta de OTRSTicket no universo válido | source_created_at dentro do período |
| Resolvidos/encerrados | Contagem distinta de tickets com status RESOLVED (CLOSED legado tolerado) | Data operacional: resolved_at, fallback terminal_state_at |
| Cancelados | Tickets com business_status CANCELED | Data terminal/canceled_at conforme o serviço |
| Backlog | Snapshot presente com status OPEN ou PENDING | Não inclui SCHEDULED; não é histórico por período |
| Agendados | Status SCHEDULED | Em aberto de negócio, mas fora do backlog |
| Motivos de fechamento | closing_code_raw não vazio, filtrado no catálogo de aliases explícitos | Rótulo canônico agrega raws mapeados; raw é preservado |
| Links impactados | Contagem de cmdb_link_id distintos nas relações presentes | Só Link resolvido no mirror CMDB e permitido pelo Data Scope |
| Reincidência por Link | Tickets externos distintos por ConfigItem/Link e janela de criação | Relação duplicada não deve somar o mesmo ticket duas vezes |
| Tempo até resolução | Média de data operacional menos source_created_at | Só duração não negativa e estado atual resolvido |
| Backlog envelhecido | Tickets OPEN/PENDING agrupados por idade desde source_created_at | Faixas são calculadas localmente no momento da consulta |

## Cockpit e Dashboard

AnalyticsQueryService recebe métrica, agrupamento, escopo, período e filtros. O escopo e Data Scope precedem filtros e contagens. Resolved usa data operacional; métricas de ticket criado usam source_created_at. backlog é marcado como snapshot. O fluxo operacional estima estoque anterior e soma entradas/fechamentos quando a cobertura histórica permite; caso faltem datas ou fechamento anterior à criação, expõe metadados de cobertura/invalidade em vez de presumir uma série completa.

O Dashboard agrega tickets criados, estado atual, motivos e distribuição por cliente/projeto a partir do mirror. Cliente/projeto e provedor vêm do CMDBLinkMirror associado, não de colunas de ticket OTRS. Por isso uma linha sem ConfigItem/Link pode não participar de distribuição geográfica/comercial.

## Rankings

RankingService agrega tickets relacionados aos Links/operadoras. Usa ocorrência por source_created_at, backlog atual, status normalizado, responsabilidade e duração operacional de resolvidos. Métricas por 100 Links dividem por inventário local de Links elegíveis, não por quantidade de tickets. Score, percentis e bandas são transformações locais, não campos nem KPIs da fonte OTRS. Valores sem data necessária são excluídos da duração e podem ser contados como qualidade incompleta.

## Mapa

MapAnalyticsService combina mirror OTRS–ConfigItem com atributos geográficos e comerciais do CMDB. Modos de chamadas usam criação e status atual; motivos exigem motivo raw e terminal com data operacional disponível; recorrência agrupa tickets por Link/ConfigItem. Chamados sem Link local podem aparecer em modos de ticket quando os filtros de contexto permitem, mas não recebem geografia por inferência.

## Recorrência de Qualidade

O serviço de recorrência usa tickets válidos relacionados ao ConfigItem e source_created_at, remove IDs repetidos no agrupamento e constrói episódios de janela de 30 dias com período de estabilização. QualityOccurrence acrescenta revisão/justificativa; não é obrigatório que exista uma revisão para o ticket entrar na base, e exclusões aplicam a política compartilhada de validade.

## Cancelamentos e SLA

As filas/etapas de cancelamento são classificadas por nomes de fila e estados definidos em aplicativos/integracoes/otrs_queues.py. Não são inferidas por título do ticket. A tabela OTRS sla fornece ID/nome; ticket também carrega valores escalation numéricos. O código consultado não compara esses campos a metas de SLA para declarar cumprimento ou violação.

## Limitações de leitura

Os indicadores operam sobre o último snapshot local, limitado às filas monitoradas. Uma resolução e reabertura entre duas leituras pode não aparecer como evento histórico no mirror. Ticket sem data operacional pode entrar em contagem atual e não entrar em KPI de tempo/resolução por período. Para campos e caminhos consulte [rastreabilidade](otrs-rastreabilidade.md); consumidores detalhados estão em [módulos](otrs-consumidores.md).
