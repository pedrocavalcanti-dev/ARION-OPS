# 17 · Diagnóstico

Os passos abaixo usam telas e metadados locais. Não repetir chamada remota nem executar sync pesado sem autorização operacional. A sincronização e os comandos de estudo consultam a ServiceUp; use-os apenas sob o procedimento autorizado.

## Conexão e autenticação

| Sintoma | Verificação segura | Interpretação / próximo passo |
|---|---|---|
| Integração não configurada | Consulte o estado da integração e se a tela mostra credencial configurada, sem revelar valor. | Revise base HTTPS, usuário e segredo pelo processo autorizado. |
| AuthenticationError / 401 / 403 | Consulte IntegrationRun/status e o tipo de exceção, nunca o body. | Confirme usuário habilitado e autorização da GenericInterface com o administrador da fonte. |
| Timeout | Compare timestamp, timeout configurado, duração do IntegrationRun e saúde de rede aprovada. | Client não repete automaticamente; worker agenda retry conforme estado. |
| TLS/certificado | Verifique hostname/cadeia do certificado e exceção CMDBTLSFailure. | Corrija cadeia/CA; não desative verify_tls como primeiro passo. |
| HTTP 404/400/422 ou JSON inválido | Revise caminho relativo, versão/configuração do Webservice e tipo de erro registrado. | Trate como endpoint/contrato incompatível; não ajuste schema do mirror por tentativa. |

## Sincronização e frescor

| Sintoma | Verificação segura | Interpretação / próximo passo |
|---|---|---|
| Sync lenta | Compare duração, número de IDs, HTTP requests e Get batches no IntegrationRun. | Lotes e volume explicam número de POSTs; client não tem backoff próprio. |
| Worker indisponível | Confira estado do processo pelo supervisor e IntegrationSyncState. | Sem scheduler, o mirror continua disponível mas envelhece. |
| Last success antigo | Compare IntegrationSyncState.last_success_at, IntegrationRun recentes e estado do conector. | Falha mantém o último snapshot; não representa ausência de Links na fonte. |
| Sync cancelada por incompletude | Identifique tipo seguro do erro: ID faltante, duplicidade, classe ou Number collision. | Nenhuma reconciliação parcial deveria ser aplicada; investigue resposta e contrato autorizado. |
| Registros antigos ainda presentes | Verifique is_present_in_source e timestamps, não apague manualmente. | Ausentes em snapshot completo são marcados stale e conservados. |

## Dados e resolução

| Sintoma | Verificação segura | Interpretação / próximo passo |
|---|---|---|
| ISP sem Provider | Compare isp_raw, operator_resolution_method e ConfigItemID/name do mirror. | UNRESOLVED pode significar ID ausente, nome ambíguo ou Provider fora do snapshot. |
| Cliente/projeto divergente | Revise Name original e campos derivados/confidence/source; teste parser com nome sintético. | Regra de classificação é interpretativa; registre alias aprovado, não corrija Name. |
| Cidade/UF vazia ou incoerente | Leia city/state do mirror e confirme tipo de dado aprovado na fonte. | Código não geocodifica nem deriva UF de endereço. |
| Ticket sem Link local | Compare OTRSTicketConfigItem.config_item_id com source_config_item_id. | Relação OTRS pode existir sem mirror correspondente. A chave não é Number. |
| Quantidade diferente no Ranking | Compare links_total e subconjuntos por Provider/presença/escopo. | Links sem Provider contam no total geral, mas ficam fora de linhas nomeadas. |
| Qualidade não mostra o circuito esperado | Verifique presença, escopo, relação OTRS, designações e ordem/ocorrência aplicável. | Designação é escolhida de campos candidatos; prioridade de atenção não altera recorrência. |

## Comandos e cautelas

O management command sincronizar_cmdb com --dry-run/default ainda lê a ServiceUp e grava IntegrationRun local; não é um teste offline. --apply também escreve o mirror ARION. estudar_cmdb é leitura remota e cria arquivos Markdown locais em docs/estudos. Não executar em investigação sem aprovação da operação responsável. Nenhum passo deste guia escreve na ServiceUp ou recomenda exclusão de linhas.

Ver [Sincronização](cmdb-sincronizacao.md), [Limitações](cmdb-limitacoes.md) e [Segurança](cmdb-seguranca.md).
