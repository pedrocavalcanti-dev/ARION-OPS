# Rankings operacionais · NOC Atacado

O módulo **Rankings** compara a pressão operacional dos provedores/ISPs do CMDB com chamados espelhados do OTRS. A identidade visual do produto/interface é **ARION OPS**; NOC Atacado identifica a operação atendida. As consultas usam somente os mirrors locais e não escrevem na origem.

## Universo, escopo e filtros

- Links são registros atuais `CMDBLinkMirror.is_present_in_source=True`. Provedor é a operadora/ISP vinculada ao link no CMDB; Cliente e Projeto usam `contracting_client` e `business_project` canônicos.
- O Data Scope de Operadora/Projeto é aplicado por `LinkQueryService.base(user)` antes de filtros, relações, dimensões e cálculos. O drawer refaz a consulta sob o mesmo usuário e escopo.
- Chamados pertencem à fila `NOC - ATACADO`, estão presentes na fila e têm status operacional válido: `OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED` ou `CLOSED`. `CANCELED` e `UNKNOWN` não entram nas métricas.
- Chamados do período usam `source_created_at`, no fuso `America/Sao_Paulo`, com início inclusivo e limite superior exclusivo no início do dia seguinte. O KPI nacional distinto segue o mesmo universo semântico do Cockpit.
- Filtros: mês, visão mensal ou consolidado de três meses, período personalizado, Cliente, Projeto, Provedor, UF, prioridade OTRS, motivo normalizado, status, apenas Aging >24h e apenas reincidentes.
- Filtro e Top Motivos usam `ClosingReasonCanonicalizer`, compartilhado com Cockpit e Mapa. Valores raw sem equivalência confirmada permanecem separados.

Um chamado relacionado a vários ConfigItems do mesmo provedor conta uma vez para esse provedor; cada link distinto afetado conta uma vez. Se os links pertencem a provedores diferentes, o chamado conta uma vez em cada provedor. Por isso, a soma dos chamados por provedor pode superar o KPI nacional distinto.

## Exposição da base CMDB atual

- A visão padrão da rota `/rankings/` é **Mais links** e mostra Top 20. As opções Top 10/20/30/50 exibem esse conjunto diretamente; **Todos** usa a paginação atual. O filtro mínimo de links (Todos, 10+, 30+, 50+, 100+) é opcional e começa em Todos.
- Links representam o snapshot atual da CMDB: todos os `CMDBLinkMirror` presentes em `is_present_in_source=True`, sem filtrar por estado de implantação. Cada ConfigItem é contado uma vez; estados operacionais, cancelados, manutenção e estados vazios permanecem no total da base.
- A quantidade por provedor usa a mesma base e o mesmo agrupamento por nome da Dashboard, depois de aplicar Data Scope e filtros. Variações de caixa/acentos no nome resolvido são agrupadas. Links sem operadora ou sem nome ficam no total da CMDB, aparecem como não identificados e não entram nas linhas de provedor.
- A identidade canônica é a FK `CMDBLinkMirror.operator` resolvida na sincronização CMDB por `ConfigItemID` ou correspondência única e exata do nome. Nomes parecidos não são fundidos. `isp_raw` não vira um provedor separado nem recebe fuzzy matching.
- **Links na CMDB** representam o snapshot atual. Não há snapshots históricos de links; por isso, a visão de 12 meses compara chamados da janela selecionada com o inventário de hoje e não afirma quantos links existiam em meses anteriores.
- **Chamados no período** continuam sendo IDs distintos válidos da fila NOC relacionados a links CMDB atuais e são contados pela criação (`source_created_at`). Um ticket pode aparecer uma vez em cada ISP distinto ao qual está relacionado. Assim, chamados podem incluir relações a links que hoje têm status de implantação inativo; a fonte não conserva o status histórico do link na data do ticket.
- **Chamados / 100 links** é `chamados no período ÷ links atuais da CMDB × 100`. Sem links, o valor é nulo e a interface mostra `—`. Volume absoluto, backlog, aging e indicador operacional permanecem métricas separadas.
- **Tempo médio de resolução** usa tickets `RESOLVED`/`CLOSED` cujo término operacional (`resolved_at`, com `terminal_state_at` como fallback) ocorreu dentro do período e cuja duração desde `source_created_at` é não negativa. Tickets abertos não entram. É tempo operacional calculado; não equivale a SLA contratual.
- **Top 10 da exposição** é `soma dos links dos 10 maiores provedores identificados ÷ total de links atuais da CMDB no recorte × 100`. Links não identificados permanecem no denominador e não são redistribuídos entre provedores.
- **Chamados** seguem o período selecionado; **backlog** e **aging** seguem o snapshot operacional atual de tickets. As quatro abas preservam suas regras e ordenações. Mais links é uma visão de exposição, não de qualidade.
- A fórmula atual do indicador não mudou. **Indicador operacional** é uma leitura auxiliar sobre os dados disponíveis; não é uma nota definitiva de provedores. Os critérios finais serão definidos com a operação.
- No snapshot local validado em 02/11/2026, a base CMDB tinha 3.446 links. A Dashboard e o Ranking usam esse mesmo total, incluindo estados de implantação diferentes. O valor varia após novas sincronizações.
- ANOP e ETIT não têm definição operacional validada e não são calculados. No código e nos documentos, aparecem somente como itens pendentes, sem fórmula. A busca por tokens completos não encontrou valores nos campos armazenados dos mirrors CMDB/OTRS; não há linhas Claro importadas no banco atual. O OTRS remoto não foi consultado; a sincronização local persiste somente campos dinâmicos mapeados, então não existe cobertura dos demais valores dinâmicos remotos.
- A Central de Relatórios contém TMP, EV, Inter e Indisp definidos para conciliação financeira do contrato Claro com o ARION: EV legado é TMP/30, Inter tem fórmula financeira própria e Indisp usa o perfil SLA da competência. Esses indicadores avaliam o cálculo/penalidade Claro aplicado à operação e dependem de valor mensal, perfil contratual, datas e pausas. Não são regras gerais por provedor; nenhum deles foi copiado para Rankings. O tempo médio desta tela é apenas uma duração observada de ticket e não equivale ao TMP efetivo do relatório. Consulte [Relatórios](relatorios.md#conciliacao-financeira-e-evidencias-locais).

## Métricas operacionais

- **Links CMDB:** links atuais do provedor dentro do Data Scope e dos filtros comerciais/geográficos.
- **Chamados:** chamados distintos válidos criados no período e relacionados aos links do provedor.
- **Chamados por link:** chamados válidos divididos pelos links CMDB atuais. A tela não usa a antiga taxa por 100 links.
- **Links afetados:** links distintos com pelo menos um chamado válido no período.
- **Reincidência:** um link reincide quando possui pelo menos dois chamados válidos distintos no período. A taxa é `links reincidentes / links afetados`; sem links afetados, é zero.
- **Backlog atual:** chamados distintos em `OPEN`, `PENDING` ou `SCHEDULED`.
- **ARION >24h:** chamados abertos há mais de 24 horas desde `source_created_at` e chamados `RESOLVED`/`CLOSED` da coorte criada no período cuja duração operacional supera 24 horas. Para os terminais, calcula `resolved_at - source_created_at`; usa `terminal_state_at` apenas quando `resolved_at` falta. Tickets sem nenhuma das duas datas não recebem duração inventada e ficam fora dos registros avaliáveis. Volume e coorte continuam definidos por `source_created_at`.
- **Taxa de aging:** chamados acima de 24h divididos pelos chamados avaliáveis com timestamps suficientes. Registros sem timestamps necessários ficam fora do denominador.

O limite de 24 horas é operacional do ARION e não representa necessariamente o SLA contratual. O módulo não estima disponibilidade, causa técnica, penalidade nem responsabilidade do provedor.

## Indicador operacional · metodologia auxiliar vigente

O índice varia de **0 a 100**. Zero representa ausência de pressão observada no período. Os cinco componentes arredondados a uma casa decimal somam o total:

| Componente | Peso | Cálculo |
|---|---:|---|
| Impacto no NOC | 30 pontos | 60% volume de chamados e 40% links afetados; cada volume é dividido pelo P95 do conjunto filtrado e limitado a 100% antes da ponderação. |
| Base afetada | 20 pontos | `links afetados / links CMDB`, limitado a 100%. |
| Reincidência | 20 pontos | `links reincidentes / links afetados`, limitado a 100%. |
| Aging ARION >24h | 20 pontos | `chamados >24h / chamados avaliáveis`. |
| Backlog atual | 10 pontos | `backlog / max(chamados válidos, 1)`, limitado a 100%. |

O P95 usa a regra de posto mais próximo sobre os provedores do recorte já filtrado; distribuição vazia ou P95 zero rende zero pontos naquele subcomponente. O score não tem piso estatístico. O P95 é metodologia interna e não aparece por linha na interface. Responsabilidade do fechamento é apenas informativa e não altera o índice.

Faixas: `0` Sem ocorrências; `0,1–19,9` Muito baixo; `20–39,9` Baixo; `40–59,9` Moderado; `60–79,9` Alto; `80–100` Crítico. Provedores sem chamados e sem backlog têm score zero e ficam fora das visões de risco e melhor desempenho.

Provedores com menos de cinco links continuam visíveis e recebem **Amostra pequena**. Eles permanecem elegíveis ao ranking de risco. Para o pódio de melhor desempenho exige-se chamado válido e pelo menos cinco links **ou** três chamados; provedores menores com eventos continuam na tabela.

## Visões e desempates

- **Mais links:** visão padrão. Lista provedores resolvidos por links do snapshot CMDB atual e combina exposição, chamados, Chamados/100 links, backlog, >24h, tempo médio e indicador operacional. Top 20 é o padrão; Top 10/30/50 e Todos estão disponíveis. A ordenação pode mudar sem compor um score novo.
- **Maior risco:** inclui `chamados >= 1 OU backlog > 0`. A ordenação padrão é pelo indicador operacional decrescente; empates usam Impacto no NOC, chamados e links afetados em ordem decrescente, depois nome. É uma leitura operacional auxiliar até a validação final dos critérios.
- **Melhor desempenho:** inclui somente provedores com ao menos um chamado válido. Ordena o índice crescente; empates priorizam maior exposição, primeiro links CMDB e depois chamados avaliados. Zero eventos não são tratados como desempenho de atendimento.
- **Sem ocorrências:** inclui `chamados = 0 E backlog = 0`; exibe até 10 provedores por maior exposição de links e apresenta links, clientes, projetos e UFs.

Os atalhos de ordenação incluem indicador operacional, impacto no NOC, chamados, percentual da base afetada, reincidentes, chamados acima de 24h, backlog e links CMDB. A tendência conserva a diferença contra o mês anterior: `↓ Melhorou` em verde, `↑ Piorou` em coral e `→ Estável` em neutro; a faixa estável é de ±2 pontos.

## Período, consolidação e tendência

A visão mensal usa o mês selecionado (até hoje quando for o mês corrente). As janelas **3, 6 e 12 meses** agregam meses-calendário até o mês selecionado; no mês corrente, o limite final é hoje. Assim, em outubro/2026, 12 meses começa em novembro/2025 e termina em hoje. O consolidado recalcula o índice uma vez sobre os dados brutos, sem somar scores mensais. Período personalizado conserva as datas informadas. O gráfico histórico do drawer recalcula o índice separadamente para cada mês.

Todas as janelas de tickets continuam separadas do estoque: a visão de 12 meses compara tickets criados desde novembro/2025 com os links operacionais atuais. O tempo médio de resolução usa data de término operacional dentro da janela, mesmo quando a criação foi anterior.

A tendência compara o score recalculado do mês selecionado com o mês calendário anterior: diferença acima de 2 pontos indica piora, abaixo de -2 indica melhora; entre esses limites é estável. A análise não altera a fonte nem simula meta ou SLA.

## Consistência de cobertura

O KPI **Chamados válidos** nacional, sem filtros adicionais, corresponde ao Cockpit para a mesma janela de criação e estados operacionais; ele inclui chamados da fila sem vínculo com um link atual. A soma por provedor pode ser maior por causa de chamados associados a mais de um ISP, ou menor quando não há provedor identificável. O Mapa agrega por UF e não inclui chamados relacionados somente a links sem UF normalizável no total estadual; a diferença de cobertura geográfica não muda as regras do Mapa.

## Detalhe e links

O ranking de circuitos apresenta 20 links por padrão e permite abrir a lista completa. O drawer mostra a soma dos cinco componentes, a evolução mensal, principais projetos/clientes/UFs/motivos, links problemáticos e chamados paginados. Clientes, Projetos e Motivos têm contagens e atributos complementares separados visualmente. As ações abrem Base e Cockpit preservando os filtros apropriados e o Data Scope.

Procedentes, Improcedentes e Indeterminados vêm do campo de validação do OTRS. O estudo da origem localizou `RvalidacaoProcedenteImprocedente` (`Validação`, tipo `Dropdown`) em `dynamic_field`; os valores ficam em `dynamic_field_value`. No snapshot consultado em 24/10/2026, 1.848 de 11.973 chamados tinham valor: 1.505 `Procedente` e 343 `Improcedente` (15,43% preenchido). O cruzamento com `CodigoFechamento` encontrou motivos iguais sob as duas classificações; a categoria não prova culpa do provedor, cliente ou área técnica.

O mirror guarda `responsibility_raw` e `responsibility_normalized`: valores reconhecidos, sem distinção de caixa/acentos, tornam-se `PROCEDENTE` ou `IMPROCEDENTE`; vazio ou valor não reconhecido vira `INDETERMINADO`. A taxa de procedência usa somente `PROCEDENTE + IMPROCEDENTE` como denominador. A marca informativa **Revisar responsabilidade** requer pelo menos seis classificações conhecidas e taxa de Improcedentes de 60% ou mais; não afirma falso positivo e não altera o indicador operacional.

O drawer inclui resumo, barra proporcional, motivos com contagens de procedentes/improcedentes, dimensões de Projeto/Cliente/UF, links com as duas classificações e chamados paginados em lotes de 10 ou 50. O modal mostra somente campos operacionais do mirror; não inclui artigos, notas internas ou anexos, e contatos reconhecíveis no título são removidos na resposta. Uma metodologia futura poderá avaliar ajuste por responsabilidade depois de validação semântica.

## Exportação e navegação para a Base

PDF e XLSX usam a análise e os filtros atuais. Ambos apresentam o indicador operacional e seus cinco pontos componentes; quantidade e exposição de links usam o mesmo snapshot da CMDB exibido no Ranking. O XLSX mantém as abas **Resumo**, **Ranking**, **Providers**, **Links** e **Tickets** (o cabeçalho em português é “Chamados”). Exportação, detalhe e navegação individual para Base respeitam `LinkQueryService.base(user)`; não há acesso fora do escopo autorizado.

O link individual abre a Base por `config_item_id` exato, resolvido dentro do queryset já limitado por Data Scope. Dimensões herdadas/contraditórias não ampliam o acesso; um ConfigItem fora do escopo não retorna resultado. A ação de lista mantém o filtro do provedor para mostrar os links acessíveis.

## Semântica de Tickets e Links

Na janela histórica, `Tickets válidos` conta IDs distintos de tickets NOC nos estados operacionais válidos, pela data de criação. `Links afetados` conta Links distintos com pelo menos um desses Tickets no recorte. Tickets sem relação com Link não inventam impacto na base; um ticket associado a vários Links pode afetar cada Link, sem multiplicar o total nacional de tickets. As linhas de Provedor deduplicam Tickets entre os Links daquele Provedor; um Ticket relacionado a provedores diferentes pode aparecer uma vez em cada linha, por isso a soma das linhas pode exceder o total nacional.

`Links reincidentes` conta Links distintos com ao menos dois Tickets distintos na janela. `Backlog atual` é um snapshot separado, limitado a `OPEN`, `PENDING` e `SCHEDULED`; no ranking por provedor considera somente Tickets relacionados aos Links atuais daquele provedor. Tickets e Links afetados são conceitos diferentes, e reincidência não equivale a impacto atual.

## Metodologia anterior · histórico

A versão anterior combinava risco proporcional (70%) e impacto absoluto (30%), convertendo os componentes em percentis ordinais do conjunto comparado. Risco proporcional: taxa de links afetados (25%), chamados por 100 links (20%), taxa de reincidência (20%), taxa >24h (20%), backlog por 100 links (10%) e criticidade alta/crítica (5%). Impacto absoluto: chamados (40%), links afetados (30%), chamados >24h (20%) e backlog (10%).

Esse modelo podia atribuir 43,3 a provedores sem ocorrência por causa dos percentis dos empates. Está preservado apenas como metodologia histórica e para o estudo de comparação; não alimenta mais tela, drawer, histórico atual nem exportação.

## Estudo real da mudança

O estudo comparativo read-only de outubro/2026 registra posições, Top 10 das três visões e casos relevantes, além do escopo, da normalização P95 e dos limites de interpretação.
## Implementação e interpretação

Campos brutos e classificação de fechamento estão em [OTRS](..\06-integracoes-e-dados\otrs.md); a canonicalização compartilhada e a interpretação de acompanhamento ficam em [Chamados](chamados.md).

A rota `/rankings/` e seus detalhes/exports são implementados no app `rankings`; regra de negócio em `services.py`/`performance.py`, export em `exports.py`, interface em `templates/rankings/` e JavaScript fonte em `frontend_src/js/rankings/`. Visões usam o universo de ticket válido, Data Scope e associação Provider/ISP documentados; filtros mudam recorte, não fórmula.

O indicador operacional é uma leitura auxiliar baseada nos dados disponíveis: não é nota definitiva, SLA contratual, prova de responsabilidade causal de Provider nem avaliação de qualidade de atendimento externa. Os critérios finais de avaliação serão definidos com a operação; a fórmula existente permanece inalterada até lá. Períodos por criação e snapshots atuais da CMDB e do backlog são conceitos distintos. A versão anterior e as comparações numéricas em `docs/07-estudos/` são históricas e não definem o contrato ativo.
