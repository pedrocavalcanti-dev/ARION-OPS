# Central de Integrações

## Papel

A Central administra configuração dos conectores CMDB/OTRS e mostra saúde/freshness dos mirrors: última execução, tipo, duração, resultado e contagens. Não é cliente de consulta remota por request; sincronização roda no worker separado.

## Conectores e estado

### Matriz operacional 1.4.0

| Integração | Origem → destino e tipo | Gatilho / dependências | Falhas, evidência e reconciliação |
|---|---|---|---|
| OTRS | MySQL remoto somente leitura → mirror local | Worker FAST 2 min/FULL 30 min; integração habilitada, conta read-only, schema/filas e TLS conforme política | `IntegrationRun`, `IntegrationSyncState`, lease; FULL reconcilia cobertura, delta mantém cursor em erro |
| CMDB ServiceUp | Search/Get HTTPS somente leitura → links/provedores locais | Worker 10 min; classes, credencial e resposta completa | Run/estado/lease; snapshot inválido não marca ausências |
| Backlog Suporte | Eventos estado/fila/procedência OTRS → intervalos locais | Worker 1 h após sucesso; leitura consistente autorizada | Cache anterior preservado, cobertura e backoff próprios; não lê artigos |
| Entrada Cancelamentos | Mirror OTRS → triagem local | Projeção 2 min, FULL válido e freshness | Estado/lease `cancellation_intake`; upsert por TicketID, decisões preservadas, zero pedidos automáticos |
| Qualidade/OTRS | Artigos da fila homologada → vínculo QID/TQ e checkpoint | Worker 120 s padrão, lote 100/teto 200; OTRS habilitado | Corpos transitórios, fila/QID exatos, conflitos auditados; envio Brevo aceito não confirma ticket |
| Brevo | ARION → serviço SMTP/API externo; **envio de mensagem** | POST humano autorizado/confirmado; habilitação, remetente/To e credenciais DPAPI | Tentativa idempotente, resultado incerto bloqueia reenvio; teste de conexão não envia; teste de roteamento envia e é separado |
| ArionBroker | Exportação autorizada → registros locais históricos | Comandos manuais DEV, preview/dry-run/apply controlado; sem polling ativo do legado | Origem/PK/checksum, lote, relatório privado e reconciliação; produção bloqueada, ver roteiro próprio |
| Mapbox | Token público/configuração → SDK e serviço de mapa no navegador | Ao abrir Mapa; banco tem precedência sobre fallback de ambiente | Falha de token/rede recuperável; não é sync de CMDB nem geocodificação de links |

Configurar requer `configuracoes.manage_integrations`, testar requer `run_integrations`, inspeção `inspect_integrations`, consulta `view_integrations`, conforme endpoint. Acesso administrativo não concede carteira operacional nem envia mensagens automaticamente. Logs/run/health check são sanitizados; antes de encaminhá-los remover contexto pessoal desnecessário. Não confundir `ExternalAPIHealthCheck` de Brevo/Mapbox com execução aplicada OTRS/CMDB.

Verificação diária: integração habilitada → conexão/teste → último apply/FULL/checkpoint → contagens/cursor/lease → freshness na tela. Estado instalado de credenciais/rotas externas exige homologação no servidor; esta revisão não realiza envios, leitura remota ou sincronização. A [carga ArionBroker em produção](../06-integracoes-e-dados/arionbroker/carga-producao.md) permanece tarefa própria.

- **CMDB ServiceUp:** GenericInterface Search/Get para classes `Links de internet` e `Operadoras`, destino local `CMDBLinkMirror` e `CMDBOperatorMirror`.
- **OTRS MySQL:** conexão read-only às filas `NOC - ATACADO`, `NOC - CANCELAMENTOS NGO`, `NOC - RETIRA EQUIPAMENTO` e `NOC – PENDENTE PAGAMENTO` (travessão no nome real), com estados, relações e Dynamic Fields selecionados; destino `OTRSTicket`, `OTRSTicketConfigItem` e `OTRSStateMapping`. O conjunto atende aos universos do Cockpit. `OTRS_NOC_ATACADO_QUEUE_URL` é um deep link da interface web, não um filtro de sincronização.

`Integration` guarda configuração protegida/status. `IntegrationRun` guarda tentativas sanitizadas. `IntegrationSyncState` persiste cursor, sucesso/último FULL e `IntegrationLease` coordena processos. Segredo aparece mascarado, nunca em response/API.

O status de `Integration` representa configuração e evidência de conectividade. `IntegrationRun` registra o resultado de cada operação; `IntegrationSyncState` registra cursor, último apply, falha e backoff. Um teste de conexão bem-sucedido não comprova que FULL/FAST aplicou dados. Falhas de autenticação, TLS ou transporte atualizam a saúde da conexão; falhas de consulta, validação ou aplicação local ficam no run e no estado de sincronização, preservando a última conectividade conhecida. Falhas ao preparar o client também geram `IntegrationRun` com erro sanitizado.

Cadências: OTRS FAST 2 min; OTRS FULL 30 min; CMDB FULL 10 min; retry 2 min. O FULL OTRS reconcilia o conjunto de filas selecionado e preserva tickets de filas fora dele. O FAST compartilhado exige o conjunto completo de quatro filas e um FULL bem-sucedido com essa cobertura; mudança de cobertura agenda novo FULL. Ver [Worker](..\02-arquitetura\worker-e-sincronizacao.md), [OTRS](..\06-integracoes-e-dados\otrs.md) e [CMDB](..\06-integracoes-e-dados\cmdb.md).

## Leitura da Central

A página inicial separa estado das conexões, sincronização e diagnóstico local. Os cartões mostram teste mais recente, última leitura, registros processados quando disponíveis e uma indicação de sincronização em andamento. Configuração de host, credenciais protegidas e testes permanecem nas páginas individuais; os cartões nunca imprimem segredos nem caminhos do banco.

Os totais de links, operadoras, tickets e vínculos de Qualidade exibidos no diagnóstico aplicam o Data Scope do usuário antes de contar. O resumo de saúde do worker e freshness descreve o estado técnico das fontes. Mensagens de falha usam resumos sanitizados persistidos pelo worker, acompanhados de uma ação sugerida.

## Diagnóstico

Leia resultado e último sucesso em conjunto: attempt FAILED recente não torna mirror fresco. Confira configuração/conectividade, último FAST/FULL, cursor e lease. OTRS depende da resolução exata de cada nome de fila selecionado; CMDB precisa resposta completa Search/Get. FULL valida o conjunto antes de marcar ausências; falha conserva o último snapshot. A tela do Cockpit distingue cobertura FULL ainda não aplicada de uma sincronização bem-sucedida sem tickets (zero real).

A causa exata do incidente TLS histórico em produção não foi confirmada nesta etapa: não houve acesso ao servidor, logs ou ambiente de produção. A inspeção local não encontrou factories OTRS distintas para Web e Worker; ambos usam o mesmo cliente e agora recebem o `.env` da raiz com a mesma precedência de ambiente. TLS segue obrigatório para host remoto, exceto opt-in explícito e restrito ao host legado configurado. A validação final da causa depende de uma futura inspeção autorizada no servidor.

Segredos locais DPAPI dependem do usuário Windows de execução. Em outra conta ou máquina, cadastre de novo sem copiar credencial em ticket de suporte.

## Limites

Chamados não atualiza OTRS e Base não edita CMDB. Importador XLSX antigo não é uma integração oficial ativa. Runs não devem armazenar payload de ticket/credenciais. Permission de integração não substitui Data Scope dos dados operacionais.

## APIs externas

A Central administra Brevo e Mapbox. A tela Brevo oferece SMTP Relay com STARTTLS obrigatório e API REST selecionável; credenciais ficam separadas e protegidas por DPAPI. A configuração distingue To, Cc e Reply-To. O teste SMTP autentica sem enviar e-mail; o teste REST consulta estatística transacional. Respostas SMTP `535` são classificadas como falha de autenticação e `525` como bloqueio de política TLS, sem registrar credenciais ou conteúdo. Diagnósticos persistidos são sanitizados. Nenhum envio externo foi feito durante esta estabilização.

O token Mapbox é público (pk.) e pode ser cadastrado pela Central. A resolução de runtime prioriza um registro explícito no banco; MAPBOX_PUBLIC_TOKEN é fallback apenas quando não há registro. Um registro inválido ou desabilitado não volta silenciosamente ao valor antigo. Restrinja o token público aos domínios autorizados no painel Mapbox.

Os dois testes e alterações passam pelo RBAC e pela auditoria central. Apenas admins podem alterar configuração; testes requerem configuracoes.run_integrations. Credenciais privadas nunca entram em HTML, logs ou metadata de auditoria. DPAPI usa o usuário Windows do processo: web e qualquer worker que venha a consumir a chave devem executar com a identidade capaz de descriptografá-la.


### Homologacao do roteamento Brevo

A pagina Brevo possui um teste de roteamento administrativo separado do teste SMTP de autenticacao e do envio de TQ. Configure um destinatario controlado; a acao exige confirmacao individual, usa somente To e nao altera a validacao OTRS. O destinatario operacional exige autorizacao administrativa previa especifica e uma segunda confirmacao por mensagem. O Cc nao e incluido nesse teste. O aceite SMTP e registrado como resultado do relay, nao como prova de recebimento ou criacao de ticket.

Antes de habilitar envio TQ, a readiness tambem exige integracao OTRS somente leitura e sucesso inicial do checkpoint incremental de todas as filas de Qualidade. A mensagem de homologacao usa ID `ARION-ROUTE-<32 hex>`; QIDs de TQ continuam reservados ao corpo da solicitacao real.

## Catálogo geográfico e clima

A Central inclui IBGE e Open-Meteo, usando ExternalAPIIntegration, credenciais protegidas, ExternalAPIHealthCheck, IntegrationSyncState e leases existentes. IBGE sincroniza pelo worker e conserva catálogo offline. Open-Meteo coleta UF/mês sob demanda, com cache persistente, limite de requisições e modalidade comercial obrigatória para uso regular em produção. Configuração não comprova acesso ativo. Ver [GEO Intelligence](geo-intelligence.md).
