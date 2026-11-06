# 15 · Qualidade e links prioritários

## Inventário e relação com ocorrências

Qualidade parte de CMDBLinkMirror presente, carrega Provider e aplica Data Scope. Ordens de qualidade podem guardar uma FK Link e um snapshot de ConfigItemID; ocorrências e tickets OTRS são cruzados pelo identificador do Link e por OTRSTicketConfigItem. Isso mantém investigação operacional ligada ao mirror sem editar ConfigItem na fonte.

## Dimensões de exibição

designation/circuit e as alternativas de nome vêm do Link. link_designation escolhe a primeira designação operacional preenchida na ordem designation, circuit, operator_designation, identification, name, source_number; exclui identificadores técnicos com prefixo interno 361310. Provider é resolvido por operator/isp_raw. Projeto usa business_project e recorre a legacy_project quando a dimensão nova está vazia. Cliente é contracting_client.

## Fila de atenção empresarial

A ordenação business_priority considera nome de Provider, projeto e cliente. Mapeamentos específicos no código:

| Provider / dimensão | Projeto | Peso de atenção |
|---|---|---:|
| Claro | Banco do Brasil | 10 |
| Claro | Correios | 20 |
| Vivo | Bradesco | 30 |
| Vivo | Arcos Dourados | 40 |
| Cliente igual FUST ou projeto igual FUST | qualquer | 90 |
| Outras combinações | — | 99 |

Valores menores aparecem primeiro na fila de maior atenção. Esse índice só influencia a ordem/categoria empresarial da lista; não altera o número de ocorrências, recorrências nem a prioridade formal de uma QualityOrder.

## Reincidência

build_priority_link_analysis combina o período selecionado (opções de 7, 30, 60, 90, 180 e 365 dias; padrão 60) com build_quality_recurrence_analysis, tickets válidos do OTRS e ocorrências/ordens existentes. O RREIP operacional e as janelas de estabilidade têm constantes próprias; QUALITY_RREIP_DAYS é 30 dias. A contagem é por Link/config item e usa chave de origem, não por texto de designação.

O painel pode apresentar quantidade de ocorrências do período, recorrências na janela, estado do episódio, estabilização e reabertura. São métricas locais derivadas; mudanças em dados CMDB só aparecem após sync válido. Ausência de ISP/projeto/classificação reduz a precisão do agrupamento mas não autoriza atribuir um valor arbitrário.

## Controle de acesso

QualityDataScopeService/DataScopeService restringe Links e ordens no backend. Filtros e listas de opções usam a mesma população autorizada. A prioridade empresarial não substitui permissão funcional nem escopo de dados. A rastreabilidade e limitações constam em [Indicadores](cmdb-indicadores.md) e [Limitações](cmdb-limitacoes.md).
