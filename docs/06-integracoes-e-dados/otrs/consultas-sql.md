# Catálogo de consultas OTRS

## Critério e segurança

Os IDs abaixo são referências documentais estáveis. São 48 famílias SQL, não 48 execuções: consultas em loop, batch, por fila ou dimensão podem executar várias vezes. Metadados são agrupados por família e SQL dinâmico é descrito como template, sem inventar uma projeção fixa. Parâmetros são placeholders.

Não execute exemplos a partir desta página. A conexão do app inicia transação de sessão somente leitura. O corpo de artigo nas Q202 e Q203 usa caminho especializado e restrito porque o parser de QID precisa examiná-lo.

## Metadados MySQL

| ID | Objetivo e forma da consulta | Tabelas/campos e limites | Código |
|---|---|---|---|
| OTRS-Q001 | Testar conexão e obter DATABASE(), VERSION(), NOW() | Funções do servidor; uma linha | clients/otrs.py:test_connection |
| OTRS-Q002 | Listar SCHEMA_NAME em ordem | information_schema.SCHEMATA | list_databases |
| OTRS-Q003 | Listar TABLE_NAME, TABLE_TYPE, TABLE_ROWS por schema | information_schema.TABLES; estimativa não é contagem exata | list_tables |
| OTRS-Q004 | Listar colunas, tipo, nulabilidade, chave, default e extra | information_schema.COLUMNS, por schema/tabela e ordinal | get_table_schema |
| OTRS-Q005 | Listar índice, unicidade, coluna e tipo | information_schema.STATISTICS, por schema/tabela | get_table_indexes |
| OTRS-Q006 | Listar FKs declaradas e colunas referenciadas | information_schema.KEY_COLUMN_USAGE, exige tabela referenciada | get_foreign_keys |
| OTRS-Q007 | Obter database, versão, charset e collation | Funções e variáveis do servidor | get_server_metadata |
| OTRS-Q008 | Enumerar nome/tipo/nulabilidade/chave/ordinal de todas as colunas do schema | information_schema.COLUMNS, schema parametrizado | get_all_columns; sync e estudo |
| OTRS-Q009 | Amostrar uma tabela validada | SELECT * dinâmico, LIMIT até 50 e OFFSET; recusa nomes article/customeruser | get_sample_rows, estudo somente |

## FULL/FAST

| ID | Objetivo e forma | Filtro, projeção ou batch | Código |
|---|---|---|---|
| OTRS-Q101 | Resolver fila por nome: SELECT id,name FROM queue WHERE name = ? | Nome parametrizado; exige uma correspondência exata | OTRSSyncService._queue |
| OTRS-Q102 | Ler ticket_state: id,name,type_id | Dimensão completa; coluna opcional conforme schema | _dimension(ticket_state) |
| OTRS-Q103 | Ler ticket_priority: id,name | Dimensão completa | _dimension(ticket_priority) |
| OTRS-Q104 | Ler ticket_type: id,name | Dimensão completa | _dimension(ticket_type) |
| OTRS-Q105 | Ler service: id,name | Dimensão completa | _dimension(service) |
| OTRS-Q106 | Ler sla: id,name | Dimensão completa | _dimension(sla) |
| OTRS-Q107 | Ler ticket_state_type: id,name | Dimensão completa | _dimension(ticket_state_type) |
| OTRS-Q108 | Selecionar campos disponíveis de ticket | FULL: queue_id IN (...). FAST: filas observadas ou IDs locais conhecidos, e change_time >= cursor com janela. Sem ORDER BY/LIMIT | _tickets |
| OTRS-Q109 | Resolver nomes das filas atuais não mapeadas | queue.id IN (...), somente quando ticket mudou de fila | run |
| OTRS-Q110 | Resolver IDs dos objetos Ticket e ITSMConfigItem | SELECT id,name FROM link_object; exige os dois nomes | _object_ids |
| OTRS-Q111 | Resolver tipo de relação por nome | SELECT id,name FROM link_type; nome DependsOn | _relation_ids |
| OTRS-Q112 | Resolver estado de relação por nome | SELECT id,name FROM link_state; nome Valid | _relation_ids |
| OTRS-Q113 | Ler relações incidentes nos tickets | link_relation, dois sentidos, batches de até 400; depois filtra IDs de objeto, tipo e estado | _relations |
| OTRS-Q114 | Identificar Dynamic Fields aprovados | dynamic_field por nomes CodigoFechamento, ticketResolvido e RvalidacaoProcedenteImprocedente | _ticket_terminal_fields |
| OTRS-Q115 | Ler valores dos Dynamic Fields | dynamic_field por field_id e object_id; batches de até 400 tickets | _ticket_terminal_fields |
| OTRS-Q116 | Resolver tipo de evento de estado | ticket_history_type com nome StateUpdate; exige exatamente uma linha | _ticket_terminal_fields |
| OTRS-Q117 | Encontrar horário terminal atual | MAX(ticket_history.create_time) agrupado por ticket/state, filtrado por StateUpdate e IDs de ticket/estado | _ticket_terminal_fields |

Forma representativa do SELECT de tickets:

```sql
SELECT id, tn, title, queue_id, ticket_state_id, ticket_priority_id,
       type_id, service_id, sla_id, responsible_user_id, customer_id,
       timeout, until_time, escalation_time, escalation_update_time,
       escalation_response_time, escalation_solution_time, create_time, change_time
FROM ticket
WHERE queue_id IN (?, ...);
```

No código, cada coluna é validada contra metadata e opcionais ausentes são omitidos. FAST acrescenta condição temporal e IDs previamente conhecidos. A query não tem paginação; o conjunto retornado é validado antes de reconciliar o espelho. O SELECT de relações usa a forma (source object = Ticket AND source key IN IDs) OR (target object = Ticket AND target key IN IDs) para preservar as duas direções.

## Qualidade e correlação TQ

| ID | Objetivo e forma | Filtro e limite | Código |
|---|---|---|---|
| OTRS-Q201 | Resolver um único queue_id pelo nome exato | queue.name = ? LIMIT 2; rejeita zero ou mais de uma linha | fetch_quality_articles |
| OTRS-Q202 | Ler artigos incrementais com body transitório | JOIN article-ticket-queue-state-MIME; queue_id exato, article.id > cursor, ORDER BY article.id, LIMIT até 200 | fetch_quality_articles |
| OTRS-Q203 | Localizar artigo de ticket indicado que contenha QID | ticket.tn exato, a_body LIKE %QID%, LIMIT até 20; parser valida token completo depois | find_quality_ticket_article_by_qid |
| OTRS-Q204 | Atualizar metadados de IDs de tickets já ligados | JOIN ticket-queue-state, ticket.id IN (...), LIMIT até 200; consulta todas as filas | fetch_quality_ticket_states |

Q202 seleciona article.id, article.ticket_id, ticket.id/tn/queue_id/ticket_state_id, nomes de fila/estado e article_data_mime.a_body. O body não é persistido. Q203 também seleciona horário do artigo. Em Q204, o argumento de nomes de fila exige configuração não vazia, mas não aparece como filtro SQL: a consulta busca os IDs ligados através de qualquer fila.

## Estudo limitado

| ID | Objetivo e forma | Limite/condição | Código |
|---|---|---|---|
| OTRS-Q301 | Resolver fila exata e devolver candidatos | queue.name = ?, LIMIT 20 | OTRSStudyService._resolve_queue |
| OTRS-Q302 | Sugerir nomes parecidos somente após falha exata | queue.name LIKE ?, LIMIT 20 | _resolve_queue |
| OTRS-Q303 | Ler tickets de uma fila para análise | Projeção montada das colunas permitidas; exclui customer_user_id; filtro queue_id | _study_tickets |
| OTRS-Q304 | Ler IDs e nomes de link_object | LIMIT 100 | _study_links, _lookup_relation_values |
| OTRS-Q305 | Ler relações em ambos os sentidos para tickets da amostra | IDs em batches de 300; seleciona chaves e, se disponíveis, tipo/estado | _study_links |
| OTRS-Q306 | Ler definições de tipo e estado de relação | ID, nome e validade quando disponíveis; tabelas link_type/link_state | _relation_definitions |
| OTRS-Q307 | Ler rótulos das dimensões de ticket | ID, nome e, para estados, tipo/validade disponíveis; LIMIT 500 | _study_dimensions |
| OTRS-Q308 | Ler rótulos de ticket_state_type | ID/nome, LIMIT 100 | _study_dimensions |
| OTRS-Q309 | Listar nomes de Dynamic Fields | Nome em ordem, LIMIT 500; nomes sensíveis são mascarados no relatório | _study_dimensions |
| OTRS-Q310 | Agregar artigos por canal/remetente para a fila | JOIN article-ticket, GROUP BY metadados; não seleciona corpo/anexos | _article_metadata |
| OTRS-Q311 | Traduzir tipo de remetente | ID/nome em article_sender_type, LIMIT 100 | _article_metadata |
| OTRS-Q312 | Contar tickets com evento na fila histórica | COUNT DISTINCT ticket_id por queue_id; evita consulta sem índice | _historical_queue_count |
| OTRS-Q313 | Ler rótulos dos tipos de histórico | ID/nome de ticket_history_type, LIMIT 100 | _history_type_counts |
| OTRS-Q314 | Contar eventos por tipo de histórico | ticket_id IN batches de 300, GROUP BY history_type_id | _history_type_counts |
| OTRS-Q315 | Contar IDs de tipo/estado das relações da amostra | link_relation, objetos e chaves nos dois sentidos, batches de 300 | _lookup_relation_values |
| OTRS-Q316 | Traduzir tipo/estado de relação | ID/nome/valid_id da dimensão, LIMIT 100 | _lookup_relation_values |
| OTRS-Q317 | Classificar ConfigItem | JOIN configitem.class_id = general_catalog.id; namespace fixo e IDs em batches de 300 | _classify_configitems |
| OTRS-Q318 | Contar linhas relacionadas por ticket | COUNT(*) com ticket_id IN batches de 300; tabela article ou ticket_history | _count_related |

O estudo é diagnóstico, não carga de mirror. Os SELECTs dinâmicos são montados após verificar tabelas/colunas no schema e validar identificadores. Leia [Segurança](otrs-seguranca.md) antes de interpretar limites de amostra.

## Execução e parâmetros

FULL é disparado pelo worker a cada 30 minutos e FAST a cada 2 minutos, após cobertura FULL válida. Estudo é comando explícito. Correlação TQ usa batch configurável até 200; recuperação manual é iniciada por operador. Resultados de sync escrevem apenas no banco local quando apply ou worker aplica; consultas deste catálogo não têm botão de execução.

Links: [sincronização](otrs-sincronizacao.md), [tabelas](otrs-tabelas.md), [Qualidade/TQ](otrs-qualidade-tq.md), [diagnóstico](otrs-diagnostico.md).
