# 03 · Catálogo de endpoints

O inventário do repositório contém duas operações remotas distintas, ambas POST JSON. IDs documentais estáveis são locais à documentação e não representam nomes de endpoint da ServiceUp.

## CMDB-API001 — ConfigItemSearch

| Propriedade | Contrato implementado |
|---|---|
| Caminho relativo | CMDB/ConfigItemSearch, sob o prefixo Webservice quando necessário. |
| Método | POST |
| Função | CMDBClient.search_items, search_item_ids e test_connection. |
| Content headers | Content-Type: application/json; Accept: application/json. |
| Autenticação | UserLogin e Password no objeto JSON, não reproduzidos aqui. |
| Body do estudo/sync | {"ConfigItem":{"Class":"Links de internet"}} ou {"ConfigItem":{"Class":"Operadoras"}}; Limit é omitido pela sincronização integral. |
| Body de pesquisa | ConfigItem pode conter Class, Number, Name, CIXMLData e Limit. Campos vazios são omitidos; limite é 1–100, padrão 25. |
| Resposta consumida | Busca de IDs: ConfigItemID/ConfigItemIDs em qualquer envelope reconhecido pelo parser. A pesquisa interativa também aceita itens completos. |
| Uso posterior | IDs são deduplicados no client e enviados ao Get. |

Exemplo sintético do body funcional, antes dos campos de autenticação:

```json
{"ConfigItem":{"Class":"Links de internet"}}
```

O código não implementa paginação em chamadas Search. Na sincronização, não passa Limit (CMDBSyncService.LIMIT = None); a exaustividade é assumida como propriedade do contrato configurado e da busca realizada. Limites do lado ServiceUp e paginação remota precisam validação autorizada.

## CMDB-API002 — ConfigItemGet

| Propriedade | Contrato implementado |
|---|---|
| Caminho relativo | CMDB/ConfigItemGet, sob o prefixo Webservice quando necessário. |
| Método | POST |
| Função | CMDBClient.get_items. |
| Body | {"ConfigItemID":"id-1,id-2"} com até batch_size IDs por chamada. |
| Lote | Configurável de 1 a 100; padrão 50. IDs vazios e duplicados são removidos antes do envio. |
| Resposta consumida | Um ou vários ConfigItems em objetos/listas ou envelopes conhecidos; detalhes de Name, Number, Class e CIXMLData. |
| Uso posterior | Sanitização, validação de completude/classe, extração de campos e upsert local. |

Os exemplos são contratos de requisição construídos pelo código, com IDs sintéticos. Não são capturas de uma instância real.

## Códigos HTTP e falhas

| Caso visto pelo client | Resultado seguro |
|---|---|
| 401 ou 403 | CMDBAuthenticationError |
| 404, 400 ou 422 | CMDBInvalidResponseError |
| Outros HTTPError | CMDBUnavailableError com status HTTP |
| Timeout/socket timeout | CMDBTimeoutError |
| Falha de verificação de certificado | CMDBTLSFailure |
| JSON inválido, texto fora de UTF-8 ou resposta acima de 4 MiB | CMDBInvalidResponseError |
| Erro da API no JSON | Código técnico validado é preservado; detalhes arbitrários não são expostos |

Não existem tentativas automáticas configuradas no cliente CMDB. Ver [Diagnóstico](cmdb-diagnostico.md), [Sincronização](cmdb-sincronizacao.md) e [Segurança](cmdb-seguranca.md).
