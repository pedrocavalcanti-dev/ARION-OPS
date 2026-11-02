# Central de Integrações

## Papel

A Central administra configuração dos conectores CMDB/OTRS e mostra saúde/freshness dos mirrors: última execução, tipo, duração, resultado e contagens. Não é cliente de consulta remota por request; sincronização roda no worker separado.

## Conectores e estado

- **CMDB ServiceUp:** GenericInterface Search/Get para classes `Links de internet` e `Operadoras`, destino local `CMDBLinkMirror` e `CMDBOperatorMirror`.
- **OTRS MySQL:** conexão read-only às filas `NOC - ATACADO`, `NOC - CANCELAMENTOS NGO`, `NOC - RETIRA EQUIPAMENTO` e `NOC – PENDENTE PAGAMENTO` (travessão no nome real), com estados, relações e Dynamic Fields selecionados; destino `OTRSTicket`, `OTRSTicketConfigItem` e `OTRSStateMapping`. O conjunto atende aos universos do Cockpit. `OTRS_NOC_ATACADO_QUEUE_URL` é um deep link da interface web, não um filtro de sincronização.

`Integration` guarda configuração protegida/status. `IntegrationRun` guarda tentativas sanitizadas. `IntegrationSyncState` persiste cursor, sucesso/último FULL e `IntegrationLease` coordena processos. Segredo aparece mascarado, nunca em response/API.

Cadências: OTRS FAST 2 min; OTRS FULL 30 min; CMDB FULL 10 min; retry 2 min. O FULL OTRS reconcilia o conjunto de filas selecionado e preserva tickets de filas fora dele. O FAST compartilhado exige o conjunto completo de quatro filas e um FULL bem-sucedido com essa cobertura; mudança de cobertura agenda novo FULL. Ver [Worker](..\02-arquitetura\worker-e-sincronizacao.md), [OTRS](..\06-integracoes-e-dados\otrs.md) e [CMDB](..\06-integracoes-e-dados\cmdb.md).

## Diagnóstico

Leia resultado e último sucesso em conjunto: attempt FAILED recente não torna mirror fresco. Confira configuração/conectividade, último FAST/FULL, cursor e lease. OTRS depende da resolução exata de cada nome de fila selecionado; CMDB precisa resposta completa Search/Get. FULL valida o conjunto antes de marcar ausências; falha conserva o último snapshot. A tela do Cockpit distingue cobertura FULL ainda não aplicada de uma sincronização bem-sucedida sem tickets (zero real).

Segredos locais DPAPI dependem do usuário Windows de execução. Em outra conta ou máquina, cadastre de novo sem copiar credencial em ticket de suporte.

## Limites

Chamados não atualiza OTRS e Base não edita CMDB. Importador XLSX antigo não é uma integração oficial ativa. Runs não devem armazenar payload de ticket/credenciais. Permission de integração não substitui Data Scope dos dados operacionais.
