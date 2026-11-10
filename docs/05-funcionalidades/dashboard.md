# Dashboard

Identidade visual do produto/interface: **ARION OPS**. A identificação da operação nas páginas é **NOC ATACADO**.

O Dashboard combina os Links atuais do mirror CMDB e os tickets presentes na fila exata `NOC - ATACADO`. As consultas usam o banco local; a página não chama ServiceUp nem o MySQL do OTRS. Links e tickets respeitam o Data Scope aplicado no backend.

## Período e snapshot

- `Tickets no período`, `Links reincidentes` e a evolução mensal usam `source_created_at`.
- Reincidência é um Link associado a pelo menos dois Tickets distintos no intervalo; a relação N:N não duplica o total de Tickets.
- `Chamados abertos agora` e `Situação atual dos chamados` são snapshots do espelho atual, independentes do período. Em atendimento, Pendentes e Agendados compõem o total aberto e são exibidos separadamente. O **aging da fila operacional** usa esse mesmo universo da página Chamados (`OPEN`, `PENDING` e `SCHEDULED`): faixas, total, percentual acima de 72h e chamado mais antigo incluem os agendados. Resolvidos, Cancelados e não classificados ficam de fora. O backlog histórico do Cockpit mantém seu conceito separado de estoque `OPEN`/`PENDING`. Resolvido e Encerrado do OTRS são consolidados em `business_status=RESOLVED`; a data operacional de resolução continua separada da data terminal do OTRS.
- A situação mostra Em atendimento, Pendentes, Agendados, Resolvidos, Cancelados e, somente quando houver, não classificados. Estados brutos Resolvido e Encerrado são consolidados na categoria `RESOLVED`.
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

## Blocos executivos atuais

- **Aging da fila operacional** usa tickets `OPEN`, `PENDING` e `SCHEDULED` presentes na fila NOC, pelo mesmo `TicketQueryService.business_open` de Chamados. A idade usa `source_created_at` e o horário atual; não o instante de atualização do mirror. Faixas: até 24h, 24–48h, 48–72h e acima de 72h. O resumo apresenta volume, percentual acima de 72h e idade do mais antigo; datas ausentes ficam fora das faixas e são informadas separadamente. O filtro histórico não altera esse snapshot. O backlog histórico `OPEN`/`PENDING` do Cockpit é outro conceito.
- **Chamados por projeto** usa tickets distintos do período e relações presentes com Links CMDB visíveis. O nome segue a precedência `business_project`, `legacy_project` e, na falta de ambos, `contracting_client`, com `canonical_project_name` / `display_project_name`. Assim aliases conhecidos como BB, PROJETO BB e BANCO DO BRASIL convergem para o mesmo projeto. A visualização usa colunas verticais no Top 5 e pode exibir também uma linha extra de não classificados para não ocultar esse volume. A participação usa todos os tickets do período como denominador; um ticket ligado a projetos diferentes aparece em cada projeto associado.
- **Principais motivos** agrupa tickets distintos do período pelo `OTRSTicket.closing_code_raw`, usando `ClosingReasonCanonicalizer`, a mesma classificação do Cockpit. Valores desconhecidos mantêm o texto original; campo vazio aparece como não classificado. A lista mostra até cinco motivos e inclui uma linha adicional de não classificados quando ela não estiver entre os cinco primeiros. Motivos classificados levam ao Cockpit com período e filtro de motivo; o link só aparece para quem tem permissão de Cockpit.
- **Distribuição da base por cliente** agrega os Links CMDB atuais já limitados pelo Data Scope. Reutiliza `BusinessIdentityRules` e os tokens de cor do Mapa para Claro (inclui TELMEX), Vivo, Algar, Outras e cliente não identificado; o total no centro e os valores/percentuais da legenda representam o snapshot inteiro, independente do período.
- **Logos de provedores** consultam arquivos existentes em `static/branding/provedores/` por aliases conhecidos (incluindo FIERCE TELECOM, TOWER CONNECTION(WKE), MIDAS CONSULTING LTDA, VERO / AMERICANET, DESKTOP, GIGA+, OP - ALGAR e ELO SOLUÇÕES). Quando não há arquivo, a linha mostra iniciais neutras em um espaço fixo de 32×32. Nomes e quantidades continuam vindo de `CMDBOperatorMirror`; o helper não inventa imagens nem altera a classificação do provedor.

Os novos agregados recebem querysets já limitados pelo Data Scope; os blocos históricos acompanham as datas selecionadas e os snapshots atuais permanecem independentes. Os estilos usam os tokens de tema existentes e as listas se ajustam ao grid responsivo da página.

### Revisão de consultas

A captura de SQL de `DashboardAnalyticsService.build` encontrou 29 consultas na fixture antes desta revisão, incluindo resolução repetida do mesmo Data Scope e uma contagem separada de Links já representados na distribuição agrupada. O serviço agora reutiliza o escopo resolvido no objeto de usuário durante a requisição e deriva o total de Links da própria distribuição. O teste de regressão passou com limite de 20 consultas; ele mede quantidade de queries em SQLite de teste, não latência nem ganho em produção. A abordagem segue a recomendação do Django de perfilar consultas antes de otimizar e reduzir trabalho repetido do ORM ([otimização de acesso ao banco](https://docs.djangoproject.com/en/5.2/topics/db/optimization/), [QuerySet API](https://docs.djangoproject.com/en/5.2/ref/models/querysets/)).

## Tickets ativos e Links afetados

Os indicadores atuais usam a fila `NOC - ATACADO`, tickets presentes no snapshot e Data Scope aplicado primeiro. `Chamados abertos agora` conta tickets distintos em `OPEN`, `PENDING` ou `SCHEDULED`, independentemente de ConfigItem. `Links afetados` conta Links CMDB locais distintos, presentes e autorizados, associados a pelo menos um desses tickets por uma relação Ticket–ConfigItem presente. Um ticket sem ConfigItem ou com ConfigItem sem Link local continua no total de tickets, mas não cria Link afetado. Um ticket relacionado a mais de um Link pode afetar cada Link; vários tickets no mesmo Link afetam apenas um.

O período do Dashboard altera métricas históricas por criação, não esses snapshots atuais. Sem filtros de fila, os indicadores atuais de tickets ativos e Links afetados correspondem aos cartões da Fila operacional no mesmo escopo.
