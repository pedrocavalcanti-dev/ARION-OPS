# ARION OPS — Auditoria de Segurança da Aplicação (Baseline Versionada)

```text
ARION OPS
Auditoria de Segurança da Aplicação

Versão:
1.3.0

Data:
02/11/2026

Ambiente:
V1 — NOC ATACADO

Tipo:
White-box + testes ativos locais + validação passiva de produção

Responsável pelo sistema:
Pedro Cavalcanti

Empresa:
ARION Serviços de Telecomunicações
```

> **Declaração técnica da release:** Segurança validada no escopo e na versão avaliados.

---

## 1. Identificação Técnica da Build

| Atributo | Valor Verificado |
|---|---|
| **Produto** | ARION OPS — NOC ATACADO |
| **Versão (fonte `docs/08-releases/releases.json` / `config/releases.py`)** | `1.3.0` |
| **Branch Git** | `feature/central-relatorios` |
| **Commit base auditado** | `8cf5fb883bf946bf78b40cc0dcf71a92421e1652` |
| **Python** | `3.12.10` (`.venv`) |
| **Django** | `5.2.18` |
| **MySQL** | `8.4.7` (`arion@localhost`, schema `arion_ops`) |
| **Servidor WSGI** | `Waitress 3.0.2` (`127.0.0.1:8100` em produção / `127.0.0.1:8000` em desenvolvimento) |
| **Sistema Operacional** | Windows (`win32`) |
| **Borda Pública de Produção** | Cloudflare + Cloudflare Tunnel (`https://nocatacadoarion.bicoai.com.br`) |

---

## 2. Arquitetura de Transporte: Desenvolvimento Local vs. Produção

- **Desenvolvimento local (`ARION_ENV=development`):**
  - Utiliza HTTP exclusivamente em loopback (`http://127.0.0.1:8000` ou `localhost`).
  - HTTP em loopback local é esperado e permitido para desenvolvimento e testes locais, não constituindo vulnerabilidade.
- **Produção pública (`ARION_ENV=production`):**
  - Fluxo real: `Internet -> HTTPS -> Cloudflare -> Cloudflare Tunnel (cloudflared) -> 127.0.0.1:8100 (Waitress) -> Django`.
  - O tráfego entre `cloudflared` e `Waitress` termina no loopback HTTP interno da própria máquina (`127.0.0.1:8100`), com `Waitress` configurado para confiar em `X-Forwarded-Proto`, `X-Forwarded-For` e `X-Forwarded-Host` exclusivamente quando provenientes de `127.0.0.1`.
  - O usuário externo acessa obrigatoriamente via HTTPS (`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `SECURE_SSL_REDIRECT = True`, `SECURE_HSTS_SECONDS = 31536000` sem `includeSubDomains` e sem `preload`).

---

## 3. Resumo de Achados, Correções e Retestes

| Severidade | Encontrados | Corrigidos no Código | Mitigados / Aceitos | Abertos no Código |
|---|---:|---:|---:|---:|
| **Crítico** | 0 | 0 | 0 | 0 |
| **Alto** | 0 | 0 | 0 | 0 |
| **Médio** | 3 | 3 | 0 | 0 |
| **Baixo** | 4 | 3 | 1 | 0 |
| **Informativo** | 1 | 0 | 1 | 0 |
| **Total** | **8** | **6** | **2** | **0** |

### Detalhamento dos Itens Tratados

1. **SEC-001 (Médio — CORRIGIDO):** Neutralização de *Formula Injection* (`=`, `+`, `-`, `@`, inclusive precedidos por espaços/tabs) em `arion_geo/aplicativos/base_links/views.py` (`_safe_csv_value`) e `arion_geo/aplicativos/painel/views.py` (`_safe_spreadsheet_text`), alinhado a `rankings/exports.py` e `relatorios/services/exports.py`.
2. **SEC-002 (Médio — CORRIGIDO):** Exigência estrita de `https://` em `CMDBConfigurationForm.clean_base_url` (`arion_geo/aplicativos/integracoes/forms.py`) e `CMDBClient.__init__` (`arion_geo/aplicativos/integracoes/clients/cmdb.py`).
3. **SEC-003 (Médio — CORRIGIDO NO REPOSITÓRIO / AÇÃO DE INFRAESTRUTURA PENDENTE NO PAINEL CLOUDFLARE):** Configurado `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `SECURE_SSL_REDIRECT = True` e `SECURE_HSTS_SECONDS = 31536000` em `arion_geo/config/settings.py`, além de `--trusted-proxy=127.0.0.1` no `Waitress` (`deploy/windows/run_process.py`). Validado localmente que `HTTP_X_FORWARDED_PROTO=https` retorna `200 OK` com `Strict-Transport-Security: max-age=31536000` e `HTTP_X_FORWARDED_PROTO=http` retorna `301 Moved Permanently` para `https://`. Permanece como ação operacional complementar habilitar *Always Use HTTPS* e *HSTS* no painel Cloudflare.
4. **SEC-004 (Baixo — CORRIGIDO):** Implementado *rate limiting* local de autenticação via cache do Django em `arion_geo/aplicativos/autenticacao/throttle.py` e `forms.py` (`5` falhas por par `IP normalizado + usuário normalizado` e `20` falhas por `IP` em janela de `900s` / 15 minutos, mantendo mensagem genérica neutra e limpando o contador após login válido).
5. **SEC-005 (Baixo — CORRIGIDO):** Implementada validação preventiva *fail-closed* de TLS para hosts remotos em `OTRSReadOnlyClient` (`arion_geo/aplicativos/integracoes/clients/otrs.py`) e `OTRSConfigurationForm` (`arion_geo/aplicativos/integracoes/forms.py`), preservando `enabled=False` e permitindo conexões locais sem TLS apenas em loopback (`127.0.0.1`, `localhost`, `::1`).
6. **SEC-006 (Baixo — MITIGADO):** Conta MySQL `arion@localhost` restrita a loopback e ao schema `arion_ops.*` sem privilégios administrativos globais, protegida pelo Windows Firewall. Criado script de diagnóstico somente leitura `deploy/windows/hardening-mysql.ps1` com recomendação de `bind-address=127.0.0.1`.
7. **SEC-007 (Baixo — CORRIGIDO):** Alinhado `deploy/windows/production_check.py` com `arion_geo/config/settings.py` e a arquitetura real `Cloudflare -> Cloudflare Tunnel -> Waitress (127.0.0.1) -> Django`.
8. **SEC-008 (Informativo — ACEITO):** Mantido `Content-Security-Policy-Report-Only` global na V1 para preservar compatibilidade integral com Mapbox GL JS, ECharts e PWA, enquanto artefatos HTML do ARI aplicam `Content-Security-Policy: sandbox` em modo de bloqueio ativo.

---

## 4. Matriz de Testes de Segurança

| Categoria | Técnica | Resultado | Evidência |
|---|---|---|---|
| Autenticação | credenciais inválidas | PASSOU | resposta genérica (`HTTP 200`, sem revelar causa específica) |
| Enumeração | usuário inexistente vs. senha incorreta | PASSOU | mesma mensagem genérica de formulário |
| Session fixation | rotação da sessão no login | PASSOU | `sessionid` alterado após autenticação (`session_rotated_on_login=True`) |
| Logout | replay da sessão após `POST /logout/` | PASSOU | sessão antiga invalidada (`302 -> /login/?next=/painel/`) |
| CSRF | `POST` sem token ou com token inválido | PASSOU | bloqueado com `HTTP 403` em todos os endpoints testados |
| Open Redirect | parâmetro `next` externo (`https://evil.example`) | PASSOU | bloqueado (`url_has_allowed_host_and_scheme`) |
| RBAC | acesso a rotas administrativas sem permissão | PASSOU | bloqueado com `HTTP 403` para perfis não administrativos |
| Data Scope | tentativa de bypass por query string (`?project=...`) | PASSOU | interseção mantida (`199` links escopados vs. `3.446` globais) |
| IDOR | acesso direto a link/ticket/conversa ARI de terceiro | PASSOU | bloqueado com `HTTP 404` |
| Mass Assignment | injeção de `is_superuser=True&is_staff=True` | PASSOU | campos ignorados pelo form (`is_superuser=False`) |
| SQL Injection | payloads SQL em buscas/filtros + revisão white-box | PASSOU | não reproduzido; consultas parametrizadas |
| XSS | payloads HTML/JS refletidos + revisão de `innerHTML` e `\|safe` | PASSOU | escapado (`raw_xss_unescaped=False`) |
| Formula Injection | prefixos `=`, `+`, `-`, `@`, `\t=` em CSV/XLSX | CORRIGIDO | neutralizados com apóstrofo inicial (`'`) |
| Upload malicioso | `.svg`, `.html`, `.exe`, `.pdf` falso, `.pdf.exe` no ARI | PASSOU | rejeitados por `validate_upload` (`ValidationError`) |
| Path Traversal | `..\..\relatorio.txt` no upload do ARI | PASSOU | sanitizado para `relatorio.txt` |
| PDF Injection | tags HTML/XML (`<b><script>...`) na exportação PDF | PASSOU | escapadas via `xml_escape()` antes do ReportLab |
| Secrets | varredura do repositório e templates | PASSOU | nenhum segredo real versionado; senhas cifradas e mascaradas |
| Cookies | flags `Secure`, `HttpOnly`, `SameSite` | PASSOU | `HttpOnly=True`, `SameSite=Lax`, `Secure=True` em `production` |
| HTTPS | redirecionamento e reconhecimento de proxy em produção | CORRIGIDO | `HTTP_X_FORWARDED_PROTO=http` -> `301`; `https` -> `200` (borda Cloudflare documentada) |
| HSTS | emissão de `Strict-Transport-Security` em produção | CORRIGIDO | `max-age=31536000` emitido pelo Django em `HTTPS` (ativação na borda Cloudflare documentada) |
| Rate Limit | tentativas consecutivas de login | CORRIGIDO | bloqueio temporário após 5 falhas em 15 min; reset após login válido |
| OTRS | garantia somente leitura + TLS para host remoto | CORRIGIDO | `SET SESSION TRANSACTION READ ONLY`, regex `SELECT` e TLS *fail-closed* |
| CMDB | transporte de credenciais e sanitização | CORRIGIDO | HTTP rejeitado; somente `https://` aceito |
| MySQL | privilégios do usuário da aplicação | PASSOU | `arion@localhost` restrito ao schema `arion_ops.*` |

---

## 5. Vetores Avaliados

| Vetor de Ataque | Status Final | Observação Técnica |
|---|---|---|
| SQL Injection | **NÃO REPRODUZIDO** | Uso de Django ORM e queries parametrizadas; 0 ocorrências de `.raw()`, `.extra()` ou `RawSQL`. |
| Cross-Site Scripting (XSS) | **NÃO REPRODUZIDO** | Auto-escape nos templates, sanitização em `render_markdown` e helpers de escape no JS. |
| Cross-Site Request Forgery (CSRF) | **NÃO REPRODUZIDO** | `CsrfViewMiddleware` ativo globalmente; 0 ocorrências de `csrf_exempt`. |
| IDOR | **NÃO REPRODUZIDO** | Objetos fora do escopo ou de outros usuários retornam `HTTP 404`. |
| Broken Access Control | **NÃO REPRODUZIDO** | Rotas protegidas por `@login_required` e `@access_required(...)`. |
| Privilege Escalation | **NÃO REPRODUZIDO** | Proteção contra auto-promoção e contra remoção do último administrador ativo. |
| Mass Assignment | **NÃO REPRODUZIDO** | Formulários utilizam listas explícitas de campos (`fields`). |
| Open Redirect | **NÃO REPRODUZIDO** | Validação nativa de host no parâmetro `next` do login/logout. |
| Session Fixation | **NÃO REPRODUZIDO** | Rotação confirmada do `sessionid` no login. |
| Session Replay após logout | **NÃO REPRODUZIDO** | Sessão invalidada no servidor após `POST /logout/`. |
| User Enumeration | **NÃO REPRODUZIDO** | Mensagens idênticas para usuário inexistente, senha incorreta, inativo ou bloqueado. |
| Credential Brute Force / Rate Limiting | **CORRIGIDO** | Implementado throttle local via cache (`aplicativos/autenticacao/throttle.py`). |
| CSV / Spreadsheet Formula Injection | **CORRIGIDO** | Neutralização aplicada em todas as exportações CSV e XLSX. |
| HTML/XML Injection em PDF | **NÃO REPRODUZIDO** | Escape XML aplicado antes da construção de `Paragraph` no ReportLab. |
| Path Traversal | **NÃO REPRODUZIDO** | Sanitização de nome de arquivo (`PurePosixPath`) e validação de caminho em `MEDIA_ROOT`. |
| Upload de arquivo malicioso | **NÃO REPRODUZIDO** | Whitelist estrita de extensões e bloqueio de `.svg`, `.html`, `.exe`, `.js`. |
| Double Extension | **NÃO REPRODUZIDO** | Rejeição de nomes com múltiplos sufixos suspeitos (`.pdf.exe`). |
| MIME Spoofing / Magic Bytes | **NÃO REPRODUZIDO** | Inspeção de assinatura binária (`%PDF-`, ZIP/OOXML, PNG, JPEG, GIF, WEBP). |
| Exposição de arquivos sensíveis | **NÃO REPRODUZIDO** | `MEDIA_ROOT` privado (`var/private_media`), não publicado diretamente. |
| Exposição de secrets | **NÃO REPRODUZIDO** | Segredos armazenados fora do código e mascarados na interface e na auditoria. |
| Cache indevido / PWA | **NÃO REPRODUZIDO** | `Cache-Control: private, no-store` em respostas dinâmicas; SW restrito a `/static/`. |
| Web Storage sensível | **NÃO REPRODUZIDO** | `localStorage`/`sessionStorage` guardam apenas preferências visuais de UI. |
| Command Injection | **NÃO REPRODUZIDO** | 0 ocorrências de `os.system`, `eval`, `exec` ou `subprocess` com `shell=True`. |
| SSRF | **CORRIGIDO** | Clientes de integração restritos à configuração administrativa com HTTPS/TLS exigido. |
| Unsafe Deserialization | **NÃO REPRODUZIDO** | 0 ocorrências de `pickle` ou `yaml.load`. |
| Raw SQL / SQL não parametrizado | **NÃO REPRODUZIDO** | Todas as consultas diretas usam placeholders `%s` e whitelist de identificadores. |
| Acesso fora do Data Scope | **NÃO REPRODUZIDO** | `DataScopeService` aplicado no backend antes de listagens, KPIs, mapa e exportações. |
| Acesso administrativo sem permissão | **NÃO REPRODUZIDO** | Todas as rotas de `/configuracoes/administracao/` retornam `HTTP 403` para não administradores. |

---

## 6. Conclusão

A versão `1.3.0` do **ARION OPS** foi submetida a revisão white-box, testes ativos controlados em ambiente local e validação passiva da superfície pública. No escopo testado, não foram identificadas vulnerabilidades críticas, altas ou médias pendentes no código da aplicação após as correções realizadas. Os principais controles de autenticação, rate limiting de login, sessão, CSRF, RBAC, Data Scope, consultas ao banco, exportações, uploads e integrações foram validados e contam com suíte automatizada de regressão (`python arion_geo/gerenciar.py test config.test_security`).

