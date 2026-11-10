# Arquitetura de Segurança — ARION OPS

**Produto:** ARION OPS — NOC ATACADO
**Versão de referência do código:** `1.4.0` (`docs/08-releases/releases.json` / `arion_geo/config/releases.py`). A auditoria de segurança 1.3.0 permanece como baseline histórica, sem certificação automática das alterações posteriores.
**Empresa:** ARION Serviços de Telecomunicações
**Concepção e desenvolvimento:** Pedro Cavalcanti
**Declaração institucional:** Segurança validada no escopo e na versão avaliados.

---

## 1. Visão geral

O **ARION OPS** adota uma arquitetura de defesa em profundidade (*defense-in-depth*) voltada à proteção de dados operacionais do NOC Atacado, combinando isolamento de rede em loopback, autenticação com proteção contra força bruta, rotação e proteção de sessões, controle de acesso baseado em perfis (**RBAC**), segregação obrigatória de dados no backend (**Data Scope**), proteção contra falsificação de requisição (**CSRF**), leitura estrita nas integrações externas (**OTRS** e **CMDB**) e trilha de auditoria sanitizada.

---

## 2. Modelo de confiança

- **Fonte oficial de verdade:** OTRS/CMDB são registros técnicos oficiais. O MySQL local de produção mantém espelhos, análise e registros próprios de acesso, Qualidade, Cancelamentos, relatórios e auditoria; o nome real do schema não é presumido nesta documentação.
- **Integrações estritamente somente leitura:** O ARION OPS nunca executa escritas (`INSERT`, `UPDATE`, `DELETE`, `ALTER`, `DROP`) no banco remoto do OTRS nem mutações na API da CMDB.
- **Fronteira de rede:** Nenhum processo Python/Django nem o banco MySQL devem ser expostos diretamente à internet pública. Todo acesso externo atravessa a borda HTTPS do Cloudflare e o agente local `cloudflared`.

---

## 3. HTTPS e Cloudflare

A topologia de produção segue o fluxo:

```text
Internet
  ↓ (HTTPS obrigatório)
Cloudflare (WAF / TLS Edge / HSTS)
  ↓
Cloudflare Tunnel (cloudflared)
  ↓ (HTTP apenas no loopback interno 127.0.0.1:8100)
Waitress (--listen=127.0.0.1:8100 --trusted-proxy=127.0.0.1)
  ↓
Django (SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https"))
```

- **Desenvolvimento local (`ARION_ENV=development`):**
  - Utiliza `http://127.0.0.1:8000` (ou `localhost`). HTTP em loopback local é permitido e esperado para desenvolvimento, sem exigir certificado TLS em `127.0.0.1`.
  - `SECURE_SSL_REDIRECT = False` e `SECURE_HSTS_SECONDS = 0`.
- **Produção pública (`ARION_ENV=production`):**
  - HTTPS é obrigatório para todos os usuários externos.
  - O Django reconhece o protocolo original via `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, habilita `SECURE_SSL_REDIRECT = True` para redirecionar requisições HTTP externas para HTTPS sem gerar loop no túnel e emite `Strict-Transport-Security: max-age=31536000` (sem `includeSubDomains` e sem `preload` automáticos).
  - Na borda Cloudflare (`SSL/TLS -> Edge Certificates`), recomenda-se manter ativos **Always Use HTTPS** e **HSTS**.

---

## 4. Waitress / loopback

- O supervisor de produção (`deploy/windows/run_process.py`) inicia o servidor WSGI **Waitress** escutando exclusivamente em loopback (`--listen=127.0.0.1:<porta>`).
- O Waitress é iniciado com `--trusted-proxy=127.0.0.1`, `--trusted-proxy-count=1`, `--trusted-proxy-headers=x-forwarded-for x-forwarded-proto x-forwarded-host` e `--clear-untrusted-proxy-headers`.
- Dessa forma, somente o agente local `cloudflared` (em `127.0.0.1`) pode fornecer cabeçalhos de encaminhamento (`X-Forwarded-Proto`, `X-Forwarded-For`), impedindo falsificação de cabeçalhos e mantendo `expose_tracebacks=False`.

---

## 5. Autenticação

- Baseada no sistema nativo `django.contrib.auth` com hash **PBKDF2-SHA256** e validadores de complexidade (`UserAttributeSimilarityValidator`, `MinimumLengthValidator`, `CommonPasswordValidator`, `NumericPasswordValidator`).
- **Prevenção contra enumeração de usuários:** A view e o formulário de login (`LoginForm` em `arion_geo/aplicativos/autenticacao/forms.py`) retornam a mesma mensagem genérica neutra para usuário inexistente, senha incorreta, conta inativa ou tentativa temporariamente bloqueada.
- **Proteção contra Open Redirect:** O redirecionamento pós-login valida o parâmetro `next` com `url_has_allowed_host_and_scheme`, rejeitando URLs externas.

---

## 6. Sessões

- **Rotação contra Session Fixation:** O identificador de sessão (`sessionid`) é rotacionado automaticamente após autenticação bem-sucedida e o hash de sessão é atualizado na troca de senha (`update_session_auth_hash`).
- **Invalidação no Logout:** O endpoint `/logout/` aceita exclusivamente `POST` protegido por CSRF, destrói a sessão no servidor e limpa caches de navegação.
- **Flags dos cookies:**
  - `SESSION_COOKIE_HTTPONLY = True`
  - `SESSION_COOKIE_SAMESITE = "Lax"`
  - Não há `SESSION_COOKIE_AGE` customizado no checkout; vale o default Django, sujeito à configuração efetiva. Não existe contrato local de oito horas.
  - `SESSION_COOKIE_SECURE = True` e `CSRF_COOKIE_SECURE = True` (ativados automaticamente quando `ARION_ENV=production`).

---

## 7. CSRF

- `CsrfViewMiddleware` está ativo globalmente em `MIDDLEWARE`.
- **Zero uso de `csrf_exempt`:** Nenhum endpoint da aplicação desabilita a verificação CSRF.
- Requisições AJAX/JSON enviam `X-CSRFToken`. Em produção (`ARION_ENV=production`), `CSRF_TRUSTED_ORIGINS` exige origens explícitas com esquema `https://`, sem wildcards, todas pertencentes a `ALLOWED_HOSTS`.
- Falhas de CSRF são tratadas por `config.errors.csrf_failure`, que registra o evento de forma sanitizada (sem vazar tokens ou credenciais embutidas em URLs).

---

## 8. Rate limiting

- O login possui proteção local baseada no cache do Django (`arion_geo/aplicativos/autenticacao/throttle.py`), sem dependência externa adicional:
  - **Por par `(IP normalizado, usuário normalizado)`:** bloqueio temporário após `5` falhas consecutivas (`ARION_LOGIN_MAX_FAILURES = 5`) na janela de `900` segundos / 15 minutos (`ARION_LOGIN_THROTTLE_SECONDS = 900`).
  - **Por IP de origem:** bloqueio temporário após `20` falhas (`ARION_LOGIN_MAX_IP_FAILURES = 20`) na mesma janela de 15 minutos.
- Enquanto o bloqueio temporário estiver ativo, `LoginForm.clean()` interrompe a tentativa antes de acionar `authenticate()` e retorna a mesma mensagem genérica de falha de login.
- Um login bem-sucedido limpa imediatamente os contadores associados via `clear_login_failures()`.
- O IP do cliente só aceita `HTTP_CF_CONNECTING_IP` quando `ARION_PRODUCTION=True` e a conexão TCP imediata provém do loopback (`127.0.0.1` / `::1`).

---

## 9. RBAC

- O controle funcional é implementado com **Django Groups** e **Permissions** gerenciados em `arion_geo/aplicativos/configuracoes/access_control.py` e aplicados no backend por `@login_required` e `@access_required("<permissão>")`.
- Perfis operacionais padrão:
  - **Gestão** (ver ressalvas de Relatórios na matriz funcional)
  - **Administrador do Sistema**
  - **Gestor**
  - **Analista NOC**
  - **Consulta / Auditoria**
- **Proteção anti-lockout administrativo:** As views de edição de usuários e grupos impedem a desativação ou remoção de permissões do último Administrador do Sistema ativo.
- **Proteção contra Mass Assignment:** Todos os formulários (`AccountForm`, `UserCreateForm`, `UserUpdateForm`, `AccessGroupForm`) declaram explicitamente os campos permitidos, ignorando parâmetros como `is_superuser` ou `is_staff`.

---

## 10. Data Scope

- O **Data Scope** (`GroupDataScope` e `DataScopeService` em `arion_geo/aplicativos/configuracoes/data_scope.py`) define quais dados operacionais cada grupo pode visualizar:
  - Modo `ALL`: visão global da operação.
  - Modo `CUSTOM`: interseção restrita às **Operadoras permitidas** (`allowed_operators`) e **Projetos permitidos** (`allowed_projects`).
- **Aplicação no backend:** As consultas operacionais de espelhos, Qualidade e Cancelamentos aplicam escopo antes dos agregados e detalhes. **Relatórios tem limitação real:** competências, Original e Tratativa armazenados não passam pelo mesmo filtro de grupo; `view_reports` também autoriza criação/upload/importação. Conferir a [matriz por módulo](../05-funcionalidades/usuarios-grupos-e-escopos.md) antes de conceder acesso restrito.
- **Proteção contra IDOR:** Tentativas de acesso direto por ID/CI a links ou recursos fora do escopo do usuário (ou a conversas do ARI pertencentes a outro usuário) retornam `404 Not Found`.

---

## 11. Banco MySQL

- Produção exige MySQL em loopback; o checkout DEV atual usa SQLite, que não homologa compatibilidade/concorrência MySQL. Não substituir o banco do servidor pelo DEV.
- **Princípio de menor privilégio:** Configuração exige conta não-root e host local em produção. Usuário/schema/grants efetivos devem ser conferidos no host; o código não comprova sozinho a ausência de `SUPER`, `FILE` ou `GRANT OPTION`. O exemplo de hardening não é evidência de grants já aplicados.
- **Parametrização SQL:** O projeto utiliza o ORM do Django e queries parametrizadas (`%s`), sem uso de `.raw()`, `.extra()` ou `RawSQL`.
- **Hardening de rede local:** Recomenda-se manter `bind-address=127.0.0.1` e `mysqlx-bind-address=127.0.0.1` no `my.ini`, verificável de forma somente leitura pelo script `deploy/windows/hardening-mysql.ps1`.

---

## 12. OTRS

- O cliente `OTRSReadOnlyClient` (`arion_geo/aplicativos/integracoes/clients/otrs.py`) impõe múltiplas barreiras técnicas de somente leitura:
  1. Executa `SET SESSION TRANSACTION READ ONLY` imediatamente ao abrir a conexão;
  2. Permite apenas instruções iniciadas por `SELECT` (ou `SHOW`/`DESCRIBE` internos), rejeitando `;` e qualquer palavra-chave de escrita ou efeito colateral (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `GRANT`, `INTO OUTFILE`, `SLEEP`, `GET_LOCK`);
  3. Valida nomes de tabelas e colunas contra o catálogo descoberto em `information_schema` e regex estrita `^[A-Za-z0-9_$]+$`;
  4. Bloqueia e mascara colunas/tabelas sensíveis (`pw`, `password`, `token`, `secret`, `session`, `customer_user`, `article`, `a_body`, `internal_note`).
- **TLS fail-closed:** Remoto exige TLS validado por padrão; loopback permite acesso local. Existe exceção temporária restrita ao host exato em `ARION_OTRS_LEGACY_NO_TLS_HOST`, somente com `use_tls=False` **e** `allow_insecure_remote=True`. Não se aplica a CMDB/Brevo/banco principal. Tráfego nessa exceção não é cifrado e sua retirada depende da correção da infraestrutura. Leitura específica transitória de artigos para QID não libera corpos/anexos pelo cliente genérico.

---

## 13. CMDB

- O cliente `CMDBClient` (`arion_geo/aplicativos/integracoes/clients/cmdb.py`) e o formulário `CMDBConfigurationForm` (`arion_geo/aplicativos/integracoes/forms.py`) exigem obrigatoriamente URLs com esquema `https://` e hostname válido, rejeitando `http://`, credenciais embutidas na URL, query strings ou fragmentos.
- A validação de certificado TLS (`verify_tls=True` por padrão) utiliza `ssl.create_default_context()`.
- O sanitizador `CMDBClient.sanitize_item()` filtra atributos sensíveis (`Password`, `PortalPassword`, `SenhaPPOE`, `UsuarioPortalOperadora`, `SessionCookie`) antes de qualquer exibição diagnóstica.
- As senhas de integração são armazenadas cifradas (`secret_ciphertext`) e nunca são reexibidas na interface (`PasswordInput(render_value=False)`).

---

## 14. Exportações

- **Prevenção contra Formula Injection (CSV / XLSX):** Todos os módulos de exportação (`arion_geo/aplicativos/base_links/views.py`, `arion_geo/aplicativos/painel/views.py`, `arion_geo/aplicativos/rankings/exports.py` e `arion_geo/aplicativos/relatorios/services/exports.py`) neutralizam células textuais que iniciem (mesmo após espaços, tabulações ou quebras de linha via `.lstrip()`) com `=`, `+`, `-` ou `@`, prefixando um apóstrofo (`'`).
- **Prevenção contra injeção em PDF:** A geração de relatórios PDF via ReportLab (`arion_geo/aplicativos/rankings/exports.py`) escapa todos os textos dinâmicos com `xml.sax.saxutils.escape` antes de construir objetos `Paragraph`.

---

## 15. Uploads

- O módulo de anexos do ARI (`arion_geo/aplicativos/ari/file_security.py`) e o handler `AriAttachmentQuotaHandler` (`arion_geo/config/upload_limits.py`) aplicam:
  - Limites de tamanho por arquivo (`20 MB`), quantidade máxima por mensagem (`5` anexos) e cota total (`40 MB`);
  - Whitelist estrita de extensões (`.pdf`, `.xlsx`, `.csv`, `.txt`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`), bloqueando `.svg`, `.html`, `.exe`, `.bat`, `.ps1`, `.js` e duplas extensões suspeitas (como `.pdf.exe`);
  - Validação de assinatura binária (*magic bytes* `%PDF-`, estrutura ZIP/OOXML `[Content_Types].xml`, cabeçalhos PNG/JPEG/GIF/WEBP);
  - Sanitização de nome de arquivo contra *Path Traversal* (`PurePosixPath` + `get_valid_filename`);
  - Armazenamento em diretório privado (`MEDIA_ROOT = var/private_media`), servido exclusivamente por views autenticadas que verificam a titularidade da conversa e enviam `X-Content-Type-Options: nosniff`.

---

## 16. PWA / Cache

- O middleware `ProductionSecurityHeadersMiddleware` (`arion_geo/config/middleware.py`) define `Cache-Control: private, no-store` em todas as páginas HTML autenticadas e respostas JSON da API.
- O Service Worker (`arion_geo/static/pwa/service-worker.js`) armazena em cache apenas recursos estáticos sob `/static/` e o shell offline neutro (`offline.html`), nunca persistindo dados operacionais autenticados para consulta offline.
- `localStorage` e `sessionStorage` são utilizados exclusivamente para preferências visuais de interface (tema claro/escuro, recolhimento de painéis e aviso de versão PWA), sem armazenar tokens ou segredos.

---

## 17. Headers

Todas as respostas da aplicação incluem cabeçalhos defensivos padronizados:

- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Cross-Origin-Opener-Policy: same-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
- `X-Request-ID: REQ-<hex>` (correlação de diagnóstico sem exposição de stack trace)
- `Strict-Transport-Security: max-age=31536000` (em produção sob HTTPS)
- `Content-Security-Policy-Report-Only` nas páginas HTML globais e `Content-Security-Policy: sandbox` estrito na visualização de artefatos HTML do ARI.

---

## 18. Logging e auditoria

- **Auditoria administrativa (`AuditService` em `arion_geo/aplicativos/auditoria/services.py`):** Registra alterações de conta, senha, usuários, grupos, permissões, escopos de dados e integrações, sanitizando recursivamente chaves sensíveis (`password`, `senha`, `token`, `secret`, `cookie`, `credential`, `authorization`, `sessionid`, `apikey`, `privatekey`).
- **Logs de processo e erros (`deploy/windows/run_process.py` e `arion_geo/config/errors.py`):** Filtram variáveis sensíveis do ambiente (`<redacted>`) e substituem falhas `4xx`/`5xx` por páginas e payloads JSON padronizados sem expor stack traces, caminhos internos ou SQL ao usuário.

---

## 19. Testes de segurança

O projeto mantém uma baseline executável de testes de regressão de segurança em `arion_geo/config/test_security.py`, complementada por `arion_geo/aplicativos/integracoes/test_clients.py` e `arion_geo/aplicativos/base_links/tests.py`.

Para executar a suíte de segurança e a verificação de configuração local:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py test config.test_security aplicativos.integracoes.test_clients aplicativos.base_links.tests.SpreadsheetExportSafetyTests
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py auditar_seguranca
```

---

## 20. Processo de release

Para implantação 1.4.0, usar o [guia Windows/MySQL](../02-arquitetura/implantacao-e-producao.md). A política de [convites e senhas](politica-de-senhas-e-acessos.md) é aplicada por middleware inclusive para superuser humano; contas de serviço são exceção explícita. Não reativar a política global já informada como ativa no servidor. Brevo envia mensagens externas sob autorização, portanto “somente leitura” descreve OTRS/CMDB, não todas as integrações.

Antes de cada fechamento de versão e publicação em produção, devem ser executados:

1. `.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py check`
2. `.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py makemigrations --check --dry-run`
3. `.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py auditar_seguranca`
4. `.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py test config.test_security aplicativos.integracoes.test_clients aplicativos.base_links.tests.SpreadsheetExportSafetyTests`
5. `powershell -ExecutionPolicy Bypass -File .\deploy\windows\production-check.ps1` (no host de produção com `.env` de produção e `collectstatic` atualizado)
6. Registro do manifesto e certificado técnico da release em `docs/03-seguranca-e-qualidade/auditorias/`.

---

## 21. Auditorias executadas

### V1.3.0 — 02/11/2026

- **Tipo:** White-box + testes ativos controlados em ambiente local + validação passiva de produção.
- **Commit auditado:** `8cf5fb883bf946bf78b40cc0dcf71a92421e1652` (`feature/central-relatorios`)
- **Resultado:** Segurança validada no escopo e na versão avaliados.
- **Críticos abertos:** `0`
- **Altos abertos:** `0`
- **Médios abertos no código:** `0`
- **Baixos abertos no código:** `0` (`1` item de infraestrutura local mitigado e documentado)
- **Principais correções aplicadas e retestadas:**
  - Neutralização de *Formula Injection* em todas as exportações CSV/XLSX (`SEC-001`);
  - Exigência estrita de HTTPS no cliente e formulário da CMDB (`SEC-002`);
  - Hardening de `SECURE_PROXY_SSL_HEADER`, `SECURE_SSL_REDIRECT=True`, `SECURE_HSTS_SECONDS=31536000` e `--trusted-proxy=127.0.0.1` no Waitress (`SEC-003` / `SEC-007`);
  - Implementação de *rate limiting* local de login via cache com mensagens genéricas (`SEC-004`);
  - Validação preventiva *fail-closed* de TLS para conexões remotas do OTRS (`SEC-005`);
  - Script de diagnóstico somente leitura para hardening do MySQL local (`deploy/windows/hardening-mysql.ps1`, `SEC-006`).
- **Artefatos da auditoria:**
  - Relatório completo: `docs/03-seguranca-e-qualidade/relatorios/relatorio-de-seguranca-v1.md` e `.pdf`
  - Baseline versionada: `docs/03-seguranca-e-qualidade/auditorias/auditoria-seguranca-v1.3.0-2026-11-02.md`
  - Manifesto legível por máquina: `docs/03-seguranca-e-qualidade/auditorias/manifesto-seguranca-v1.3.0.json`
