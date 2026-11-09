# Gestão de acessos e ciclo de senhas

## Escopo

O ARION OPS continua usando o usuário padrão do Django, sessões Django, Groups/Permissions e `GroupDataScope`. A política acrescenta estado de segurança por usuário, links de uso único e um middleware de bloqueio; não substitui autenticação nem altera grupos, permissões ou escopos existentes.

## Usuários novos

O formulário administrativo exige login, e-mail e perfil de acesso, não possui campos de senha e grava uma senha inutilizável. Depois de salvar os dados, a administração emite o convite pela página do usuário. A URL é montada a partir de `ARION_PUBLIC_BASE_URL`, nunca do cabeçalho Host recebido.

O convite vale 48 horas, pode ser usado uma vez e guarda somente SHA-256 do token aleatório de 256 bits. A emissão de outro convite revoga os anteriores da mesma finalidade. A página de emissão, acessível somente após autorização no backend, mostra o link em um campo de cópia. O token fica apenas no fragmento da URL final (`#...`), que não é enviado ao servidor, e não é persistido em sessão, banco, path ou log. A página é isolada, sem recursos externos e com `Referrer-Policy: no-referrer`; ao fechá-la ou recarregá-la, o token original não pode ser recuperado. Uma nova emissão gera outro token e invalida o anterior. A administração copia e encaminha o link manualmente pelo e-mail corporativo. A ativação não pode tornar uma conta inativa ativa.

## Troca inicial, renovação e bloqueio

A migration `autenticacao.0001_initial` cria um `UserSecurityProfile` para cada conta preexistente e marca a troca inicial como pendente, sem tocar no hash atual. A política global nasce desligada. Enquanto ela estiver desligada, essa marcação inicial não bloqueia acessos. O comando local `ativar_politica_senhas` ativa o bloqueio apenas depois das verificações de rollout e permite identificar contas de serviço não interativas.

Quando ativada, a política bloqueia acessos de contas pendentes até que a nova senha seja definida. O fluxo de troca obrigatória direciona o usuário autenticado diretamente para a tela de atualização de senha pessoal (com nova senha e confirmação, mínimo de 8 caracteres, sem repetição da senha atual e validação backend do Django), sem solicitar código por e-mail ou OTP, visto que a identidade já foi autenticada pelas credenciais atuais. A infraestrutura de desafios e validação de OTP via Brevo permanece disponível para fluxos específicos de segurança que a necessitem.

O middleware verifica cada requisição autenticada. Contas pendentes não chegam a views, APIs, exportações, páginas do admin ou módulos; APIs recebem JSON 403 com `PASSWORD_CHANGE_REQUIRED`, e páginas HTML seguem para o fluxo obrigatório. Login, logout, ativação, redefinição e arquivos estáticos necessários continuam acessíveis. A troca atualiza o hash Django e invalida sessões antigas pela verificação de hash de sessão; o usuário precisa autenticar novamente.

A validade inicial é de 90 dias completos desde a última definição de senha, calculados com `timezone.now()` e datetimes com fuso. O prazo fica em `PasswordPolicy.max_age_days` e pode ser ajustado pelo comando local. As senhas novas precisam ter pelo menos 8 caracteres, passam pelos validadores Django de similaridade, lista de senhas comuns e conteúdo numérico, e não podem ser iguais à senha atual. Não há consulta externa de senhas comprometidas.

## Brevo e recuperação

O envio de códigos usa a integração Brevo já registrada em Integrações. É necessário que ela esteja habilitada, que o envio real esteja autorizado, que o método selecionado (SMTP Relay ou API REST) e suas credenciais protegidas estejam válidos, e que `sender_email` esteja configurado. O código sempre vai para o e-mail da conta, sem cópia ou Reply-To configurados para outras rotinas.

Administradores com as permissões correspondentes podem emitir links individuais de redefinição, válidos por 48 horas e independentes do Brevo. O link altera somente a senha: grupos, permissões e Data Scope permanecem iguais. Para recuperar o único administrador quando o fluxo normal não estiver disponível, o comando local `emitir_link_redefinicao_admin` exige um ator administrador, uma conta de destino ativa também administradora e confirmação do e-mail corporativo cadastrado. O comando emite token único, registra auditoria e não cria senha mestra nem bypass permanente.

## Permissões administrativas

Além de `manage_users`, cada operação sensível tem permission própria: `create_users`, `generate_access_invites`, `revoke_access_invites`, `generate_password_reset_links`, `require_password_change` e `view_access_status`. A proteção é aplicada no backend, independente da visibilidade dos controles.

O grupo `Gestão` é criado por migration e recebe matriz explícita de consulta: Dashboard, Cockpit, Mapa, Rankings, Base, Chamados, ARI, Qualidade e Relatórios. Não recebe permissões administrativas ou de escrita, nem `is_staff` ou `is_superuser`. Seu Data Scope inicial é personalizado e restritivo.

## Auditoria

São auditados criação da conta, emissão/revogação/consumo de convite, ativação, envio/validação/falha de código, troca inicial, renovação, emissão de link administrativo, troca obrigatória, alteração de grupo e permissões. Eventos guardam ator quando a ação parte de uma sessão, usuário afetado, resultado e metadados não secretos. Nunca são gravados senha, código completo, token, URL secreta ou credenciais Brevo.

## Configuração do domínio público

Defina `ARION_PUBLIC_BASE_URL` como a origem HTTPS pública exata, por exemplo `https://arion.example.com`, e inclua o mesmo host em `DJANGO_ALLOWED_HOSTS`. Em desenvolvimento, configure `ARION_WEB_PORT=8100` e `ARION_PUBLIC_BASE_URL=http://127.0.0.1:8100` no ambiente local. Sem origem explícita em DEV, o endereço usa `127.0.0.1` e a porta de `ARION_WEB_PORT`. Produção exige a origem HTTPS configurada; o cabeçalho Host da requisição nunca escolhe o domínio dos convites.

A página de ativação recebe o fragmento pelo JavaScript, remove-o do endereço e envia o token em POST com CSRF para validação no backend. Somente um token válido permite personalizar a saudação com o nome do destinatário. As páginas com formulário usam `Referrer-Policy: same-origin`: preservam a origem no POST local e não enviam Referer para outros domínios. Não se permite `Origin: null` nem se desabilita CSRF. A página administrativa que apenas mostra o link continua com `no-referrer`.

A migration `autenticacao.0002_reconcile_legacy_security_profiles` corrige perfis técnicos legados com senha utilizável e login registrado que foram classificados como convite pendente. Ela preserva a senha, a obrigação de troca, grupos, escopos e a data de alteração de senha desconhecida. Novas contas por convite continuam pendentes até a ativação real.

## Ativação controlada no Windows Server

Não execute esta ativação até que o checklist abaixo esteja concluído. Aplicar a migration não ativa a política: ela deixa os usuários preexistentes marcados, enquanto `PasswordPolicy.active` permanece `False`.

1. Planeje janela operacional, faça backup MySQL completo e confirme o restore disponível. Pare Web e Worker antes de substituir os arquivos.
2. Configure `ARION_PUBLIC_BASE_URL`, `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` com o domínio HTTPS público correto. Não use URL fornecida por request.
3. Identifique contas não interativas. Elas serão explicitamente excluídas por login no comando, sem excluir administradores humanos ou superusuários.
4. Confirme que existe administrador humano ativo com e-mail corporativo válido e permission de Administrador do Sistema.
5. Em Integrações → Brevo, valide remetente, método e credenciais, habilite envio real e confirme que o teste de homologação foi recebido. Não registre nem copie credenciais nos logs.
6. Atualize o código, confira o plano, aplique migrations, compile e colete os assets:

```powershell
Set-Location C:\ARION-OPS
git pull --ff-only origin main
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py migrate --plan
# Confirme o backup e o banco local MySQL antes de aplicar:
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py migrate --noinput
npm ci
npm run build:production
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py collectstatic --noinput
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py check --deploy
```

7. Reinicie Web e Worker conforme a janela aprovada e valide login, Brevo, HTTPS, domínio dos links e acesso às rotas principais. Complete no mínimo um convite e um código OTP com uma conta de teste antes da política global.
8. Faça a simulação da ativação. Substitua os exemplos pelos logins de serviço efetivamente identificados:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py ativar_politica_senhas --exclude-usernames svc_otrs svc_worker
```

9. Confirme a contagem e as exclusões com o responsável de segurança. Ative e registre o ator administrador; use 90 dias inicialmente:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py ativar_politica_senhas --confirm --actor-username admin --exclude-usernames svc_otrs svc_worker --max-age-days 90
```

10. Valide a auditoria e confirme que usuários humanos, inclusive administradores e superusuários, estão em troca obrigatória. Teste primeiro a recuperação do administrador e depois o acesso de usuário comum. Contas inativas permanecem inativas.
11. Confirme Dashboard, Cockpit, Mapa, Rankings, Base, Chamados, Qualidade, Relatórios, Configurações e APIs; confirme também que serviços excluídos continuam operando.

`ativar_politica_senhas` é idempotente: mantém a data original de ativação, o prazo atual quando nenhum novo é informado, contas já classificadas como serviço e os grupos/escopos. Sem `--confirm`, não grava dados. Use `--include-usernames` para reclassificar explicitamente uma conta de servico como humana; a simulacao mostra a contagem antes da gravacao.

### Recuperação de administrador pelo servidor local

Use apenas se Brevo e a emissão administrativa web não puderem ser usados. Abra PowerShell local autorizado, confirme a identidade do solicitante pelo canal corporativo e verifique que o e-mail digitado corresponde exatamente ao cadastrado. O responsável e a conta de destino precisam ser administradores ativos:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py emitir_link_redefinicao_admin --username admin --actor-username admin --confirm-email admin@arion.example.com
```

O comando mostra o link uma vez no terminal. Encaminhe-o por canal corporativo seguro, feche/limpe o terminal e não o salve em arquivo ou log. O link expira em 48 horas, só pode ser usado uma vez e não remove o bloqueio permanentemente.

Esta implementação prepara e documenta o rollout. Não executa atualização de servidor, ativação de política ou alteração em contas de produção.
