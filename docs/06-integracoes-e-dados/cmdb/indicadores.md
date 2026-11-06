# 13 · Indicadores

## Universo básico de Links

CMDB-Q001 é a base compartilhada LinkQueryService.base: CMDBLinkMirror presente na fonte e, quando informado usuário, filtrado por Data Scope; carrega Provider por select_related. Esta consulta é a origem do universo CMDB para Dashboard, Base, Cockpit, Rankings e Mapa. Os dados são locais, portanto cada indicador reflete o último snapshot válido, não uma chamada em tempo real.

## Regras rastreadas

| Indicador | Universo e fórmula implementados | Observações |
|---|---|---|
| Total de Links | Contagem de Links presentes no mirror após Data Scope e filtros de tela. | Distinto de ticket e de relação Ticket–CI. |
| Links por Provider | Agrupa Links com CMDBOperatorMirror resolvido; Links sem Provider continuam no total geral e não entram em uma linha nomeada. | ISP bruto não resolve automaticamente duplicidades. |
| Links exibidos como “Links na CMDB” no Ranking | Contagem de inventário presente por Provider; o valor chamado links_active é preenchido com links_total e não é filtrado pelo estado de implantação. | deployment_active_links é métrica separada. Não interpretar esse label como disponibilidade técnica. |
| Links em operação no Ranking | deployment_state casefold igual a operacional ou operational. | Regra exata do código; não há normalização abrangente de enumeração ou acentuação nessa condição. |
| Concentração Top 10 | Soma do inventário dos dez Providers com mais Links / total de Links do universo × 100. | Links sem Provider ficam no denominador geral, mas não nas dez linhas nomeadas. |
| Tickets por 100 Links | Tickets OTRS distintos relacionados ao Provider no período / inventário de Links do Provider × 100. | Tickets são deduplicados no Provider; período e filtros seguem o Ranking. |
| Links afetados | Número de IDs distintos de Link com uma ou mais relações válidas para tickets do recorte. | Uma relação por CI não multiplica Link. |
| Mapa por UF | Agregação de Links por state após Data Scope, com filtros de cliente/Provider/projeto. | Cidade e UF são atributos textuais; tickets são conjuntos de IDs relacionados. |
| Cobertura de cliente no Mapa | Links cujo grupo de cliente não é “Cliente não identificado” / Links totais da UF × 100. | Deriva da identidade classificada; não valida cadastro na fonte. |
| Links prioritários/recorrência | Base de Links + relações/tickets OTRS válidos + ordens/ocorrências de Qualidade. | Prioridade empresarial ordena a fila; não altera a fórmula de recorrência. |

## Janela temporal e status

As métricas de ticket usam o snapshot OTRS local, filas operacionais configuradas e categorias normalizadas de status. Alguns indicadores usam data de abertura; métricas resolvidas usam resolved_at com fallback para terminal_state_at. Períodos e filtros variam por módulo. Não se deve tratar o inventário atual da CMDB como fotografia histórica: o mirror contém o valor atual, não versões passadas de cada campo.

No Mapa, os modos base/projects/calls/repeat/motives usam conjuntos distintos de relações e janelas. Tickets por UF são deduplicados por ticket_id; Links afetados contam links com pelo menos um ticket e reincidência no modo repeat exige pelo menos dois tickets distintos no recorte. Em motives, relações terminais precisam de status terminal, motivo de fechamento e timestamp operacional. Esses resultados juntam OTRS ao Link local e não são colunas trazidas pela CMDB.

No Dashboard/Cockpit, os dados de inventário vêm de LinkQueryService.base; tickets abertos seguem TicketQueryService e categorias OTRS. O helper count_affected_links agrega ConfigItems distintos. Nos Relatórios, cmdb_isp_identity resolve nome/ID no mirror e retorna candidatos em conflito sem escolher um ISP arbitrariamente. O enriquecimento padrão usa relações e espelhos locais; remote_lookup é opt-in.

## Escopo e deduplicação

Data Scope é aplicado antes de agregações e catálogos: operador contratante e projeto funcionam em interseção; projeto legado só entra quando business_project está vazio. Superuser continua global. Em consulta Ticket–Link são usados vínculos presentes, e contagens de links afetados usam IDs distintos. A definição específica das métricas OTRS segue [OTRS · Indicadores](otrs-indicadores.md).

## Catálogo de consultas locais

Os identificadores estáveis CMDB-Q001 a CMDB-Q008 e as transformações CMDB-T001 a T008 estão detalhados em [Consultas internas e transformações](cmdb-consumidores.md) e [Rastreabilidade](cmdb-rastreabilidade.md). Ver [Qualidade](cmdb-qualidade.md) para as regras de atenção.
