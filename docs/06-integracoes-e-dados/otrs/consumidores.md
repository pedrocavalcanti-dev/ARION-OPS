# Módulos consumidores do espelho OTRS

O inventário identificou oito apps Django com acesso ao mirror OTRS ou às relações locais. Requests web consultam o banco ARION; a conexão externa é responsabilidade dos comandos/worker e dos fluxos explícitos de Qualidade.

| App | Serviço/entrada | Informação OTRS usada | Complemento e ressalva |
|---|---|---|---|
| Integrações | TicketQueryService, analytics, views de integração | Snapshot, estados, fila, saúde e execuções | Administra status da leitura; não torna dashboard uma consulta remota |
| Chamados | TicketQueueService | TN, título, estado bruto/categoria, prioridade, motivo, criação e relações | Atribuição interna ARION fica em TicketTracking, separada do owner OTRS |
| Painel/Cockpit | AnalyticsQueryService e DashboardAnalyticsService | Métricas, filtros, agrupamentos, evolução e evidências | Link, provedor, cliente, projeto e geografia vêm do mirror CMDB |
| Rankings | RankingService e exportação | Tickets por Link/operadora, status, criação, datas de resolução, motivo e procedência | Score e normalização são calculados localmente |
| Mapa | MapAnalyticsService | Tickets e vínculos por ConfigItem, status, motivo, prioridade e data | Geografia, UF e projetos vêm de CMDB; tracking é dado interno ARION |
| Qualidade | recurrence, ticket_validity, priority_links, treatments | Ocorrências válidas, status, data de criação, motivo, ticket de suporte e vínculo com CI | OQ/TQ, classificação e RREIP são modelos locais |
| Relatórios | lookup e views de tratamentos | Correspondência de número/ID, status, criação, resolução e motivo | Planilha importada não é origem OTRS; vínculo local é opcional |
| Base de Links | views de Links | Existência de tickets abertos e relações Ticket–CI por Link | Total de Link e atributos comerciais vêm da CMDB |

## Como ler as métricas em cada tela

Chamados lista o estado atual do mirror e combina a busca com atributos do Link quando o usuário tem acesso ao Link. O estado de atribuição ARION é separado do responsável numérico OTRS.

Cockpit pode consultar todas as filas espelhadas dependendo do escopo selecionado; o universo de suporte padrão continua NOC - ATACADO. Backlog é fotografia atual OPEN/PENDING. Resolvidos por período usam data operacional, não apenas criação.

Rankings analisa os tickets relacionados a Links elegíveis, calcula ocorrência e tempos localmente e apresenta categorias de score calculadas. Mapa associa dados geográficos do CMDB e pode ter uma parcela sem Link local. Qualidade agrupa tickets por ConfigItem com datas de criação para recorrência. Relatórios faz lookup entre número informado e mirror local quando há correspondência.

## Dados mestres e escopo

Operadora, ISP, projeto, UF e número de Link podem ser dados CMDB. Prioridade, estado e TN vêm do OTRS. Status ARION, classificações de procedência, motivo canônico, métricas e tracking são derivados/local. O Data Scope opera sobre Links permitidos antes da consulta analítica e não deve ser substituído por ocultação de interface.

Consulte [indicadores](otrs-indicadores.md), [espelho](otrs-espelho-local.md), [base CMDB](../cmdb.md) e [rastreabilidade](otrs-rastreabilidade.md).
