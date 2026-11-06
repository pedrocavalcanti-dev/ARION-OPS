# 02 · Fonte ServiceUp

{{badge:REMOTE}} {{badge:READ-ONLY}}

## Origem e transporte

A configuração em Integration com key cmdb identifica o conector CMDB ServiceUp. CMDBClient fala com a GenericInterface OTRS por HTTP POST com JSON, exclusivamente sobre HTTPS. A implementação não abre conexão SQL com a ServiceUp e não executa alterações em ConfigItems. Os dois caminhos de operação são descritos em [Endpoints](cmdb-endpoints.md).

O campo base_url guarda a origem e o caminho de Webservice informado pelo administrador. O cliente aceita uma base terminada em /Webservice ou uma base de instalação anterior ao Webservice: nesse segundo formato acrescenta o segmento Webservice. Caminhos completos de infraestrutura não são publicados nesta documentação.

## Autenticação

As credenciais são enviadas no corpo JSON como UserLogin e Password, junto aos parâmetros da operação. O modelo Integration armazena o usuário e secret_ciphertext; a senha é protegida pelo serviço central aplicativos/integracoes/services/secrets.py. A view _cmdb_client recupera o valor apenas para construir CMDBClient. O formulário não reexibe a senha: campo vazio preserva o segredo cadastrado.

Isso é autenticação de payload da GenericInterface, não autenticação HTTP Basic. O código não envia cookie de sessão nem token Bearer. Respostas e logs não devem imprimir payload, credenciais, URL privada completa ou CIXMLData bruta.

## TLS, timeout e limites

CMDBConfigurationForm exige HTTPS e rejeita usuário/senha embutidos na URL, query string e fragmento. A verificação do certificado é ligada por padrão; a configuração tem uma opção administrativa explícita para desabilitá-la. Não documentamos essa exceção como recomendada. O timeout padrão é 15 segundos e é limitado pelo cliente a 1–60 segundos. A resposta é limitada a 4 MiB por POST.

O tamanho de lote padrão do Get é 50 e o código limita a configuração a 100. O client não implementa retry/backoff HTTP. A cadência do worker não é a cadência de cada requisição: o snapshot CMDB é agendado a cada 10 minutos e cada execução pode produzir múltiplos POSTs, conforme quantidade de ConfigItems e lotes.

## Uso por telas e comandos

- Teste de conexão executa uma busca de um item da classe Links de internet e verifica se a resposta contém IDs.
- Pesquisa interativa usa Search com filtros de classe, número, nome ou CIXMLData e limite de 1 a 100.
- Consulta de itens usa Get para uma lista de ConfigItemID.
- Sincronização programada usa Search IDs e Get por lote nas classes mapeadas.
- O comando estudar_cmdb também consulta Search/Get, limitado ao fluxo de estudo; ele é somente leitura na fonte, mas grava relatórios locais. Não foi executado nesta tarefa.

## Estado de evidência

| Afirmação | Evidência |
|---|---|
| HTTPS e JSON POST | CMDBClient.__init__ e CMDBClient._post. |
| GenericInterface e autenticação no payload | URL construída em _post e payload autenticado no client. |
| Credencial protegida em repouso | Integration.secret_ciphertext e integração com get_secret_protector. |
| Estrutura do servidor de produção | Não examinada nesta tarefa; nenhuma conexão remota foi aberta. |

Ver [Segurança](cmdb-seguranca.md), [Endpoints](cmdb-endpoints.md) e [Limitações](cmdb-limitacoes.md).
