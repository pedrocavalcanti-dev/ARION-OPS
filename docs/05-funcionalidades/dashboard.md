# Dashboard

Identidade visual do produto/interface: **ARION OPS**. A identificação da operação nas páginas é **NOC ATACADO**.

O Dashboard combina os Links atuais do mirror CMDB e os tickets presentes na fila exata `NOC - ATACADO`. As consultas usam o banco local; a página não chama ServiceUp nem o MySQL do OTRS. Links e tickets respeitam o Data Scope aplicado no backend.

## Período e snapshot

- `Tickets no período`, `Links reincidentes` e a evolução mensal usam `source_created_at`.
- Reincidência é um Link associado a pelo menos dois Tickets distintos no intervalo; a relação N:N não duplica o total de Tickets.
- `Chamados abertos agora` e `Situação atual dos chamados` são snapshots do espelho atual, independentes do período. Em atendimento, Pendentes e Agendados compõem o total aberto e são exibidos separadamente; Resolvidos, Encerrados, Cancelados e não classificados ficam fora do backlog. Os totais Resolvidos/Encerrados refletem `business_status` atual; `CLOSED` continua como estado oficial do OTRS, não como substituto da data de resolução operacional.
- A situacao mostra Em atendimento, Pendentes, Agendados, Resolvidos, Encerrados, Cancelados e, somente quando houver, nao classificados.
- O gráfico compara tickets criados no mês com a quantidade de Links que tiveram dois ou mais tickets distintos naquele mês. Não representa histórico de backlog.

## Rankings da base CMDB

- **Top 10 Estados com mais Links** agrega os Links presentes (`CMDBLinkMirror.is_present_in_source=True`) por UF válida, em ordem decrescente de quantidade.
- **Top 10 Provedores com mais Links** agrega os Links presentes por nome da entidade CMDB resolvida (`CMDBOperatorMirror.name`), contando Links distintos. Provedores não resolvidos não ocupam posições.
- Ambos os rankings usam o snapshot atual da CMDB, aplicam o mesmo Data Scope e não mudam com o período selecionado para Tickets.
- A classificação histórica VIVO / CLARO / ALGAR / OUTROS foi removida da apresentação do Dashboard. O campo e a migration permanecem para análise futura.

## Outros indicadores

- Total de Links: itens presentes no mirror CMDB.
- Estados atendidos: UFs válidas distintas na base visível.
- Freshness e atualização de dados continuam vinculadas aos mirrors e integrações existentes.
## Implementação, rotas e leitura

A rota `/dashboard/` é servida por views/analytics do app `painel` e template `painel/dashboard.html`; estilos ficam em `static/css/painel/` e fontes JavaScript em `frontend_src/js/painel/`. O serviço de analytics combina querysets de Links atuais e Tickets presentes, aplicando escopo no backend antes dos indicadores.

O seletor de período altera somente as métricas por criação de ticket e reincidência da coorte; totais atuais de Links/UF/Providers e backlog continuam sendo fotografia do mirror. Provider é o nome resolvido em `CMDBOperatorMirror`, não uma classificação manual VIVO/CLARO/ALGAR. Ticket total é distinto no agregado geral; uma relação Ticket-CI múltipla pode associar o ticket a mais de um Link em análises por Link.

A página não calcula SLA cumprido, não reconstrói backlog histórico e não acessa fonte remota em request. Freshness é mantida pela integração e monitorada pela Central. Consulte [OTRS](..\06-integracoes-e-dados\otrs.md), [CMDB](..\06-integracoes-e-dados\cmdb.md) e [Data Scope](usuarios-grupos-e-escopos.md).

## Tickets ativos e Links afetados

Os indicadores atuais usam a fila `NOC - ATACADO`, tickets presentes no snapshot e Data Scope aplicado primeiro. `Chamados abertos agora` conta tickets distintos em `OPEN`, `PENDING` ou `SCHEDULED`, independentemente de ConfigItem. `Links afetados` conta Links CMDB locais distintos, presentes e autorizados, associados a pelo menos um desses tickets por uma relação Ticket–ConfigItem presente. Um ticket sem ConfigItem ou com ConfigItem sem Link local continua no total de tickets, mas não cria Link afetado. Um ticket relacionado a mais de um Link pode afetar cada Link; vários tickets no mesmo Link afetam apenas um.

O período do Dashboard altera métricas históricas por criação, não esses snapshots atuais. Sem filtros de fila, os indicadores atuais de tickets ativos e Links afetados correspondem aos cartões da Fila operacional no mesmo escopo.
