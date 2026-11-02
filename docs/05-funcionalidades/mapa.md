# Mapa

O Mapa e a visao geografica operacional do NOC Atacado. A interface oferece os modos Base, Projetos, Chamados, Reincidencia e Motivos, com filtros compartilhados de cliente, projeto e provedor. O painel Brasil mostra resumo contextual, ranking Top 5 e distribuicao; selecionar uma UF abre detalhes com bandeira local, links, tickets e dimensoes relacionadas.

## Fontes e dimensoes

O backend consulta somente os mirrors locais. `CMDBLinkMirror.name` preserva o Nome original do CMDB. `LinkBusinessIdentityService` deriva Cliente/operadora contratante, Projeto, Conta e Business ID somente de prefixos reconhecidos e delimitadores estruturados. Separadores (`-`, en dash e em dash), espacos e acentuacao sao normalizados para comparacao, sem substituir o texto raw. Classificacoes ambiguas ficam vazias. `legacy_project` continua historico e nao e o Projeto principal.

Cliente/Projeto/Conta sao dimensoes comerciais parseadas do nome CMDB por `BusinessIdentityRules`. TELMEX e preservado como valor raw, mas sua dimensao Cliente canonica e Claro. Provedor/ISP e sempre o `CMDBLinkMirror.operator` associado ao ConfigItem; nao se mistura com Cliente.

O Data Scope e aplicado por `LinkQueryService.base(user)` antes de filtros, catalogos, detalhes ou agregacoes. Operadora de escopo representa Cliente/contratante e Projeto representa `business_project`. Para grupos antigos, `legacy_project` so e consultado quando o link ainda nao tem Projeto parseado, preservando a intersecao sem alargar o acesso de links classificados.

## Modos e datas

- Base mostra o snapshot CMDB atual. Projetos preserva Links CMDB do snapshot e acrescenta os Tickets NOC criados no periodo selecionado; filtros de status OTRS/ARION, responsavel, prioridade e procedencia refinam essa parte temporal.
- Chamados contam Tickets distintos da fila NOC relacionados a Links da UF, pela data local de criação (`source_created_at`), nos estados `OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED` e `CLOSED`, com responsabilidade `PROCEDENTE` ou `INDETERMINADO`. `CANCELED`, `UNKNOWN` e `IMPROCEDENTE` ficam fora. Um Ticket relacionado a vários Links na mesma UF conta uma vez nessa UF e uma vez no total nacional. Tickets sem ConfigItem ou sem Link local entram somente no total nacional sem filtro dimensional; não recebem UF nem aumentam Links afetados. Em Data Scope `CUSTOM`, Tickets sem relação com Link autorizado permanecem fora.
- Reincidência conta Links com ao menos dois Tickets distintos válidos criados no período e com responsabilidade `PROCEDENTE` ou `INDETERMINADO`; Tickets nos Links reincidentes também são apresentados. `RESOLVED` e `CLOSED` permanecem válidos para essa análise histórica.
- Motivos contam resoluções operacionais de Tickets `RESOLVED`/`CLOSED` com responsabilidade `PROCEDENTE` ou `INDETERMINADO`, pela data local `resolved_at`, usando `terminal_state_at` somente como fallback. O estado bruto continua sendo preservado; `closing_code_raw` e `ClosingReasonCanonicalizer` fornecem a categoria, compartilhada com Cockpit e Rankings. `reason=falha_massiva` filtra os raws `FalhaMassiva`, `Falha Massiva` e `Defeito Massivo`; aliases desconhecidos não são inferidos. Tickets cancelados, improcedentes, sem código ou sem qualquer data de resolução/terminal não entram. Chamados e reincidência continuam pela data de criação.

Os limites de data usam inicio inclusivo e dia seguinte exclusivo no fuso local, evitando conversoes `DATE()` inconsistentes entre SQLite e MySQL. Periodos disponiveis: este mes, mes anterior, ultimos 30/90 dias e intervalo personalizado.

## Mapa e API

A representacao e choropleth estadual com as 27 UFs do asset local `static/geo/brasil_estados.geojson`; nao existem coordenadas confiaveis por link e nao ha geocodificacao/markers individuais. Em Base/Projetos, a cor indica o Cliente predominante na UF (Claro terracota, Vivo roxo, Algar verde, demais neutro); ao filtrar um cliente, a intensidade representa volume. Chamados, Reincidencia e Motivos usam escalas quantitativas separadas. Hover mostra a metrica do modo e clique abre o drawer da UF. A barra superior agrupa titulo/fonte, filtros de contexto e controles de modo/busca. No desktop largo ocupa cerca de 92px; entre 1200px e 1499px reorganiza em duas linhas, e em telas menores usa tres grupos compactos.

GET /base/api/mapa/ agrega contagens por UF sem enviar a lista completa de Tickets ao navegador. Aceita mode, client, project, provider, period, date_from, date_to, reason, status, priority, arion_status, assignee e provenance conforme o modo. GET /base/api/mapa/uf/ acrescenta uf, tab, ticket_search, ticket_page e ticket_page_size (25, 50 ou 100); a aba Chamados recebe somente a pagina solicitada. As abas Overview, Tickets, Projects, Providers, Reasons e Recurrence reutilizam os agregados filtrados. A integracao com Base preserva os filtros comerciais; Cockpit recebe UF, metrica, periodo e motivo quando aplicavel.

O Mapbox recebe as geometrias estaduais e os valores agregados. O token e publico por definicao do SDK e vem de `MAPBOX_PUBLIC_TOKEN`; sem token ou sem rede, a pagina informa o erro recuperavel sem apagar o restante da aplicacao. Bandeiras sao assets locais em `static/imagens/bandeiras/estados/`. No tema escuro, o fundo do mapa e grafite neutro `#0c0d0d`; UFs sem dados usam cinza neutro `#363839`. O tema claro mantem o fundo atual.

## Interacao e apresentacao visual

O hover mostra um tooltip compacto junto a UF, com conteudo adaptado ao modo e ancoragem automatica nas bordas. Clicar abre o drawer lateral persistente da UF. As abas mostram resumo, Tickets paginados, Projetos canonicos, Provedores, Motivos e Reincidencia. Trocar o hover nao altera a UF selecionada; fechar o drawer, escolher outra UF ou limpar a selecao encerra o foco.

A aba Tickets pesquisa no servidor por numero, titulo, cliente, projeto ou motivo e aceita 25/50/100 linhas por pagina. Cada linha inclui status OTRS, status/responsavel ARION, data de criacao, prioridade, identidade do Ticket e provedor. Abrir direciona para o drawer existente em Chamados; Tickets historicos aparecem em leitura, enquanto alteracoes ARION continuam limitadas ao universo ativo da fila.

Os filtros progressivos cobrem Cliente, Projeto, Provedor, periodo/datas, status OTRS, prioridade, status ARION, responsavel, procedencia e motivo de fechamento. Chips ativos permitem remover filtros e limpar o recorte; o estado dos filtros e preservado na URL. Todas as dimensoes, catalogos e paginas respeitam o Data Scope antes de agregar ou consultar o detalhe.


O mapa permanece 2D. `arion-state-fill` pinta as UFs, `arion-state-selected-halo` e `arion-state-outline` destacam selecao e hover com `feature-state`, e `arion-state-labels` posiciona siglas em 19 estados. RN, PB, PE, AL, SE, ES, RJ e DF usam callouts geograficos em fonte GeoJSON separada: conectores e labels seguem pan/zoom, aceitam hover e clique e abrem o mesmo drawer que a geometria estadual. O estado selecionado recebe contorno e halo; os demais permanecem legiveis com intensidade reduzida. A selecao pode ser feita no poligono, nos labels, nos callouts, na busca ou no Top 5 do painel.

`fitBrazil()` e `fitSelectedState()` centralizam o enquadramento. O padding considera a largura dos overlays laterais, o viewport e a UF selecionada; recolher o painel Brasil recalcula o foco. O mapa mantem uma instancia e reutiliza sources/layers nas mudancas de modo e filtro. O estilo usa somente o fundo grafite/off-white e o GeoJSON estadual local, sem pontos, ruas, POIs ou terreno.

Em Base, o Cliente predominante segue a regra existente e a intensidade varia com o volume: Claro usa coral/terracota, Vivo lavanda/violeta, Algar verde/teal, Outras azul-ardosia e nao identificado cinza frio. Em Projetos, Todos os projetos e `Diversos` mantem a escala azul-petroleo neutra. Ao selecionar um projeto, a cor passa a usar uma escala quantitativa visualmente inspirada em sua identidade; Cliente e Projeto continuam sendo filtrados pelas dimensoes existentes. Chamados mantem pessego/coral/terracota, Reincidencia magenta/vinho e Motivos ambar/laranja/vermelho. A legenda usa as mesmas tres paradas do fill e acompanha a troca de projeto e tema. Essas cores sao apresentacao, nao declaracao de paleta oficial das marcas.

## Nomes canônicos de Projeto no Mapa

Filtros, opções, contagens e grupos do Mapa normalizam somente aliases explícitos: `BDN` e `BRADESCO` são exibidos como `BRADESCO`; `BB` e `BANCO DO BRASIL` são exibidos como `BANCO DO BRASIL`. Comparação ignora caixa, espaços excedentes e variações conservadoras de separadores. `BRADESCO SEGUROS` e `BB SEGUROS` continuam projetos distintos. O Data Scope é aplicado antes de descobrir e combinar aliases, então um nome equivalente fora do escopo não amplia o acesso. A normalização é analítica e exclusiva do Mapa; títulos, valores brutos e campos persistidos não são reescritos.

Paletas de Projetos configuradas:

As paletas desta tabela são apenas apresentação visual e não definem aliases analíticos; somente os mapeamentos explícitos da seção de nomes canônicos são usados para agrupar Projetos.

| Projeto | Paleta visual inspirada |
| --- | --- |
| Bradesco | rosa/vermelho |
| Banco do Brasil | amarelo/ouro e azul profundo |
| Correios | amarelo/ouro e azul profundo |
| Arcos Dourados | amarelo, laranja e vermelho |
| Caixa Econômica Federal | azul e laranja |
| Fust | violeta |
| Escolas Conectadas | verde |
| Carrefour | azul e vermelho |
| Petrobras | verde e amarelo |
| Diversos e projetos sem paleta específica | azul-petróleo neutro |

O tooltip reutilizado acompanha o ponteiro sobre o mapa com popup Mapbox, afastamento de 18px e ancoragem automatica nas bordas. O hover nao move nem substitui o Painel Brasil; a UF selecionada nao abre tooltip enquanto seu drawer esta visivel, mas outra UF ainda pode ser inspecionada. Clique e navegacao do drawer fecham o popup. Base e Projetos mostram volume e dimensoes comerciais disponiveis; Chamados mostra tickets, links afetados, reincidencia e principal motivo; Reincidencia mostra links reincidentes, tickets associados e Provider principal; Motivos mostra encerramentos, links afetados e Provider principal. Campos ausentes sao omitidos. Busca aceita nome com ou sem acento e sigla, exibe bandeira local, e suporta setas, Home/End, Enter e Escape; seu autocomplete sobe acima das camadas do mapa, legenda e controles enquanto aberto e fecha antes de aplicar zoom/drawer. Clique ou busca aplica o mesmo destaque, zoom e drawer. O breadcrumb sobre o mapa mostra Brasil / UF; Brasil fecha o drawer, enquadra o pais e limpa somente a UF. O drawer preserva os dados existentes, tem navegacao alfabetica entre UFs e rolagem apenas no conteudo.

O painel Brasil e uma superficie absoluta independente, sem wrapper visual legado, com 300px no desktop e 272px em telas intermediarias. O recolhimento tira o painel da composicao visual apos a transicao; permanece apenas um controle compacto de expansao. O drawer tem 356px no desktop, 330px em telas intermediarias e ocupa a largura disponivel como folha inferior no celular; preserva os dados atuais, com cabecalho e acoes fixos e apenas as secoes internas rolaveis. O ranking Top 5 e acionavel. A troca de recorte atualiza o source existente, preserva o mapa e cancela requisicoes anteriores; filtros, payloads e semantica de contagem nao mudam.

## Backlog visual/geografico

Permanecem para uma etapa futura: zoom estadual com geometria municipal, hotspots, comparacao temporal e story mode. Esta entrega nao integra geometria municipal nem exibe cidades no mapa estadual.

Nao foi implementada extrusao 3D. Sem coordenadas confiaveis por link, extrudar estados por volume distorce a comparacao choropleth e prejudica a leitura das UFs pequenas; contorno, halo, labels e transicoes sutis servem melhor a exploracao operacional.

## Qualidade da identificacao de negocio

Snapshot do mirror local: 3.437 links. Antes das regras comerciais confirmadas, Cliente estava identificado em 2.074 (60,34%) e nao identificado em 1.363 (39,66%). Depois da reclassificacao derivada, 3.057 (88,94%) estao identificados e 380 (11,06%) permanecem sem inferencia. Projeto identificado: 3.197 (93,02%); Provedor/ISP permanece em 97,03%. As proporcoes pertencem ao snapshot consultado e variam com a sincronizacao.

Nos 1.363 links sem Cliente, nomes/prefixos recorrentes incluem BANCO DO BRASIL (338), FUST (307), BRADESCO (152), ARCOS DOURADOS (105), CAIXA ECONOMICA FEDERAL (65), CORREIOS (45), FUST UBS (45), ESCOLAS CONECTADAS (43), LINK MOTIVA (32), DIVERSOS (27) e SANTANDER (15). Esses prefixos nao identificam, por si, o cliente contratante. O ISP tambem varia dentro desses grupos. Nenhuma regra Cliente foi inferida deles. O parser ja reconhece um segmento de projeto imediatamente antes de um Business ID delimitado; os prefixos com segmentos adicionais continuam ambiguos e nao foram promovidos a Projeto nesta revisao. Nenhum dado do CMDB de origem foi alterado.

TELMEX e um token comercial explicito relacionado a operacao Claro: o raw continua auditavel no nome CMDB e em `contracting_client_raw`, enquanto `contracting_client` e canonico Claro. `Outras` agrupa clientes identificados fora de Claro/Vivo/Algar; Cliente nao identificado e uma categoria separada no resumo e na legenda. Se a categoria nao identificada for maior que qualquer categoria identificada agregada, a UF recebe o tom neutro.

Na apresentacao por UF, Cliente, Projeto, Conta e Provedor usam rotulos de nao identificacao especificos da dimensao. O drawer Base informa cobertura de Cliente e as quantidades identificada/nao identificada. Variantes de cidade sao agrupadas por chave sem acento, caixa e espacos; o nome de origem permanece intacto. No snapshot estudado, Goiânia/GOIANIA em GO soma 14 links sob o rotulo Goiânia. O Mapa usa os nomes canônicos de Projeto definidos acima em filtros e grupos; os valores raw persistidos permanecem intactos.

Base e um snapshot CMDB e nao exibe Periodo. Projetos preserva o conjunto atual de Links CMDB e usa o Periodo selecionado para as metricas e o drill-down de Tickets. Chamados, Reincidencia e Motivos exibem Periodo e aceitam `YYYY-MM` em URLs compartilhaveis. A URL do Mapa preserva modo, cliente, projeto, periodo, UF, motivo, provedor e os novos filtros operacionais. As secoes do drawer dependem do modo e de haver dados; Motivos e Reincidencia nao aparecem na Base.


## Regras comerciais e contexto OTRS

`BusinessIdentityRules` centraliza a precedencia: cliente explicito no nome CMDB; regra de projeto exclusivo; projetos compartilhados sem inferencia; caso ambiguo sem cliente. TELMEX canoniza para Claro e nunca e Projeto; tokens `CLARO.UF` sao contexto tecnico, nao Projeto. Telefonica/Vivo canoniza para Vivo; Correios, Banco do Brasil, Caixa Economica Federal e Carrefour inferem Claro; Fust, L2L e Arcos Dourados/McDonald's inferem Vivo; Escolas Conectadas infere Algar. Bradesco e Diversos nunca inferem cliente sozinhos. O Nome original CMDB permanece intacto e campos derivados do mirror foram recalculados; a origem CMDB nao foi alterada.

Na reclassificacao local de 25/10/2026, 3.437 links atuais resultaram em 27 valores distintos de `business_project`; `TELMEX` deixou de aparecer como Projeto. Dos nomes com token TELMEX, 273 ficaram com Cliente canonico Claro; 174 desses tambem tem Projeto Bradesco. O total Claro/Bradesco no snapshot e 288. Os valores sao fotografias do mirror, nao aliases globais adicionais.

`TicketBusinessIdentityService` interpreta o titulo espelhado do ticket para contexto do proprio ticket (cliente, projeto, tipo, servico e Business ID). Cliente explicitamente confiavel do titulo prevalece no contexto analitico do chamado; na ausencia dele, usa-se a identidade do link CMDB. Essa classificacao nao atualiza o link. No snapshot analisado, 10.195 de 11.973 titulos continham ao menos um campo de identidade reconhecido (9.442 com cliente e 5.149 com projeto). O drawer autorizado do Mapa apresenta titulo, status, prioridade, datas, motivo, identidade parseada e campos operacionais do ConfigItem; artigos, notas internas, anexos e dados pessoais nao sao importados nem apresentados. Chamados distribui tickets por status, motivo, cliente, projeto ou provedor em um unico painel; o detalhe abre no drawer de Chamados.
## Implementação e limites

A rota de página é `/mapa/`; serviço em `aplicativos/mapa/services/analytics.py`, endpoints nas views/urls do app, apresentação em `templates/mapa/` e JavaScript fonte em `frontend_src/js/mapa/`. A query aplica Data Scope antes de agregar por UF. Modos atuais: `base`, `projects`, `calls`, `repeat` e `motives`; filtros são validados no backend.

Mapa mostra agregados por UF, não geocodificação de cada endereço, coordenada precisa ou rota. UFs ausentes/inválidas não viram localização inferida. Tickets e Links dependem do mirror local e da freshness; a página não abre conexão OTRS/CMDB.

## Contagem de Tickets, Links afetados e reincidência

No modo Chamados, Tickets são IDs distintos da fila NOC nos estados operacionais válidos (`OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED`, `CLOSED`), com responsabilidade `PROCEDENTE` ou `INDETERMINADO`, criados no período. Um Ticket associado a dois Links conta uma vez no total nacional; dentro de cada UF conta uma vez em cada UF onde possui Link. Links afetados são Links locais distintos com ao menos um Ticket selecionado. Tickets sem ConfigItem ou com ConfigItem sem Link local são somados ao total nacional somente quando cliente, Projeto, provedor e UF não estão filtrados; eles não são atribuídos a uma UF nem contados como Link afetado. Em Data Scope `CUSTOM`, a regra continua restrita a Tickets com relação a um Link autorizado. Links com UF inválida não recebem localização inferida.

Reincidência é outra métrica: cada Link conta uma vez se tiver pelo menos dois Tickets distintos válidos no período. A quantidade de Tickets em Links reincidentes deduplica IDs globalmente e por UF, mesmo quando o mesmo Ticket participa de mais de um Link reincidente. Não é sinônimo de Links afetados nem de Tickets ativos agora.
