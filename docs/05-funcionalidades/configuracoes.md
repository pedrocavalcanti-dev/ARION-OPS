# Configurações

## Visão geral

A Central de Configurações concentra preferências da conta e administração de acesso, escopos e auditoria. É destinada a usuários autenticados; cada área administrativa também exige a permission correspondente no backend. O menu organiza áreas de Conta e Administração. `/admin/` do Django permanece voltado à manutenção técnica.

## Áreas e permissões

| Área | Permission | Finalidade |
|---|---|---|
| Minha conta e Segurança | Login obrigatório | Consultar/alterar dados da própria conta e senha. |
| Aparência | Login obrigatório | Consultar e persistir preferência de tema. |
| Usuários | `manage_users` | Criar, editar, ativar/desativar contas e atribuir perfil. |
| Grupos e acessos | `manage_groups` | Administrar grupos e suas Django Permissions. |
| Escopos de dados | `manage_data_scopes` | Configurar `GroupDataScope` por grupo. |
| Auditoria | `view_audit_log` | Consultar e exportar eventos. |

O acesso não depende apenas de ocultar itens do menu: rotas verificam autenticação e permission. Usuário autenticado sem permission recebe 403 real. A camada global de erros fornece a resposta HTML; detalhes estão em [Tratamento Global de Erros](tratamento-global-de-erros.md).

## URLs e componentes

| Área | URL base | Observação |
|---|---|---|
| Central | `/configuracoes/` | Preferências da conta. |
| Usuários, grupos, escopos e auditoria | `/configuracoes/administracao/` | Rotas administrativas protegidas por permission. |
| Tema | `/configuracoes/theme/` | POST JSON para preferência de tema. |

`aplicativos.configuracoes.views` implementa as views; `access_required` protege operações administrativas; `UserPreference` persiste preferências; `AuditService` registra alterações relevantes. A estrutura de grupos e escopos está descrita em [Usuários, Grupos e Escopos](usuarios-grupos-e-escopos.md), e eventos em [Auditoria](auditoria.md).

### Testes e estado

Os testes de `aplicativos.configuracoes` cobrem acesso, permissões, gestão de usuários/grupos/escopos, preferências e respostas negadas. As páginas administrativas e preferências estão implementadas; integrações externas ainda são áreas distintas e não são configuradas por esta central.
