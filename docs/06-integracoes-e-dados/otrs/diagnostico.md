# Diagnóstico OTRS

Use evidências locais de integração: estado do conector, último IntegrationRun, IntegrationSyncState, IntegrationLease, logs sanitizados e contagens do espelho. Não copie credenciais, SQL com valores, corpos de artigo ou títulos para tickets de suporte.

| Sintoma | Verificações seguras | Interpretação e próximo passo |
|---|---|---|
| Falha de conexão | Conferir integração habilitada/configurada, código seguro de erro, endpoint e porta na configuração protegida; testar conectividade pela operação autorizada existente | Diferenciar DNS/rota/timeout de erro de aplicação; não imprimir senha |
| Falha TLS | Conferir TLS habilitado, CA existente/formato PEM e certificado confiável; revisar erro tls_* | Host remoto exige TLS. hostname verification depende de CA customizada no código atual |
| Credencial inválida ou descriptografia falha | Conferir identidade de execução Windows, estado da proteção e erro tipado | DPAPI é por usuário Windows; não testar copiando segredo para log |
| FULL falha/incompleta | Ler IntegrationRun e status seguro; checar cobertura de filas, schema obrigatório e duplicidade de IDs/TN | Erro dentro da reconciliação cancela a escrita local; snapshot anterior permanece |
| FAST sem cursor | Consultar IntegrationSyncState.change_cursor e última FULL | Fazer FULL com cobertura atual antes de FAST; FAST não inicializa cursor |
| Worker ausente ou parado | Verificar processo via supervisor do ARION, lease do worker, última execução e logs do processo | Lease evita scheduler duplicado; lease expirado permite nova tentativa conforme cadência |
| Ticket não atualiza | Conferir queue atual no mirror, is_present_in_queue, source_changed_at, last sync e assinatura de filas | Ticket fora das quatro filas pode não pertencer ao conjunto atual; falha não deve ser confundida com fila vazia |
| TN duplicado ou ID conflitante | Ler resumo de erro da execução e verificar unicidade apenas no espelho local autorizado | Serviço cancela antes de reconciliar quando ID/TN não é único; não escolher linha manualmente sem validar origem |
| CI sem Link local | Ver OTRSTicketConfigItem, config_item_id e mirror CMDB/flag de presença | Relação pode ser válida no OTRS, mas ConfigItem não corresponde a Link CMDB atual; fica sem FK de Link |
| Situação OTRS divergente | Comparar external_state_name, external_state_type, business_status, OTRSStateMapping e regras em otrs_status.py | Nome bruto é preservado; categoria consolida vários nomes; UNKNOWN requer revisão do mapeamento |
| Data de resolução ausente | Verificar ticketResolvido como Dynamic Field Ticket e value_date; olhar terminal_state_at separadamente | resolved_at pode ser nulo; terminal_state_at é fallback condicionado ao estado atual e histórico |
| TQ sem correlação | Conferir fila configurada, checkpoint por fila, last_article_id, artigo mais recente e status seguro do vínculo | QID faltante/duplicado ou conflito não vincula automaticamente; artigo não é salvo no mirror |
| TQ com envio incerto | Usar procedimento de reconciliação manual previsto na tela, TN e referência de evidência | Um artigo único com QID completo em fila permitida comprova vínculo; sem evidência, não liberar reenvio |

## Componentes de referência

- Cliente/TLS e erros: integracoes/clients/otrs.py, services/errors.py.
- Sync: services/otrs_sync.py, comandos sincronizar_otrs.py e integration_worker.py.
- Estado/cursor/lease: IntegrationSyncState, IntegrationRun, IntegrationLease e services/sync_lock.py.
- Status/data: services/otrs_status.py, services/sync_common.py.
- Ticket-CI: services/otrs_sync.py e models.py.
- QID: qualidade/services/tq_otrs_correlation.py e tq_correlation.py.

Evite alterar cursor, executar queries arbitrárias, apagar mirror, ampliar grants ou repetir envio para “ver se funciona”. Registre o erro seguro e valide o próximo passo pelo procedimento operacional aprovado.
