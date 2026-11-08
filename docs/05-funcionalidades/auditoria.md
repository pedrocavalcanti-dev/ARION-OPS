# Auditoria

## Finalidade e modelo

A Auditoria registra eventos relevantes de autenticação, administração e exportação para rastreabilidade. Não é log de depuração: exceções HTTP, request IDs e diagnósticos técnicos seguem o logging global, não criam automaticamente `AuditEvent`.

`AuditEvent` armazena timestamp, usuário opcional, categoria, ação, resultado, módulo, rótulo/tipo/ID do objeto, descrição, IP, User-Agent e `metadata` JSON. A listagem não oferece edição ou exclusão.

## Eventos registrados

Eventos confirmados incluem `LOGIN_SUCCESS`, `LOGIN_FAILED` (sem senha), `LOGOUT`; `ACCOUNT_UPDATED`, `PASSWORD_CHANGED`, `PASSWORD_POLICY_ACTIVATED`, `PASSWORD_VERIFICATION_CODE_SENT`, `PASSWORD_VERIFICATION_CODE_VALIDATED`, `PASSWORD_VERIFICATION_CODE_INVALID`, `PASSWORD_CHANGED_INITIAL`, `PASSWORD_RENEWED`; criação, emissão, revogação e consumo de convites; emissão de link administrativo e troca obrigatória; `USER_CREATED`, `USER_UPDATED`, `GROUP_CHANGED`, `USER_ACTIVATED`, `USER_DEACTIVATED`, `PERMISSIONS_CHANGED`, `DATA_SCOPE_CHANGED`; `CMDB_IMPORT`; `SAVED_VIEW_CREATED`, `SAVED_VIEW_UPDATED`, `SAVED_VIEW_DELETED`; exportações Cockpit `EXPORT_CSV`, `EXPORT_XLSX`, `EXPORT_PDF`; e `AUDIT_EXPORT`.

Busca, filtros, paginação, navegação e abertura de detalhes não geram eventos. A exportação da própria Auditoria registra `AUDIT_EXPORT` depois de preparar o arquivo; não registra o conteúdo exportado.

## Sanitização e acesso

`AuditService.record()` centraliza criação, ator e contexto da requisição e sanitiza recursivamente metadata com nomes de campos sensíveis, incluindo senha, token, cookie, secret e credenciais. A UI exige `view_audit_log`; usuários comuns veem a listagem autorizada pela permission. Os eventos não são filtrados pelo Data Scope operacional: esse escopo restringe registros CMDB, não o histórico de auditoria. A página permite filtrar por período, usuário, categoria, ação, resultado e módulo; tabela e detalhe apresentam os campos e metadata registrados.

O fluxo de acesso nunca grava senha, código OTP completo, token de convite/redefinição, URL secreta ou credencial Brevo. Consulte [Gestão de acessos e ciclo de senhas](../03-seguranca-e-qualidade/politica-de-senhas-e-acessos.md) para o conjunto de eventos de segurança e procedimento de rollout.

## URLs e componentes

| Endpoint | Método | Finalidade |
|---|---|---|
| `/configuracoes/administracao/auditoria/` | GET | Lista eventos paginados e indicadores do recorte. |
| `/configuracoes/administracao/auditoria/exportar/` | GET | Exporta CSV do recorte filtrado e registra `AUDIT_EXPORT`. |

`AuditService` é o serviço central de gravação; `AuditEvent` é o model; as views administrativas em `aplicativos.configuracoes.views` consultam e exportam. As rotas exigem login e `view_audit_log`. Erros técnicos e `X-Request-ID` são tratados pela arquitetura global, não pela tabela de auditoria; veja [Tratamento Global de Erros](tratamento-global-de-erros.md).

## Desempenho, testes e estado

A consulta é paginada no servidor e os filtros são aplicados no banco. Campos de timestamp, categoria, ação, resultado e módulo possuem índices no model. Testes cobrem sanitização, sinais de autenticação, alterações administrativas, exports e acesso. Não há retenção automática ou integração SIEM implementada.
