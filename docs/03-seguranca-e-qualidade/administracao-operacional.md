# Administração operacional do ARION OPS

## Tipos de operação

**Consulta** lê estado/dados; **administrativa** altera conta, grants, escopo, decisão ou configuração; **crítica** pode afetar disponibilidade, credenciais, banco ou serviços e requer autorização, backup e validação compatíveis com o impacto. Os exemplos são procedimentos futuros no host correto, não comandos executados por esta auditoria.

## Contas e acessos

| Necessidade | Procedimento na Central | Tipo e controle |
|---|---|---|
| Criar conta | Usuários → Novo; informar usuário, e-mail corporativo, nome e grupo; revisar escopo do grupo antes de emitir link | Administrativa; `manage_users` + `create_users`; sem senha inicial |
| Gerar primeiro acesso | Detalhe → Gerar convite; copiar uma vez e encaminhar pelo canal corporativo | Administrativa sensível; `generate_access_invites`; 48h desde emissão, sem e-mail automático |
| Reemitir expirado/perdido | Gerar novo convite para conta ativa ainda não ativada | Administrativa; revoga anterior da mesma finalidade; nunca recuperar token do banco |
| Revogar convite | Detalhe → Revogar | Administrativa; `revoke_access_invites`; não desativa usuário |
| Alterar grupo | Editar usuário → perfil de acesso | Administrativa; `manage_users`; afeta permissões e escopo; revisar multi-grupos antes de substituí-los |
| Alterar permissões | Grupos e acessos → editar grupo | Administrativa; `manage_groups`; conferir alterações para todos os integrantes |
| Alterar carteira | Escopos → grupo → ALL ou CUSTOM com cliente/operadora E projeto | Administrativa; `manage_data_scopes`; salvar e verificar com usuário representativo |
| Desativar conta | Editar usuário → conta inativa | Administrativa; impede login/uso autenticado; último administrador ativo tem proteção |
| Recuperar senha | Conta ativa já ativada → gerar link de redefinição | Administrativa sensível; `generate_password_reset_links`; compartilhar manualmente, sem alterar grants |
| Exigir troca individual | Detalhe → exigir troca | Administrativa sensível; `require_password_change`; recusa serviço e convite pendente |
| Consultar situação | Lista/detalhe de Usuários | Consulta; `view_access_status` e permissões de navegação; situação de senha não é `is_active` |
| Consultar auditoria | Configurações → Auditoria; filtrar ator, módulo e ação | Consulta administrativa; `view_audit_log`; não copiar metadata pessoal desnecessária |

O detalhe exige `manage_users` e `view_access_status`. Ações POST validam suas permissions próprias; navegação oculta não substitui proteção. Revisar [matriz de permissões e limites de escopo](../05-funcionalidades/usuarios-grupos-e-escopos.md), especialmente Relatórios e contatos da Qualidade, antes de delegar acesso.

Auditoria permite exportar CSV pela lista com `?export=csv`, preservando filtros e exigindo `view_audit_log`. Essa exportação grava um evento `AUDIT_EXPORT`; não altera os eventos exportados. Não usar a subrota inexistente `/auditoria/exportar/` nem executar exportação real somente para esta revisão.

## Consultar a política sem reativá-la

A operação informou ativação prévia no servidor; conferir o estado efetivo. O comando abaixo **somente consulta** flags e contagens, sem hashes, tokens, nomes ou e-mails. Executar pelo Python do checkout/banco autorizado. Não usar `PasswordPolicy.current()` ou métodos que criem perfis para uma consulta.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py shell -c "from aplicativos.autenticacao.models import PasswordPolicy, UserSecurityProfile as P; p=PasswordPolicy.objects.filter(pk=1).values('active','activated_at','max_age_days').first(); print(p); print({'flags_pendentes':P.objects.filter(must_change_password=True,is_service_account=False).count(),'primeiro_acesso_pendente':P.objects.filter(first_activation_completed_at__isnull=True,is_service_account=False).count(),'servicos':P.objects.filter(is_service_account=True).count()})"
```

Contagem de flags não equivale ao total vencido: datas vencidas sem flag ainda persistida são avaliadas no acesso. Para investigar contas específicas, usar a lista administrativa autorizada, que calcula a situação. Não publicar listagem nominal nem perfil/hash completo.

Não executar `ativar_politica_senhas --confirm` para consultar ou corrigir convite. O comando global tem coorte de rollout e um limite de classificação de contas não ativadas descrito na [política de senhas](politica-de-senhas-e-acessos.md). Para recuperação local de administrador, seguir o comando sensível desse documento, sem registrar o link em transcript.

## Integrações e freshness

**Consulta:** Configurações → Integrações. Conferir integração habilitada, saúde de conexão, último teste, último sucesso FULL/delta, contagens e próxima tentativa. OTRS/CMDB indisponível conserva a leitura anterior; sucesso do teste não comprova espelho atualizado. Analisar run e cursor antes de reiniciar processo.

**Administrativa:** alterar configuração/credenciais requer `manage_integrations`; testar requer `run_integrations`; inspecionar fonte requer `inspect_integrations`, conforme rota. Um teste de conexão é leitura externa; teste de roteamento Brevo é **envio real** separado e precisa de confirmação/autorização específicas. Não clicar em envio TQ ou roteamento apenas para verificar que o serviço existe.

**Crítica:** sincronização manual lê fontes reais e altera espelhos/estado local. Consultar [Worker](../02-arquitetura/worker-e-sincronizacao.md), evitar concorrência com worker ativo e respeitar lease. Não criar pedidos de Cancelamentos para “completar” o backlog: projeção cria somente triagem, e a admissão é humana.

## Serviços, logs e falhas

Na raiz confirmada do Windows Server, as consultas usuais são:

```powershell
.\deploy\windows\status-arion.ps1
.\deploy\windows\cloudflare\status-cloudflared.ps1
git rev-parse HEAD
git status --short
.venv\Scripts\python.exe arion_geo\gerenciar.py showmigrations --plan
Get-Content -LiteralPath .\var\logs\arion-web.log -Tail 80
Get-Content -LiteralPath .\var\logs\arion-worker.log -Tail 80
```

Consultar Sobre / Atualizações e `/api/version/` no domínio já validado; versão do Git não prova processo reiniciado. Logs de erro podem ter identificador `REQ-...`, útil para correlação; não publicar stack trace, payload ou segredos. O status deve confirmar supervisor e filho, não apenas um PID existente.

| Sintoma | Consulta e próximo passo |
|---|---|
| Web indisponível | Status/PIDs, log Web, ambiente/manifest/migrations; se loopback saudável e domínio falha, investigar Tunnel/DNS |
| Worker vivo, dados antigos | `IntegrationRun`, último sucesso FULL, cursor/backoff e credencial recuperável pela identidade do worker |
| Login volta à renovação | Situação da conta, `PasswordPolicy`, expiração e primeiro acesso; não reativar política global |
| Sem dados com CUSTOM | Conferir cliente E projeto do grupo, link atual e aliases autorizados; ALL não concede permission |
| TLS/conexão falha | Erro sanitizado e configuração; não desabilitar TLS/CSRF para “resolver” |
| Falha de envio TQ | Conferir tentativa/resultado incerto e checkpoint QID; não reenviar às cegas |

Reiniciar/parar/iniciar é operação **crítica**: validar causa e autorização, usar scripts ARION por papel. Não matar processos por nome. O guia de [Implantação e Produção](../02-arquitetura/implantacao-e-producao.md) contém ordem Web/Worker, identidades DPAPI e recuperação.

## Backup e recuperação

Backup manual, alteração de retenção, restore e deploy são **críticos**. `backup-arion.ps1` grava dump e pode apagar backups vencidos se a retenção não for zero. O corte usa `-RetentionDays 0`, destino externo privado, checksum, storage/configuração separados e restore ensaiado. Nenhum backup é garantia de recuperação sem teste do conteúdo.

Restaurar serviço por reinício não é restaurar dados. Se houve mudança de schema/importação, registrar e preservar o estado antes de decidir restore, considerando trabalho posterior ao backup. Não usar `rollback_banco_arion.py` como restore de produção. Procedimentos: [guia do servidor](../02-arquitetura/implantacao-e-producao.md), [checklist 1.4.0](../08-releases/v1.4.0-implantacao-windows.md) e [carga histórica](../06-integracoes-e-dados/arionbroker/carga-producao.md).
