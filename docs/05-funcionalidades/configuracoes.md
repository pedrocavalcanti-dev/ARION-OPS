# Configurações

## Visão geral

A Central de Configurações concentra preferências da conta e administração de acesso, escopos e auditoria. É destinada a usuários autenticados; cada área administrativa também exige a permission correspondente no backend. O menu organiza áreas de Conta e Administração. `/admin/` do Django permanece voltado à manutenção técnica.

## Áreas e permissões

| Área | Permission | Finalidade |
|---|---|---|
| Minha conta e Segurança | Login obrigatório | Consultar/alterar dados da própria conta e senha. |
| Aparência | Login obrigatório | Consultar e persistir preferência de tema. |
| Usuários | `manage_users`, `create_users`, permissions de convite/redefinição e `view_access_status` | Criar contas sem senha, atribuir perfil e escopo, emitir/revogar links e consultar o estado de acesso. |
| Grupos e acessos | `manage_groups` | Administrar grupos e suas Django Permissions. |
| Escopos de dados | `manage_data_scopes` | Configurar `GroupDataScope` por grupo. |
| Auditoria | `view_audit_log` | Consultar lista/detalhe e exportar CSV pela mesma lista com `?export=csv`; não existe rota `/auditoria/exportar/`. |

O acesso não depende apenas de ocultar itens do menu: rotas verificam autenticação e permission. Usuário autenticado sem permission recebe 403 real. A camada global de erros fornece a resposta HTML; detalhes estão em [Tratamento Global de Erros](tratamento-global-de-erros.md).

O escopo é atribuído ao grupo, não editado individualmente no cadastro do usuário. Detalhe de acesso exige `manage_users` e `view_access_status`; geração/revogação de convite e redefinição têm permissions próprias. A [administração operacional](../03-seguranca-e-qualidade/administracao-operacional.md) organiza os procedimentos diários e distingue consulta de alteração crítica.

## URLs e componentes

| Área | URL base | Observação |
|---|---|---|
| Central | `/configuracoes/` | Preferências da conta. |
| Usuários, grupos, escopos e auditoria | `/configuracoes/administracao/` | Rotas administrativas protegidas por permission. |
| Tema | `/configuracoes/theme/` | POST JSON para preferência de tema. |

`aplicativos.configuracoes.views` implementa as views; `access_required` protege operações administrativas; `UserPreference` persiste preferências; `AuditService` registra alterações relevantes. Cadastro de usuários não define senhas: links de primeiro acesso são gerados após salvar e encaminhados manualmente, com validade de 48 horas. Política de troca inicial, OTP Brevo, renovação de 90 dias, permissions e implantação controlada estão descritos em [Gestão de acessos e ciclo de senhas](../03-seguranca-e-qualidade/politica-de-senhas-e-acessos.md). A estrutura de grupos e escopos está descrita em [Usuários, Grupos e Escopos](usuarios-grupos-e-escopos.md), e eventos em [Auditoria](auditoria.md).

### Testes e estado

Os testes de `aplicativos.configuracoes` cobrem acesso, permissões, gestão de usuários/grupos/escopos, preferências e respostas negadas. As páginas administrativas e preferências estão implementadas; integrações externas ainda são áreas distintas e não são configuradas por esta central.
