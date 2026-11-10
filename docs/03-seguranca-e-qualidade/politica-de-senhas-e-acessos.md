# Gestão de acessos e ciclo de senhas

## Escopo e estado operacional

Código 1.4.0, conferido em `autenticacao/models.py`, `security.py`, `views.py`, migrations e comandos. Login usa Django Auth por **usuário e senha**; e-mail corporativo é dado da conta e canal de compartilhamento. Não existe SSO Microsoft/Entra ID ativo. Groups/Permissions e `GroupDataScope` continuam independentes da política de senha.

A operação informou que a política de 90 dias já foi ativada no servidor conhecido. Seu estado atual não foi consultado nesta auditoria. Não reexecutar ativação global para atualizar documentação ou implantar código. `PasswordPolicy` nasce inativa na migration; isso não significa que uma instalação existente esteja inativa.

## Contas novas e convite de 48 horas

1. Em Configurações → Usuários, administrador autorizado cadastra usuário, e-mail e grupo. A criação exige `manage_users` **e** `create_users`; usa senha inutilizável, sem senha inicial compartilhada.
2. Revisar o escopo **do grupo**. Não existe um segundo escopo individual atual; alterar o grupo ou seu escopo afeta os integrantes correspondentes.
3. Gerar convite individual pelo detalhe do usuário. A ação exige `generate_access_invites`, conta ativa e primeiro acesso ainda não concluído. Consultar o detalhe exige `manage_users` e `view_access_status`.
4. Copiar o link exibido uma única vez e encaminhá-lo manualmente ao destinatário por canal corporativo seguro. Gerar convite **não envia e-mail automaticamente**, nem depende de Brevo.
5. O usuário abre `/ativacao/`, define nova senha e confirmação. O token válido permite identificar o destinatário; o GET isolado não ativa a conta. A gravação ocorre por POST com CSRF.
6. O consumo registra senha, `password_changed_at`, primeiro acesso e auditoria. O usuário entra pelo login normal; não recebe grupos adicionais pela ativação.

`issue_access_token` calcula `expires_at = now + 48 horas` **no momento da emissão**, não na abertura do link. No instante da expiração, o link já é inválido. É de uso único: consumo, revogação, expiração, finalidade incorreta e conta inativa impedem uso. Reemitir usa a mesma ação de geração, revoga tokens ainda não usados da mesma finalidade e reinicia as 48 horas do novo link; não prolonga o anterior. Recarregar a página de emissão não recupera o token: emitir novamente quando necessário. Revogar convite exige `revoke_access_invites` e não desativa a conta; desativação é ação própria.

O banco conserva somente hash SHA-256 do token aleatório. A URL usa `ARION_PUBLIC_BASE_URL` e fragmento; o JS remove o fragmento do endereço e envia o token em POST. A página de emissão não tem recursos externos e usa no-referrer; ativação/redefinição usam same-origin para preservar CSRF. Nunca copiar link/token para Git, auditoria, relatório, captura de tela ou chat de suporte.

## Senhas e prazos distintos

| Regra | Implementação |
|---|---|
| Convite e link administrativo | 48 **horas** desde cada emissão; uso único |
| Renovação da senha | Padrão de 90 **dias completos** desde `password_changed_at`; prazo em `PasswordPolicy.max_age_days` |
| Validação | Mínimo 8 caracteres, similaridade com usuário, senhas comuns e senha inteiramente numérica pelos validadores Django |
| Nova senha | Confirmação igual e proibição de repetir a senha atual |
| Sessão | Prazo de sessão é independente; não há configuração de sessão de 8h neste checkout |

Não são 48 dias nem 90 dias de convite. Não existe exigência adicional inventada de caracteres especiais nem consulta externa de senhas comprometidas.

Troca voluntária em `/configuracoes/seguranca/` exige senha atual, nova senha e confirmação; atualiza a data e mantém a sessão corrente por `update_session_auth_hash`. Outras sessões deixam de validar o hash antigo ao serem usadas. Troca obrigatória em `/senha/obrigatoria/` ocorre após autenticar com a senha atual, exige nova senha e confirmação **sem OTP** e encerra a sessão ao concluir, retornando ao login. Redefinição por link também altera o hash; não preserva sessões antigas nem altera permissões/escopo.

## Contas anteriores e bloqueio backend

`autenticacao.0001_initial` criou perfis para contas preexistentes e marcou troca inicial sem alterar hashes. `0002_reconcile_legacy_security_profiles` corrige apenas perfis com motivo `initial`, primeiro acesso ausente, login registrado e senha utilizável; preserva a data de senha desconhecida. A criação normal de uma conta por convite deixa primeiro acesso pendente e senha inutilizável. A tela administrativa distingue **Aguardando ativação/Convite expirado** de **Troca obrigatória/Senha expirada**. A ação individual de exigir troca recusa conta ainda não ativada, orientando usar convite.

`password_change_required` verifica cada requisição autenticada: contas de serviço explicitamente marcadas são excluídas; exigência administrativa vale mesmo sem política global ativa; com política ativa, flag pendente, data desconhecida ou vencimento bloqueiam módulos. Não há exceção automática para superuser humano.

O middleware protege também sessões anteriores, APIs e exportações. HTML redireciona à troca obrigatória; API retorna 403 `PASSWORD_CHANGE_REQUIRED`. Login, logout, ativação, redefinição, troca obrigatória e estáticos permanecem liberados para o fluxo de recuperação. O vencimento não executa logout em lote ou cron: bloqueia na próxima requisição; o logout explícito ocorre ao concluir a troca obrigatória.

## Recuperação e Brevo

Administrador com `generate_password_reset_links` emite link de 48h para conta ativa com primeiro acesso concluído. A finalidade é distinta de convite e a reemissão revoga apenas tokens da mesma finalidade. Compartilhar manualmente pelo canal corporativo. Não existe recuperação pública autônoma por e-mail nesta versão.

O código conserva infraestrutura OTP de 10 minutos e envio por Brevo para fluxos específicos, mas a rota atual de renovação obrigatória **não a utiliza**. Não exigir configurar/enviar OTP como pré-requisito dessa rota. Brevo exige integração habilitada, autorização de envio real e remetente/credenciais protegidos quando um fluxo efetivamente o consumir.

Recuperação local de administrador ativo já ativado, por operador autorizado do servidor, usa o comando existente abaixo. **Operação administrativa sensível:** grava token e auditoria e imprime URL secreta; não usar transcript/log compartilhado. O ator precisa ser administrador ativo e pode ser o próprio destinatário quando autorizado; a conta de destino também deve ser administradora, não serviço, e o e-mail precisa coincidir.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py emitir_link_redefinicao_admin --username <login-destino> --actor-username <login-admin> --confirm-email <email-confirmado>
```

Conta inativa requer revisão/reativação administrativa antes de emissão; nunca criar senha mestra, contornar CSRF ou marcar primeiro acesso manualmente para “destravar”.

## Comando de ativação global

`ativar_politica_senhas` é procedimento crítico de rollout, **não rotina diária nem passo padrão do deploy**. Sem `--confirm`, apenas simula. Não foi executado nesta auditoria.

| Parâmetro | Efeito |
|---|---|
| `--confirm` | Ativa política, marca perfis e grava auditoria |
| `--actor-username` | Obrigatório na confirmação; administrador ativo responsável |
| `--max-age-days` | De 1 a 3650; padrão inicial 90, preserva valor existente quando omitido |
| `--exclude-usernames` | Logins existentes explicitamente classificados como serviço; remove exigência interativa |
| `--include-usernames` | Reclassifica serviços como humanos e exige troca inicial |

A primeira ativação fixa `activated_at`. O comando seleciona contas com `date_joined <= activated_at`, incluindo logins explicitamente informados por inclusão/exclusão; não seleciona só contas humanas ativas. Repetição preserva o corte, mas pode remarcar quem não trocou senha após ele ou tem flags pendentes. A simulação mostra coorte/exclusões, não uma previsão exata de quantas contas terão flags alteradas.

**Limite confirmado:** o loop do comando não filtra senha inutilizável/primeiro acesso pendente antes de marcar a coorte. Uma conta por convite anterior ao corte, ou explicitamente incluída, pode ser classificada incorretamente. Não usar o comando para regularizar convites nem como mecanismo de reemissão. Antes de um novo rollout, conferir a coorte, separar contas não ativadas e homologar uma proteção específica em tarefa de código. A ação administrativa individual já possui a proteção apropriada. Nesta auditoria não houve mudança do comando ou dos usuários.

Pré-requisitos de uma ativação excepcional: autorização de segurança, consulta do estado atual, backup MySQL/restore verificado, administrador de recuperação ativo, origens HTTPS corretas e revisão de toda a coorte/serviços. `--exclude-usernames` não é filtro genérico de exclusão: transforma a conta em serviço e não deve ser usado para esconder convites pendentes. O [guia administrativo](administracao-operacional.md) mostra consulta sem reativação. Não há comando documentado de desativação/rollback automático da política; recuperar individualmente e avaliar o caso com o responsável.

## Auditoria e referências

Criação, emissão/revogação/consumo, ativação, redefinição, exigência/troca de senha e alterações de grupo/escopo usam `AuditService`. Registrar ator, objeto e resultado; nunca senha, token, código ou URL secreta. Permissões administrativas e os grupos provisionados estão em [Usuários, Grupos e Escopos](../05-funcionalidades/usuarios-grupos-e-escopos.md). Procedimentos de host/HTTPS/DPAPI: [Implantação e Produção](../02-arquitetura/implantacao-e-producao.md).
