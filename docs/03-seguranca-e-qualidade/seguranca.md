# Segurança

## Identidade, permissão e escopo

Autenticação usa Django Auth. RBAC usa Groups/Permissions e cada operação é verificada no servidor; esconder link/botão não protege endpoint. Data Scope é aplicado no backend antes de filtros do usuário, agregações, ranking, paginação, detalhes, APIs e exports. Em CUSTOM, Cliente/Operadora e Projeto se cruzam por AND; dimensão ausente ou lista vazia não amplia acesso. Superuser técnico é global e dispensa `GroupDataScope`.

## Fronteira das fontes

Conector MySQL OTRS configura sessão read-only e limita as queries. Chamados, analytics, Ranking, Mapa e Base não escrevem em OTRS/CMDB nem consultam fontes remotas durante requests. CMDB usa Search/Get; sincronizador valida snapshot antes de reconciliar ausência. Queries não leem senha, tokens, artigos ou corpo de mensagens.

Credenciais de portal/PPPoE não são persistidas nos mirrors CMDB. Username de portal não pertence ao model atual `CMDBOperatorMirror`. Segredos de Integration são mascarados e ciphertext não volta à UI. DPAPI no Windows vincula segredo ao usuário de execução; mudança de conta/máquina exige cadastrar novamente.

## Sessão, upload e exportação

Forms seguem proteção CSRF e validação Django; interface não é fronteira de autorização. ARI permite até cinco anexos por request, limita cada arquivo a 20 MiB e o total a 40 MiB durante o recebimento e na camada de serviço; valida tipo, nome e ownership antes de servir anexos de storage privado. Exports repetem Data Scope e CSV neutraliza valores interpretados como fórmula. Não publicar senha, token, cookie, Authorization, connection string ou PII desnecessária em docs, logs, audit metadata ou exemplos.

## Auditoria e observabilidade

Eventos importantes passam por `AuditService`, com sanitização recursiva de metadata. Auditoria é histórico, não debug log; não deve guardar credenciais nem corpo de nota. Logs técnicos mantêm contexto mínimo, sem segredo. Worker usa `IntegrationLease` com token, heartbeat e expiração.

## Produção

Production exige `DEBUG=False`, chave externa forte, hosts explícitos, origens CSRF HTTPS, MySQL loopback não-root, Waitress em `127.0.0.1`, cookies seguros e HTTPS via Tunnel. CSP permanece Report-Only para validar Mapbox; HSTS inicia em 3600 segundos sem preload/subdomínios. Respostas dinâmicas são `private, no-store`; Cloudflare deve bypassar cache em páginas/APIs e cachear somente static.

Secrets locais de integração e do banco usam DPAPI current-user. Web e Worker precisam rodar sob a mesma conta Windows dedicada que cadastrou os secrets. Não copiar ciphertext DPAPI para outra conta ou máquina: recadastrar no host novo.

Cloudflare WAF, bot protection, rate limiting/login challenge, DNS, Tunnel health e Security Events exigem configuração/revisão na conta e podem depender do plano. Access é opção futura e não está ativado. O Tunnel é outbound; não abrir publicamente 8000, 3306, 80 ou 443. Backup MySQL completo fica fora do web root; restore exige parada manual de Web/Worker, backup explícito do destino e confirmação operacional.

Consulte [Implantação](..\02-arquitetura\implantacao-e-producao.md) para os comandos, transferência MySQL e roteiro manual. Os scripts preparam a máquina local, mas não criam Tunnel/DNS nem executam deploy remoto.
