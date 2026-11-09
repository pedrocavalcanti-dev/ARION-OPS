# Cancelamentos — plano de integração ArionBroker / ARION OPS

Data da análise: 09/11/2026. **Status: proposta de implementação em DEV**, baseada no código atual do ARION OPS. Não representa módulo implementado nem regras homologadas do ArionBroker.

Este é o plano original da etapa de preparação, preservado como referência. A primeira entrega operacional posterior, seus contratos e limites estão em [Cancelamentos — operação NOC](cancelamentos.md). O pedido de implementação consolidou as regras operacionais descritas nesse documento; a migração histórica continua dependente da identificação do banco real do legado.

## 1. Escopo e contratos obrigatórios

Esta etapa prepara a arquitetura e a implementação futura. Não cria app, tabelas, migrations, telas ou jobs; não executa importação histórica, sincronização externa ou alteração em produção.

- Importar ticket OTRS cria apenas uma entrada de triagem. **Importar ticket não cria cancelamento.**
- Somente uma decisão humana explícita, autorizada e auditada pode admitir uma entrada no fluxo oficial.
- Rejeição preserva o ticket, a justificativa, o analista, a data e a evidência da decisão, sem criar cancelamento.
- Fluxo oficial previsto: **Pedido → Solicitação → Pendências → Concluídos**. Campos, condições de transição, retornos, reabertura e exceções dependem do diagnóstico do legado.
- Estado, fila, encerramento ou classificação analítica do OTRS não aprovam triagem nem avançam o fluxo local automaticamente.
- OTRS e CMDB continuam oficiais em seus domínios. O ARION registra decisões e acompanhamento próprios, sem substituir o registro técnico externo.
- Nenhuma operação desta proposta muda o status de links para Cancelado, local ou remotamente. Reconciliação CMDB será uma etapa posterior e explícita.

## 2. Diagnóstico da arquitetura existente

O projeto é um monólito modular Django. `arion_geo/config/settings.py` registra os apps; `arion_geo/config/urls.py` compõe as rotas. Requisições operacionais leem espelhos locais; o processo `integration_worker` sincroniza fontes externas fora do HTTP. Não há necessidade de Celery, Redis, nova API pública, SPA ou scheduler paralelo para esta integração.

| Área inspecionada | Implementação atual | Consequência para Cancelamentos |
|---|---|---|
| Integrações | `aplicativos/integracoes/models.py`, `clients/`, `services/`, `views.py`, `urls.py` | Manter transporte, credenciais e espelhos no app existente |
| OTRS | `clients/otrs.py`, `clients/factory.py`, `services/otrs_sync.py`, comando `sincronizar_otrs` | Reutilizar leitura MySQL autorizada e upsert por TicketID |
| CMDB | `clients/cmdb.py`, `services/cmdb_sync.py`, `CMDBLinkMirror`, `CMDBOperatorMirror` | Reutilizar Search/Get e espelhos; nenhum novo cadastro paralelo |
| Consulta local | `integracoes/services/analytics.py`: `LinkQueryService`, `TicketQueryService` e `ticket_link_relations` | Compor consultas com escopo; observar os limites descritos abaixo |
| Chamados | `chamados/models.py`, `services.py`, `views.py`, `forms.py`, `urls.py` | Referência para tracking local, responsável, revisão concorrente, detalhe e auditoria |
| Qualidade | `qualidade/services/data_scope.py` e modelos próprios do módulo | Referência para adaptar escopo CMDB a registros de domínio e preservar snapshots |
| Acesso | `configuracoes/models.py`, `data_scope.py`, `access_control.py`, `permissions.py` | Django Groups/Permissions e escopo central existente |
| Auditoria | `auditoria/models.py`, `services.py`: `AuditEvent`, `AuditService.record`, `sanitize_metadata` | Usar serviço central para decisões e operações relevantes |
| Administração | `configuracoes/web_urls.py`, `integracoes/urls.py`, views e forms correspondentes | Evoluir Central de Configurações; `/admin/` permanece técnico |
| Interface | `templates/chamados/index.html`, `static/css/chamados/chamados.css`, `frontend_src/js/chamados/chamados.js` | Referência visual e de interação; extrair partial apenas onde houver reutilização real |
| Worker | `integracoes/management/commands/integration_worker.py`, `services/sync_lock.py` | Estender execução supervisionada existente, com checkpoints próprios |

Os caminhos `aplicativos/`, `templates/` e `static/` desta tabela são relativos a `arion_geo/`. Fontes JavaScript ficam na raiz `frontend_src/js/`, são referenciados por `arion_js` e passam pelo build existente em produção. APIs atuais são views Django com respostas locais, sem necessidade de acrescentar framework REST.

### 2.1 Cancelamentos já aparecem no espelho e no Cockpit

`integracoes/otrs_queues.py` define a cobertura atual de quatro filas: `NOC - ATACADO`, `NOC - CANCELAMENTOS NGO`, `NOC - RETIRA EQUIPAMENTO` e `NOC – PENDENTE PAGAMENTO`. A última usa travessão específico na origem; o código mantém alias histórico com hífen para consulta de espelhos antigos.

`integracoes/services/cancellation.py` já classifica tickets por fila e estado para exploração analítica: solicitações, tratamento, retirada de equipamento, pagamento e encerramento. Esses critérios documentam o recorte observado no OTRS; **não constituem o fluxo de aprovação do ArionBroker**. Preservar esse serviço e os indicadores atuais. Não substituir essas categorias pelas quatro etapas futuras nem contar tickets espelhados como cancelamentos aprovados.

A candidata à entrada de triagem é `NOC - CANCELAMENTOS NGO`, mas a fila oficial de admissão, seu QueueID, eventuais subfilas e os estados elegíveis precisam ser homologados. As filas de retirada e pagamento não devem ser tratadas automaticamente como novos pedidos. Uma URL de busca salva pode continuar como atalho para o OTRS; não será fonte nem contrato de sincronização.

### 2.2 Capacidades e limites reaproveitáveis

- `configured_otrs_client` utiliza `Integration`, credencial protegida e normalização de configuração. `OTRSReadOnlyClient` limita consultas e efeitos colaterais, parametriza valores e trata erros de conexão/TLS. Usar conta corporativa de serviço com privilégios mínimos de leitura; login web/SSO do analista não equivale a autorização de banco. Se a empresa exigir outro transporte, decidir após validar o acesso aprovado, mantendo o contrato do espelho. Não reutilizar cookies de navegador nem flexibilizar TLS para viabilizar o módulo.
- A leitura genérica bloqueia conteúdo sensível, artigos e anexos. Existe leitura específica de artigos para correlação da Qualidade; isso não autoriza seu uso em Cancelamentos. Se o diagnóstico exigir conteúdo, especificar acesso mínimo e tratamento privado próprios antes de ampliar o contrato.
- `OTRSSyncService` resolve nomes exatos de filas de forma única, lê dimensões e relações Ticket–CI, normaliza datas, faz upsert e aplica o snapshot em transação. `OTRSTicket.external_id` é único; `ticket_number` é número operacional, não substituto de TicketID.
- `IntegrationRun`, `IntegrationSyncState` e `IntegrationLease` já registram execução, cursor, falhas e exclusão de concorrência. O worker agenda FAST a cada 2 minutos, FULL a cada 30 e CMDB a cada 10; utiliza backoff até 30 minutos. São cadências existentes, não promessa de SLA do futuro módulo.
- FAST exige FULL com cobertura válida, usa sobreposição temporal de 120 segundos e consulta também tickets conhecidos que mudaram de fila. FULL reconcilia ausências sem apagar o histórico local. `is_present_in_queue` significa presença no conjunto de filas sincronizado; a triagem deve verificar também a fila de admissão homologada.
- Snapshots operacionais diários (`OTRSOperationalSnapshot` e filhos) são observações locais; não são histórico completo de movimentos e não recompõem automaticamente todas as passagens anteriores pela fila.
- `LinkBusinessIdentityService`, `TicketBusinessIdentityService` e `project_names.py` ajudam a normalizar identidade. Parsing de nome/título não prova vínculo nem concede acesso. `CMDBOperatorMirror` representa ISP/provedor; as dimensões `base_links.Operadora` e `Projeto` já sustentam o escopo existente.

## 3. Localização e separação propostas

Criar futuramente o app **`arion_geo/aplicativos/cancelamentos/`**, com namespace próprio `cancelamentos`, mantendo integrações externas no app `integracoes`. Cancelamentos possui decisões e ciclo de vida próprios; colocá-los no sincronizador ou em `chamados` misturaria espelho técnico e registro de negócio.

```text
OTRS autorizado → sincronizador existente → espelho OTRS local
                                             ↓
                                  projeção idempotente de triagem
                                             ↓
                              aguardando decisão do analista
                                   ↙                   ↘
                         rejeição documentada      aprovação explícita
                                                          ↓
                                     Pedido → Solicitação → Pendências → Concluídos
CMDB → espelhos existentes → referências de links e identidade, sem escrita remota
```

| Componente futuro | Responsabilidade |
|---|---|
| `models.py` | Entradas de triagem, decisões, referência de origem e domínio após diagnóstico |
| `forms.py` | Validação de aprovação/rejeição, justificativas, responsáveis e filtros |
| `services/triage.py` | Admissão humana, rejeição e controle de concorrência |
| `services/otrs_intake.py` | Projetar tickets do espelho para triagem, sem criar registros oficiais |
| `services/queries.py` | Universo escopado, pesquisa, filtros, indicadores e paginação |
| `services/data_scope.py` | Adaptar `DataScopeService` aos vínculos/snapshots de Cancelamentos |
| `services/workflow.py` | Transições explícitas homologadas; implementar após diagnóstico |
| `services/history.py` | Histórico funcional e integração com `AuditService` |
| `services/exports.py` | Exportar a mesma consulta escopada e filtrada da interface |
| `services/legacy_import.py` e management command | Preview, dry-run, apply controlado e reconciliação histórica |
| `views.py`, `urls.py` | HTML, ações POST e JSON local apenas quando necessário |
| `templates/cancelamentos/`, `static/css/cancelamentos/`, `frontend_src/js/cancelamentos/` | Interface no padrão ARION OPS e build existente |

Esses nomes são proposta de organização, não arquivos ou rotas já disponíveis. Não criar cliente OTRS adicional, scheduler independente, autenticação ou sistema próprio de permissões. Não acoplar a aprovação a signals de criação do espelho.

## 4. Modelo preliminar de triagem

O desenho abaixo define invariantes técnicas. Tipos definitivos, cardinalidade do domínio e migrations aguardam o diagnóstico.

| Entidade conceitual | Campos e vínculos mínimos propostos |
|---|---|
| Entrada de triagem | TicketID externo único no OTRS atual, referência opcional ao mirror, número para exibição, fila de admissão observada, primeira/última observação, última versão da origem, situação da triagem e revisão concorrente |
| Decisão de triagem | Entrada, decisão, usuário Django, data, justificativa e snapshot mínimo da evidência observada |
| Referência de link | Entrada ou registro oficial, ConfigItemID de origem, vínculo ao `CMDBLinkMirror`, situação de resolução e evidência de associação |
| Registro oficial | Criado apenas na aprovação; referência à decisão e à entrada, responsável Django e etapa local. Campos de negócio dependem do legado |
| Origem histórica / lote | Identidade da origem e PK legada, versão do mapeamento, checksum, execução, destino e resultado da reconciliação |

Situações preliminares da triagem: **Aguardando triagem**, **Aprovado**, **Rejeitado**. Falta de vínculo, origem desatualizada e saída da fila são condições de qualidade/origem, não aprovação ou rejeição automática. Responsável pela triagem e responsável pelo cancelamento são referências locais; não inferir equivalência entre IDs de usuários OTRS e Django.

Regras técnicas propostas:

1. Uma entrada por TicketID. Se houver múltiplas instâncias OTRS no futuro, adotar identidade composta por origem + TicketID antes de integrá-las.
2. Aprovar exige autenticação, permission, escopo válido, entrada pendente, revisão atual e os dados mínimos homologados. Na mesma transação: bloquear/revalidar entrada, registrar decisão, criar vínculo oficial e auditar. Constraint de unicidade impede segunda admissão por repetição HTTP ou concorrência. A relação de um ticket com múltiplos cancelamentos/links permanece pendente de diagnóstico; não presumir um cancelamento por link.
3. Rejeitar exige justificativa e registra decisão/histórico sem registro oficial. Nova sincronização não apaga nem reabre a rejeição. Reconsideração, se homologada, será ação explícita auditada que preserva decisões anteriores.
4. Mudanças da origem atualizam observações técnicas, nunca sobrescrevem decisão, responsável local ou etapa oficial. Usar controle de revisão para evitar aprovar evidência que mudou durante a análise.
5. Saída da fila, ticket encerrado ou mirror ausente preservam entrada e evidências. Na ausência de política homologada, bloquear aprovação de evidência ausente/desatualizada e apresentar a razão; não rejeitar nem concluir automaticamente.
6. Não fazer exclusão em cascata de decisões ou registros oficiais ao remover mirrors. Preservar identificadores externos e snapshot mínimo; escolher `PROTECT`/`SET_NULL` conforme a referência na implementação.

## 5. Estratégia de sincronização e recuperação

Manter duas responsabilidades independentes: **sincronizar espelho** e **projetar entradas para triagem**. A projeção consome somente dados locais de uma execução OTRS bem-sucedida e com cobertura homologada. Registrar checkpoint próprio, usando a infraestrutura de estado/lease existente, sem reutilizar o cursor OTRS como confirmação de triagem.

Sequência proposta:

1. Homologar conta de serviço, transporte corporativo, fila exata, QueueID, estados, subfilas e marco inicial de importação. Validar a identidade da fila, sem seleção por título ou URL salva. Mudança de cobertura exige FULL controlado pelo mecanismo existente.
2. Sincronizar espelho pelo worker atual, preservando cursor em falhas. Não alterar a cobertura global do Cockpit sem validar seus consumidores.
3. Executar a projeção em job do mesmo worker após sync válido, com lease e checkpoint próprios. Reter referência à execução/cobertura e processar em lotes limitados; reexecução faz upsert apenas por TicketID e não altera decisões.
4. Confirmar checkpoint somente após commit de cada lote. Se houver falha após commit e antes do checkpoint, repetir o lote com segurança. Se a projeção falhar depois do sync, manter o sync concluído e repetir a projeção pendente mesmo sem novos tickets externos.
5. Fazer reconciliação periódica local para recuperar entradas não projetadas e conferir totais, preservando rejeitados/aprovados. Limitar as consultas e aproveitar índices de fila, TicketID, decisão e datas.
6. Em indisponibilidade externa, manter o último estado local e informar freshness. Em falha de projeção, registrar resumo sanitizado, quantidade pendente, última execução válida e próxima tentativa. Não converter erro em fila vazia.

A execução atual é baseada em polling: um ticket que entre e saia da fila entre observações pode não ser visto. O relatório deve definir se é obrigatório capturar toda passagem; nesse caso, será necessário ampliar a leitura autorizada de histórico/eventos com checkpoint próprio, sem afirmar que FAST ou snapshot diário já resolvem essa lacuna.

Monitorar separadamente conexão OTRS, sucesso do mirror e sucesso da projeção: última execução, duração, cobertura, criados/atualizados/inalterados, falhas, backlog de projeção, entradas sem vínculo e atraso da fonte. Usar `IntegrationRun` e `IntegrationSyncState` onde o contrato couber; novos tipos de operação/chaves serão adicionados na implementação. Contadores visíveis devem respeitar o escopo; diagnósticos técnicos globais exigem as permissões administrativas correspondentes.

## 6. RBAC e Data Scope

### 6.1 Permissões funcionais

Usar Django Auth, Groups e Permissions. Propor permissions no app `cancelamentos`, seguindo o precedente de permissões de domínio de Qualidade/Relatórios:

| Capacidade proposta | Alcance |
|---|---|
| `view_cancellations` | Consultar triagem, registros, detalhes, indicadores e histórico autorizado |
| `triage_cancellations` | Aprovar/rejeitar entradas dentro do escopo |
| `manage_cancellations` | Atribuir responsáveis e executar transições homologadas |
| `export_cancellations` | Exportar dados autorizados |
| `import_cancellation_history` | Executar importação histórica controlada em ambiente autorizado |

Codenames preliminares, ainda inexistentes. Incorporá-los ao catálogo e à seleção administrativa de `configuracoes/access_control.py` e ao provisionamento idempotente existente. O bootstrap atual não concede automaticamente toda permission nova aos grupos existentes; prever atualização explícita, conservadora e revisável. Não conceder triagem/importação apenas por acesso ao Cockpit, nem aplicar comportamento por nome de grupo ou username.

Manter `configuracoes.view_integrations`, `manage_integrations`, `run_integrations`, `inspect_integrations` e `view_audit_log` nos contextos administrativos existentes. Permission de configurar integração não aprova cancelamento. Verificar acesso em cada view, JSON, detalhe, POST e export; usar CSRF, validação server-side e negar acesso manual fora do escopo. Superuser técnico mantém bypass global nativo, sem exigir cadastro de escopo.

### 6.2 Recorte dos dados

O código atual usa **`GroupDataScope`**, modos `ALL`/`CUSTOM`; `LegacyUserDataScope` é legado, não base de uma implementação nova. `DataScopeService` restringe grupos múltiplos por interseção; grupo sem configuração resulta em acesso restritivo. Em `CUSTOM`, cliente contratante e projeto são combinados com `AND`; conjuntos vazios não ampliam acesso. No mirror CMDB, o recorte considera `contracting_client`/`contracting_client_raw` e `business_project`, com fallback a `legacy_project` somente sem projeto normalizado. Não confundir ISP em `CMDBLinkMirror.operator` com a dimensão de cliente/operadora do escopo.

`TicketQueryService.base(user, all_queues=True)` é uma referência útil, mas não pode ser usado sem adaptação: por padrão consulta apenas NOC; mesmo com `all_queues` exige presença atual e autoriza tickets pela existência de pelo menos um link permitido. Triagem e histórico precisam de consultas próprias que preservem decisões e observações anteriores.

- Aplicar escopo antes de pesquisa, filtros, contagens, indicadores, ordenação, paginação, detalhe e exportação. Compartilhar o mesmo universo entre esses consumidores.
- Ticket sem link/identidade resolvida não aparece para `CUSTOM`. Manter entrada em triagem restrita a operador com permission de triagem e escopo global, até resolução documentada. Não distribuir acesso por título, cliente inferido ou ausência de vínculo.
- Ticket com múltiplos links exige avaliação conservadora: um vínculo permitido não autoriza mostrar vínculos, evidências ou dados de outros clientes. Proposta inicial: triagem completa de ticket com vínculos mistos fica com operador global; usuários restritos acessam apenas registros com contexto integralmente autorizado. Confirmar granularidade após diagnóstico.
- Nunca buscar `get(pk=...)` irrestrito seguido apenas de botão oculto. Obter o objeto no queryset autorizado e revalidar o contexto dentro da transação de decisão.
- Snapshots históricos preservam evidência, não grants antigos. Reavaliar acesso atual e evitar que mudança/remoção de vínculo amplie acesso; sem identidade confiável, restringir ao contexto global autorizado.

## 7. Relacionamento CMDB

Preferir `OTRSTicketConfigItem.config_item_id` e `cmdb_link` para candidatos a vínculo; a relação pode estar ausente, obsoleta, não resolvida ou conter vários CIs. Identificar links por `CMDBLinkMirror.source_config_item_id`; não vincular apenas por designação, circuito, nome ou título semelhantes.

Reutilizar clientes/projetos/provedores existentes e seus mecanismos de normalização. Divergência vira pendência de reconciliação, sem criar cadastro substituto. A decisão deve preservar ConfigItemID e snapshot mínimo do contexto. Não escolher arbitrariamente o primeiro link e não modificar `deployment_state`, `incident_state` ou o status remoto por conclusão de um cancelamento.

## 8. Auditoria e histórico

Usar `AuditService.record` com módulo Cancelamentos, objeto, ator, resultado, ação e metadata mínima. Eventos relevantes: decisão de triagem, reconsideração homologada, alteração de responsável, transição de etapa, correção de vínculo, exportação relevante, importação/reconciliação e alteração administrativa da entrada OTRS.

Registrar identificadores, revisão, decisão/etapa anterior e posterior, execução de origem, lote e justificativa controlada. O histórico funcional pode ter entidade própria para timeline e integridade do domínio, referenciando o evento central; não criar outro mecanismo de auditoria administrativa. Registrar decisão, histórico e auditoria de sucesso na mesma transação. Falhas relevantes exigem evento sanitizado fora da transação revertida, sem registrar sucesso inexistente.

`sanitize_metadata` remove chaves sensíveis recursivamente, mas não garante remoção de segredo inserido em texto livre, descrição ou mensagem. Adotar allowlist de campos; não enviar payload bruto, corpo de artigo, cookie, senha, token, header Authorization, connection string ou credenciais para logs, metadata ou frontend. Justificativas/evidências precisam de validação e política de minimização. Definir retenção e acesso a dados pessoais antes de importar anexos ou comunicações.

Histórico e auditoria exibidos dentro do módulo respeitam o mesmo Data Scope. A consulta administrativa central permanece sob sua permission existente; validar sua política de exposição antes de incluir detalhes sensíveis. Eventos técnicos recorrentes ficam nas execuções de integração, sem gerar auditoria por hover, filtro ou cada repetição idempotente.

## 9. Interface proposta

Uma entrada de navegação **Cancelamentos**, com lista principal compartilhada e abas **Entrada OTRS**, **Pedido**, **Solicitação**, **Pendências** e **Concluídos**. Em Entrada OTRS, situação da triagem permite consultar aguardando, aprovados e rejeitados sem criar outra tela de registros inválidos.

Usar uma única barra de pesquisa/filtros, com parâmetros validados e preservados na URL. Pesquisa por número OTRS, identificador do registro e identidade autorizada; filtros por período com data explicitada, etapa/situação, cliente/operadora, projeto, ISP, responsável e qualidade do vínculo conforme o contexto. Opções e contadores derivados do universo autorizado. Evitar barras separadas para tabela e indicadores.

O detalhe reúne dados do ticket, referências CMDB, responsável, evidência da decisão e timeline. Aprovar/rejeitar são ações explícitas no detalhe, com justificativa e feedback de conflito. Etapas oficiais usam o mesmo detalhe, com seções condicionais conforme regra futura; não replicar quatro formulários ou telas equivalentes.

Indicadores discretos: aguardando triagem, rejeitados, aprovados e volume por etapa; distinguir ticket, entrada e registro oficial como unidades. Definir data-base e regras de contagem antes de calcular tempo em etapa, produtividade ou taxas. Indicadores financeiros, prazos contratuais e SLA aguardam regras do legado. Não somar universo do Cockpit com registros aprovados como se fossem o mesmo conceito.

Exportação usa o mesmo service de consulta, filtros e escopo, com colunas mínimas, origem e data de referência. Proteger CSV contra fórmulas e exportações muito grandes por limites explícitos; escolher formato conforme recursos já disponíveis.

Preservar `base.html`, tokens de `base.css`/`branding.css`, `UserPreference`, temas claro/escuro e JavaScript vanilla. Tabela com paginação no servidor, hierarquia simples, ações pequenas, estados vazios, indicação de desatualização, foco visível, labels e teclado. Validar 1920×1080, 1440×900, 1366×768 e 1024×768, confinando scroll à tabela. Inserir menu apenas quando a rota existir; smoke tests das páginas autenticadas ao alterar navegação global.

## 10. Migração histórica do ArionBroker

Não executar sem esquema real, amostra controlada e dicionário do legado. Não usar `migrar_banco_arion`/`arion_database_migration.py` como importador: o mecanismo existente migra o próprio banco ARION e não traduz domínio do ArionBroker. Reaproveitar somente padrões seguros de diagnóstico, etapas e reconciliação que se mostrarem adequados.

### 10.1 Insumos e mapeamento

Obter tabelas, PKs/FKs, enums, constraints, volumes, datas/fusos, exclusões lógicas, usuários, anexos e histórico. Mapear TicketID e número OTRS separadamente, ConfigItemID, cliente, projeto e provedor para identidades existentes. Mapear responsável legado para usuário Django por tabela revisada; sem correspondência, preservar rótulo histórico e deixar associação pendente, sem criar contas automaticamente.

Definir unidade do registro e cardinalidade ticket/pedido/solicitação/link antes do schema final. Estados desconhecidos e associações ambíguas permanecem em staging restrito. Registros históricos já válidos podem ser admitidos como origem legada após validação do lote, preservando evidências existentes; não inventar aprovação humana atual ou obrigar um evento histórico inexistente. A aprovação humana de novas entradas OTRS continua obrigatória.

### 10.2 Pipeline proposto

1. **Extração autorizada:** snapshot/export consistente ou conexão somente leitura ao legado, fora da request; acesso limitado e armazenamento privado. Nunca copiar credenciais/sessões do ArionBroker.
2. **Preview:** apresentar amostra minimizada, volumes por estado/período, candidatos a associação, duplicidades, campos ausentes e regras propostas; sem escrita nas tabelas oficiais.
3. **Dry-run:** executar o mesmo mapeamento/validação do apply, sem alterar domínio, mirrors ou decisões. Emitir relatório de criáveis, já existentes, conflitos, inválidos e não reconciliados; artefatos de diagnóstico, se persistidos, ficam privados e identificados.
4. **Validação:** PK/FK, órfãos, campos obrigatórios, datas/fusos, ordem temporal comprovável, enum, cardinalidade, identidades CMDB e totais. Se houver valores financeiros, validar precisão e reconciliação conforme semântica homologada, sem criar cálculos.
5. **Apply controlado em DEV:** após revisão do relatório, backup e recuperação verificável; lotes transacionais com checkpoint, checksum da extração e versão do mapeamento. Nenhuma ação remota OTRS/CMDB.
6. **Reconciliação:** total extraído = importados novos + já conciliados + conflitos + rejeitados pela validação, em categorias mutuamente exclusivas. Conferir também totais de filhos/históricos, por etapa e período, com amostras por identidade. Não ignorar registros silenciosamente.
7. **Reexecução e recuperação:** chave única por origem + entidade + PK legada, mapa legado→destino e hash do conteúdo. Repetição não duplica; mudança conflita ou segue política explícita, sem sobrescrever decisão recente. Reversão por lote preserva dependências e registros já tratados; preferir correção reconciliada quando rollback apagaria trabalho válido.

Ticket já presente na triagem e no histórico deve ser reconciliado pelo TicketID comprovado, sem segundo cancelamento por importação. Não deduplicar exclusivamente por título, número parecido ou designação. Política para múltiplos registros legítimos do mesmo ticket depende da cardinalidade real. Histórico faltante deve ser indicado como indisponível, sem reconstrução fictícia de etapas.

### 10.3 Transição futura

Definir marco de corte, destino de alterações concorrentes e responsável por divergências antes do apply definitivo. Preferir ensaio DEV com snapshot e relatório aprovado; planejar janela/freeze ou delta legado quando necessário. Importação histórica não pode disparar automações, notificações, escrita externa ou mudança de status CMDB. Produção requer tarefa e autorização próprias.

## 11. Dependências do diagnóstico ArionBroker

O relatório e o prompt do legado precisam esclarecer:

- Esquema físico, modelos, serviços e regras efetivamente executadas, incluindo operações manuais fora do sistema.
- Diferença exata entre Pedido e Solicitação; pré-requisitos, campos, responsáveis e permissões de cada etapa.
- Natureza de Pendências, condições de conclusão, retornos, reabertura e exclusões.
- Cardinalidade entre tickets, registros, clientes, links e solicitações; chaves legadas e evidências da origem OTRS.
- Fila/QueueID de entrada, estados elegíveis, subfilas, tickets já encerrados, primeiro carregamento e necessidade de histórico de passagem.
- Motivos e política de rejeição/reconsideração; dados mínimos e evidência necessária à aprovação.
- Regras de responsabilidade, datas, prazo, valores, contratos, retirada de equipamento e pagamento, sem inferi-las dos nomes das filas.
- Política de escopo para múltiplos links/clientes, registros sem vínculo e históricos sem referência atual.
- Histórico, anexos, usuários inativos, duplicidades, retenção, volumes e reconciliação de contagens.
- Contas corporativas autorizadas, requisitos de transporte, janela de extração e estratégia de corte do legado.

Até esses pontos serem resolvidos, não definir campos financeiros, automatizar transições nem tratar a classificação do Cockpit como regra de negócio consolidada.

## 12. Ordem de implementação futura

1. Revisar diagnóstico e aprovar dicionário, fila de entrada, cardinalidades e contratos de acesso/decisão.
2. Consolidar modelo de domínio e mapeamento histórico; revisar riscos sobre os consumidores atuais de OTRS/CMDB.
3. Criar app, models, constraints e migrations reais com `makemigrations`; integrar catálogo de permissions e adapter de Data Scope.
4. Implementar projeção local para triagem, checkpoint, reprocessamento e diagnóstico no worker existente, ainda sem criar cancelamentos.
5. Implementar aprovação/rejeição transacional, concorrência, histórico e auditoria.
6. Entregar lista, filtros e detalhe no design atual; integrar navegação somente com rotas protegidas disponíveis.
7. Implementar Pedido → Solicitação → Pendências → Concluídos conforme regras homologadas, responsáveis e exportação; indicadores compartilham consultas.
8. Implementar importador histórico e ensaiar preview/dry-run/reconciliação em DEV; revisar resultados antes de apply.
9. Executar validações focadas e consolidar documentação do comportamento implementado. Reconciliação CMDB e produção permanecem etapas separadas.

## 13. Riscos e critérios de aceite

| Risco | Controle e evidência esperada |
|---|---|
| Importação virar cancelamento sem análise | Job de ingestão cria somente triagem; cenário sem intervenção humana produz zero registros oficiais |
| Aprovação dupla / concorrência / retry | Constraint + transação + revisão; duas aprovações concorrentes resultam em uma única admissão |
| Rejeição perdida em nova sincronização | Ticket rejeitado permanece documentado após FAST, FULL e replay da projeção |
| Fila incorreta ou renomeada | Identidade exata homologada e cobertura validada; falha explícita, sem fallback para outra fila |
| Lacuna entre sync e projeção | Checkpoint independente, repetição de lote e reconciliação recuperam falha sem duplicar |
| Passagem pela fila entre polls | Limite comunicado e requisito de histórico/eventos decidido antes de prometer captura integral |
| Vazamento por links mistos, ausência de vínculo ou export | Casos `ALL`, `CUSTOM`, sem escopo, grupos múltiplos e superuser; mesma proteção em detalhe, POST, opções, KPI e export |
| Duplicação de cadastros ou vínculo incorreto | ConfigItemID e identidades existentes; ambiguidades reportadas, sem cadastro/associação automática |
| Confusão entre Cockpit e fluxo aprovado | Classificação analítica preservada; origem, unidade e universo dos indicadores explícitos |
| Alteração indevida da CMDB/OTRS | Nenhuma escrita remota ou mudança de status de link em ingestão, aprovação, conclusão ou importação |
| Histórico incompleto ou migração destrutiva | Preview/dry-run, totais reconciliados, origem/PK/checksum, replay idempotente e recuperação de lote |
| Exposição de secrets e dados pessoais | Allowlist, metadata sanitizada, arquivos privados e ausência de payloads/credenciais em logs e interface |

Aceite desta preparação: documento distingue capacidades existentes de propostas; identifica reaproveitamentos e limitações; mantém decisão humana obrigatória; explicita dependências sem inventar regras; não altera código funcional, banco, produção ou integração remota.

Aceite da implementação futura: cenários da tabela validados em testes focados do módulo e da integração alterada; `check` sem problemas; migrations consistentes; ensaio no MySQL DEV real sem fallback automático a SQLite; telas claro/escuro utilizáveis; rotas protegidas; smoke tests globais somente quando houver alteração compartilhada. Simular falhas externas com fixtures controladas, sem disparar sincronização ou teste extenso de módulos alheios.

## 14. Referências internas

- [Arquitetura atual](../02-arquitetura/arquitetura.md)
- [Fontes de dados](../02-arquitetura/fontes-de-dados.md)
- [Worker e sincronização](../02-arquitetura/worker-e-sincronizacao.md)
- [Sincronização OTRS](../06-integracoes-e-dados/otrs/sincronizacao.md)
- [Sincronização CMDB](../06-integracoes-e-dados/cmdb/sincronizacao.md)
- [Diretrizes de interface](../04-interface/diretrizes-de-interface.md)

Os achados desta análise foram conferidos no código citado, sem consulta às origens remotas. Estudos históricos e perfis de busca OTRS são referências auxiliares, não substituem a homologação do contrato de entrada ou o esquema real do ArionBroker.

## 15. Validação desta etapa

- `.venv\Scripts\python.exe arion_geo\gerenciar.py check`: concluído sem problemas (`0 silenced`).
- A inspeção protegida da configuração identificou DEV com backend ativo SQLite, não MySQL local. A verificação encerrou antes de abrir conexão; não foi feita substituição de backend, ativação de candidato ou validação de dados usando SQLite como alternativa ao MySQL.
- Alteração restrita a este documento; sem migrations, execução de importador, worker ou chamadas OTRS/CMDB. Testes funcionais e suites extensas não se aplicam à entrega documental.
