# 04 · Estruturas de resposta

## Contrato observado pelo parser

O código não fixa um único schema de JSON da ServiceUp. GenericInterface pode envelopar a operação em Data ou outras chaves. CMDBClient._items_from_response reconhece listas e envelopes chamados ConfigItem, ConfigItems, Items, Item, Result, Data, ConfigItemSearch e ConfigItemGet; também percorre valores aninhados e objetos indexados por ID. A busca de IDs procura ConfigItemID e ConfigItemIDs recursivamente.

Isso descreve tolerância implementada no ARION, não uma garantia universal da ServiceUp. Sem consulta externa nesta tarefa, não afirmamos que cada formato aceito foi emitido pela instância atual.

## Busca

Na execução integral, Search fornece identificadores. A representação aceita inclui um ConfigItemID singular ou uma coleção ConfigItemIDs. O client remove duplicatas preservando a primeira ordem. Se a resposta não declarar esses identificadores, test_connection e search_item_ids rejeitam a resposta como inválida.

## Detalhe do ConfigItem

O item de detalhe contém campos diretos e atributos dinâmicos em CIXMLData. CIXMLData pode chegar como objeto JSON aninhado ou como string XML. O sincronizador achata os nós folha, remove namespace XML e compara nomes sem diferença de caixa. Extração usa o campo direto quando não vazio; caso contrário usa o atributo CIXMLData.

Exemplo estrutural inteiramente sintético:

```json
{
  "ConfigItem": {
    "ConfigItemID": "CI-DEMO-001",
    "Number": "DEMO-001",
    "Name": "CLARO - PROJETO EXEMPLO - BD123",
    "Class": "Links de internet",
    "CIXMLData": {
      "Cidade": "Cidade Exemplo",
      "Estado": "SP",
      "ISP": "CI-ISP-DEMO"
    }
  }
}
```

O exemplo não significa que todos os atributos sejam retornados sempre, nem que os campos ilustrados sejam obrigatórios na fonte. Os atributos opcionais vazios são gravados como string vazia no mirror; os campos obrigatórios para sync são ConfigItemID e Name para Operadoras e ConfigItemID para Links.

## Erros de aplicação e resposta parcial

O parser identifica Error, ErrorCode, ErrorMessage, Fault e sucesso explicitamente falso. Erros cujo texto indica autenticação são classificados como falha de autenticação; outros são resposta inválida. A mensagem sensível não é reproduzida.

O sync exige correspondência integral entre IDs de Search e detalhes de Get, sem IDs repetidos e com classe esperada. Um único item faltante, de classe inesperada ou incompleto impede reconciliação. Assim uma falha de envelope não transforma todo o inventário em obsoleto.

## Sanitização

Pesquisa apresentada na interface expõe somente PUBLIC_ITEM_FIELDS e atributos não sensíveis. Estudos conservam nomes de campos sensíveis apenas para medir presença e substituem valores por [PROTEGIDO]. Strings são limitadas; estruturas profundas ou grandes são resumidas. Senhas e demais valores são excluídos do mirror pela whitelist do sync.

Ver [Dicionário de dados](cmdb-dicionario.md), [Segurança](cmdb-seguranca.md) e [Limitações](cmdb-limitacoes.md).
