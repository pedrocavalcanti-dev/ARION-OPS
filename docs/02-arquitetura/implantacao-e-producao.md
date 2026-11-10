# Implantação e Produção

Referência operacional do código **1.4.0**, revisada em 10/11/2026. Produção utiliza Windows Server, MySQL e processos Web/Worker. Esta revisão inspecionou o repositório, sem acessar o servidor ou executar deploy. A versão instalada, a configuração efetiva e a política de senhas do servidor devem ser consultadas antes de qualquer intervenção.

## Ambiente conhecido e confirmação

| Item | Referência informada pela operação | Contrato do código |
|---|---|---|
| Checkout | `C:\Users\Arion\Desktop\ARION-OPS` | Scripts resolvem a raiz do checkout; não exigem esse caminho |
| Domínio | `https://nocatacadoarion.bicoai.com.br/` | `ARION_PUBLIC_BASE_URL`, hosts, CSRF e ingress precisam concordar |
| Web interno | `127.0.0.1:8100` | Bind fixo em loopback; `ARION_WEB_PORT` configurável, padrão **8000** |
| Banco | MySQL local do servidor | Produção recusa SQLite, host não local e usuário `root` |
| Inicialização | Web/Worker supervisionados | Instalador fornece tarefas `ARION-Web` e `ARION-Integration-Worker`; Cloudflared é serviço independente |

8100 e o domínio são referências da última implantação conhecida, não valores descobertos no servidor nesta auditoria. O exemplo `deploy/windows/cloudflare/config.example.yml` usa 8000 e hostname ilustrativo. Não sobrescrever a configuração instalada com esse exemplo. Conferir tarefas, conta de execução, porta e ingress no host autorizado.

## Arquitetura V1

```text
Navegador HTTPS → Cloudflare → Tunnel → cloudflared
    → Waitress em 127.0.0.1:<ARION_WEB_PORT> → Django → MySQL local
Worker supervisionado → MySQL ARION + OTRS read-only + CMDB Search/Get
Envios humanos autorizados → Brevo SMTP/API
```

OTRS/CMDB são as fontes técnicas oficiais. A base ARION também contém registros próprios que um FULL não recompõe: contas, grupos, escopos, Qualidade, Cancelamentos, financeiro, auditoria, ARI e relatórios. O Worker não é iniciado por uma request. Não usar `runserver` nem iniciar um segundo scheduler em produção.

## Estrutura persistente e dependências

| Caminho na raiz | Uso e cuidado |
|---|---|
| `.venv/` | Python 3.12 e `requirements-prod.txt`; os scripts usam o executável diretamente |
| `frontend_src/`, `package.json`, `package-lock.json` | Fontes e build Node 24, sem framework adicional |
| `arion_geo/gerenciar.py` | Comandos Django; `config.wsgi:application` é o WSGI |
| `arion_geo/static/`, `arion_geo/static/arion_build/` | Assets fonte e bundles gerados; não editar bundles |
| `var/arion-frontend-manifest.json` | Manifest padrão do build; confirmar `DJANGO_FRONTEND_MANIFEST` |
| `var/staticfiles/` | Destino padrão do `collectstatic`; confirmar `DJANGO_STATIC_ROOT` |
| `var/private_media/` | Mídia privada padrão de produção; confirmar `DJANGO_MEDIA_ROOT`, copiar e proteger separadamente do banco |
| `var/database-config.json` | Configuração local do banco com segredo protegido; confirmar `ARION_DATABASE_CONFIG` |
| `var/run/` | Locks e identidades de processos, arquivos transitórios; não restaurar PIDs como processos vivos |
| `var/logs/` | `arion-web.log`, `arion-worker.log`, logs Django e relatórios do gerenciador |
| `.env` e certificados externos | Privados, fora do Git, com ACL para a conta autorizada |
| `arion-backups/` ao lado do projeto | Destino padrão dos dumps; nunca dentro do checkout/web root |

Validar caminhos efetivos: os defaults não substituem a configuração instalada. A conta Windows autorizada precisa acessar banco, DPAPI, storage e logs. Web/Worker devem usar a mesma identidade capaz de abrir as credenciais. Executar um diagnóstico em outro usuário não prova que a tarefa agendada terá acesso. DPAPI não é transportável automaticamente entre usuários/máquinas.

## Configuração de produção

Não publicar valores secretos. Configurações relevantes:

- `ARION_ENV=production`, `DJANGO_DEBUG=false`, `DJANGO_SECRET_KEY` externa e forte.
- `ARION_PUBLIC_BASE_URL`: origem HTTPS exata; `DJANGO_ALLOWED_HOSTS`: hosts explícitos sem wildcard; `DJANGO_CSRF_TRUSTED_ORIGINS`: origens HTTPS exatas autorizadas.
- `ARION_WEB_PORT`: porta de loopback que deve coincidir com o ingress. O bind permanece `127.0.0.1`.
- `DATABASE_URL`, quando presente, tem precedência sobre a conexão **active** do arquivo local. Um banco **candidate** não é escolhido automaticamente. Não imprimir a URL, o arquivo de configuração ou o dicionário `DATABASES`.
- `ARION_DATABASE_CONFIG`, `DJANGO_STATIC_ROOT`, `DJANGO_FRONTEND_MANIFEST` e `DJANGO_MEDIA_ROOT`: confirmar os overrides e ACLs. `ARION_FRONTEND_MANIFEST_PATH` é o nome do setting Django resolvido pelo override `DJANGO_FRONTEND_MANIFEST`.
- `DJANGO_SECURE_SSL_REDIRECT` e `DJANGO_SECURE_HSTS_SECONDS`: defaults de produção são redirect ativo e HSTS **31536000 segundos**, sem preload/includeSubDomains automáticos. O checker exige pelo menos 3600 segundos; esse mínimo não é o default do setting. Conferir o valor efetivo.

O supervisor carrega `.env` da raiz com precedência para variáveis já herdadas do Windows. Alterar o arquivo sem reiniciar o processo não atualiza o ambiente em uso. O banco ativo é resolvido por `integracoes/services/arion_database.py`; não alternar a conexão durante uma revisão documental.

## Serviços, inicialização e logs

**Consulta** — na raiz confirmada do checkout:

```powershell
.\deploy\windows\status-arion.ps1
.\deploy\windows\manage-arion.ps1 -Action Status
.\deploy\windows\cloudflare\status-cloudflared.ps1
Get-ScheduledTask -TaskName 'ARION-Web','ARION-Integration-Worker'
Get-Content -LiteralPath .\var\logs\arion-web.log -Tail 80
Get-Content -LiteralPath .\var\logs\arion-worker.log -Tail 80
```

Logs podem conter contexto operacional mesmo sanitizados: consultar localmente e minimizar qualquer compartilhamento. O supervisor faz rotação dos logs. `var/run/<role>.pid`, `.launcher.pid` e `.child.pid` distinguem supervisor, launcher e Python efetivo; JSONs associados guardam criação/executável. Os scripts validam identidade para não atuar sobre PIDs reutilizados. Não usar encerramento genérico de processos Python nem apagar leases ativos.

**Crítica** — parar, iniciar e reiniciar afeta a disponibilidade e exige janela autorizada:

```powershell
.\deploy\windows\stop-arion.ps1 -Role worker
.\deploy\windows\stop-arion.ps1 -Role web
# Após a manutenção autorizada:
.\deploy\windows\start-arion.ps1 -Role web
.\deploy\windows\start-arion.ps1 -Role worker
# Reinício isolado, quando necessário:
.\deploy\windows\restart-arion.ps1 -Role worker
```

O gerenciador `deploy/windows/ARION-OPS.bat` ou `manage-arion.ps1` coordena Start/Stop/Restart/Update/Diagnostics com lock e relatório sanitizado. Start/Stop/Restart/Update exigem Administradores locais. Diagnostics cria relatório local; não altera usuários ou banco. Cloudflared é consultado, não reiniciado pelo gerenciador. Web e Worker são processos supervisionados por tarefas, não serviços Windows com nomes inventados.

`install-arion.ps1` e `install-tasks.ps1 -ServiceAccount '<conta Windows autorizada>'` são procedimentos críticos de instalação, não consultas nem passos de todo deploy. A instalação de tarefas solicita credencial local, modifica ACLs e inicia processos. Não reinstalar tarefas em operação somente para conferir status.

## Build de frontend e versão

O build reutiliza Node 24 e o lockfile: `npm ci`, seguido de `npm run build:production`. Gera bundles com fingerprint, minificação/ofuscação e manifest; Service Worker é minificado sem ofuscação. Não publica fontes legíveis de `frontend_src` nem source maps. `collectstatic` coleta CSS e assets com manifest para WhiteNoise. O build deve existir antes de inicializar Django em produção; se o checkout novo ainda não tem manifest, executar o build antes dos comandos Django de pré-validação.

`docs/08-releases/releases.json` é a fonte única da versão para HTML, `/api/version/`, PWA, Sobre e Atualizações. Git contém **1.4.0**; a tag `v1.4.0` identifica o release de código. Consultar a revisão instalada com `git rev-parse HEAD`, a página Sobre e o endpoint de versão no domínio autorizado. A publicação de um commit posterior de documentação não regrava a tag histórica.

## Processo completo de atualização

**Operação crítica futura, não executada nesta auditoria.** O [checklist 1.4.0](../08-releases/v1.4.0-implantacao-windows.md) é a lista de execução da release; este guia define os cuidados permanentes.

1. Confirmar host, raiz, commit, versão efetiva, banco MySQL, conta Windows, tarefas, porta, domínio e janela. Conferir alterações locais com `git status --short`; interromper se não estiverem reconciliadas.
2. Revisar código/migrations da revisão aprovada. Fazer backup MySQL, configuração, storage e certificados; verificar checksum e restore isolado antes do corte.
3. Usar `git fetch origin --tags`, revisar `git log --oneline HEAD..origin/main` e confirmar ancestralidade. Atualizar somente por fast-forward autorizado. Não usar reset destrutivo, force push ou substituir `.env`.
4. Interromper Worker e Web para alterar código/schema. Instalar dependências dos manifests quando necessário. Gerar build antes de iniciar Django se o manifest ainda não existir.
5. Executar `check`, `showmigrations --plan` e `migrate --plan` no MySQL alvo confirmado; revisar as operações antes de `migrate --noinput`. Não executar `makemigrations` para inventar schema no servidor; `makemigrations --check --dry-run` apenas verifica consistência.
6. Gerar o build da revisão, executar `collectstatic --noinput`, `check --deploy` e `production-check.ps1`. A checagem de produção não valida por si só conectividade externa, restore, grants, contagens ou a saúde de uma sincronização.
7. Iniciar Web, depois Worker; conferir supervisor **e** filho, logs e versão. Validar login, convite/troca com conta autorizada, permissões/escopo, OTRS/CMDB, Cancelamentos e Qualidade. Não disparar TQ, OTP ou sincronização somente para satisfazer um smoke test.
8. Registrar revisão final, migrations, backup, horários, resultados e pendências em registro operacional privado. Abrir a aplicação somente com validações aprovadas ou limites expressamente aceitos pelo responsável.

Não repetir `ativar_politica_senhas --confirm` no deploy. A operação informou que a política já foi ativada no servidor conhecido; confirmar seu estado por consulta. Código/migrations da política e ativação operacional são etapas distintas. Veja [política de acesso](../03-seguranca-e-qualidade/politica-de-senhas-e-acessos.md).

## Cloudflare Tunnel e proteção perimetral

O modelo de config está em `deploy/windows/cloudflare/config.example.yml`. A instalação fornece config e credentials-file privados em `C:\Windows\System32\config\systemprofile\.cloudflared`, com ACLs SYSTEM/Administrators, e log de exemplo em `C:\ProgramData\ARION\logs\cloudflared.log`. Confirmar caminhos efetivos no serviço instalado sem divulgar credenciais. O instalador não cria automaticamente Tunnel, DNS ou hostname.

Conferir ingress para a porta local correta e regra final `http_status:404`, HTTPS público, serviço `Cloudflared`, saúde do Tunnel e registros DNS. Waitress confia em um proxy de loopback para X-Forwarded-For/Proto/Host; não expor sua porta diretamente. MySQL também fica em loopback. Não abrir portas públicas de banco/Web interno para resolver problema de Tunnel.

Cookies de sessão e CSRF são Secure/SameSite=Lax em produção; sessão é HttpOnly e CSRF é legível pelo AJAX same-origin. CSP global é Report-Only. Manter bypass de cache para HTML, login, ativação, módulos e APIs, inclusive `/cancelamentos/*` e `/qualidade/*`; cache público somente de estáticos apropriados. `/api/version/` e `/service-worker.js` usam no-store. O Service Worker não oferece cache offline de HTML autenticado nem dados operacionais. WAF, rate limit e restrições de token Mapbox exigem configuração pela equipe nos respectivos painéis; não são garantidos por este checkout.

## Worker e integrações

OTRS FAST: 2 min; FULL: 30 min; CMDB: 10 min; projeção de triagem: 2 min; histórico de backlog: 1 h após sucesso. Qualidade consulta artigos por cursor com intervalo padrão de 120 s. Backoff preserva a última leitura. Frequências são configurações do scheduler, não SLAs. Detalhes e comandos manuais estão em [Worker e sincronização](worker-e-sincronizacao.md).

Verificar configuração habilitada, teste da conexão, `IntegrationRun`, sucesso FULL, cursor e lease. Processo vivo não comprova freshness. OTRS/CMDB só são lidos; Brevo envia mensagens somente mediante autorização específica. A exceção temporária de TLS para OTRS legado exige todos os opt-ins do código e host exato; não flexibiliza TLS do restante do sistema.

## Backup e restore

**Crítica** — exemplo de backup consistente, fora do checkout, com retenção automática desativada durante o corte:

```powershell
.\deploy\windows\backup-arion.ps1 -RetentionDays 0
```

`-BackupDirectory` permite destino externo explícito. O padrão de retenção é **30 dias**; se maior que zero, o script remove `arion-db-*.sql` antigos do destino. Não usar retenção sem revisar a política. O script exige `mysqldump` no PATH e configuração de produção recuperável pela mesma conta DPAPI. Cria arquivo de opções temporário protegido, sem senha na linha de comando, remove-o ao terminar e promove `.partial` para `.sql` somente em sucesso não vazio.

O dump usa `--single-transaction --quick --routines --events --triggers --hex-blob --no-tablespaces --databases`. Confirmar privilégios e consistência das tabelas InnoDB; evitar DDL concorrente. A presença do arquivo não comprova restore. Registrar tamanho, SHA-256 (`Get-FileHash -LiteralPath '<dump privado>' -Algorithm SHA256`), cópia externa e resultado de restauração isolada. O backup **não** inclui `.env`, certificados, credenciais do Tunnel ou mídia privada.

Restore é manual; não há script de restore no repositório. O dump inclui criação/seleção do schema e pode recriar tabelas: não o apontar a um destino com trabalho a preservar. Em máquina isolada, usar cliente MySQL da versão compatível, conexão local autenticada por mecanismo privado e comando do cliente `source C:/caminho/privado/backup.sql`. Nunca usar `-pSENHA`, colar credenciais no comando ou importar cegamente um dump sobre outra base. Restaurar em schema/máquina de ensaio aprovado e comparar tabelas, PK/FK, migrations, contagens, Decimal, datas, usuários/grupos, históricos e arquivos privados. Não copiar o SQLite DEV sobre o MySQL produtivo.

## Recuperação em caso de falha

1. Suspender novas escritas/tráfego e parar Worker/Web. Preservar banco e logs da tentativa; registrar commit, migrations aplicadas e mudanças após o backup.
2. Definir retorno compatível entre código, schema e dados. Reverter Git sozinho não desfaz migrations, envios, importações ou edições humanas. Não executar `migrate <versão antiga>` sem avaliar reversibilidade e perda.
3. Quando restore for autorizado, conferir destino/backup, guardar o estado que será substituído e restaurar pelo procedimento validado; recuperar storage/configuração com ACLs. Reconciliar dados posteriores ao backup antes de liberar a operação.
4. Reconfigurar segredos sob a identidade correta se a máquina/conta mudou. Conferir integridade, `check`, status, login, escopo, versão e integrações, depois iniciar Web/Worker controladamente.

`rollback_banco_arion.py` somente troca a conexão de bootstrap para SQLite e exige reinício; **não restaura MySQL** e não é recuperação de produção. O modo `migrar_banco_arion --full-snapshot` serve para candidato vazio, sem mesclar dados do servidor. A [carga histórica de Cancelamentos](../06-integracoes-e-dados/arionbroker/carga-producao.md) tem plano independente e Apply produtivo ainda bloqueado no código.

## Evolução futura

Linux/Docker, `arionops.grupoarion.com.br`, DNS/IP corporativo, HTTPS sem dependência do Tunnel e Entra ID permanecem no [roadmap](../01-projeto/roadmap.md). Não substituem esta operação Windows/MySQL.
