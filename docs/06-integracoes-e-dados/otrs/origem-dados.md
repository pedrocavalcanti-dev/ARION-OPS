# Origem dos dados OTRS

## Fonte e ambiente

O conector `OTRSReadOnlyClient` acessa um MySQL/MariaDB configurado como integração externa. Host, porta, database, TLS e credenciais são configuração da integração; seus valores não fazem parte desta documentação. A factory `configured_otrs_client` monta o cliente e descriptografa o segredo apenas em memória.

O acesso é remoto ao MySQL do OTRS, mas a aplicação seleciona um database configurado. Se o database estiver ausente no comando de sync, o código tenta descobrir um único schema de aplicação após excluir schemas de sistema; ambiguidade interrompe a operação.

## O que foi levantado

Foram lidos os caminhos do código que executam consultas OTRS:

- `clients/otrs.py`: conexão, introspecção, leitura de artigos e estado de ticket.
- `services/otrs_sync.py`: descoberta de schema, snapshot das filas, Dynamic Fields, histórico terminal e links.
- `services/otrs_study.py` e comando `estudar_otrs`: estudo limitado e não persistente de schema, fila e relações.
- `services/tq_otrs_correlation.py`: leitura incremental de artigos de Qualidade e atualização de tickets já associados.

O arquivo local `docs/07-estudos/estrutura-otrs.md` deriva de uma captura de metadados de 23/10/2026. Ele é evidência histórica para alguns tipos SQL, índices e chaves declaradas. Não foi acessado novamente e não atesta o schema atual.

## Escopo de leitura por rotina

| Rotina | Conteúdo lido | Limite conhecido |
|---|---|---|
| FULL/FAST | Tickets nas filas configuradas, dimensões, Dynamic Fields selecionados, histórico de estados terminais e `link_relation` | O conjunto de filas é explícito; FULL não pagina tickets |
| Estudo | Metadados do schema, amostra de tickets de uma fila, contagens e relações de forma limitada | Resultado fica em memória; amostras são limitadas, colunas sensíveis são filtradas |
| Correlação TQ | Corpo de artigo apenas nas filas de Qualidade, em lotes por `article.id` | Até 200 artigos por lote; corpo só é entregue ao parser do QID |
| Reconciliação TQ | Metadados dos IDs de tickets já ligados | Consulta IDs exatos, sem filtro SQL de fila, para perceber mudança de fila |

O cliente de conexão aplica `SET SESSION TRANSACTION READ ONLY`. Isso protege a sessão da aplicação, mas não substitui grants MySQL restritos à leitura. O projeto não fornece prova atual dos grants do usuário externo.

## Evidência, tipo e inferência

- **Confirmado pelo código:** tabela/coluna projetada, filtro, parâmetros, limites, transformação e destino local.
- **Tipo observado:** metadado constante no estudo local de 23/10/2026. É uma fotografia, não contrato atual.
- **Relação semântica:** associação usada pelo código por ID ou por chave polimórfica; não significa que exista constraint FK.
- **Pendente:** fato que exige novo estudo autorizado do schema ou validação operacional. Esta tarefa não acessou rede, banco ou máquina de produção.

Veja [tabelas](otrs-tabelas.md), [consultas](otrs-consultas-sql.md) e [limitações](otrs-limitacoes.md) para os inventários detalhados.
