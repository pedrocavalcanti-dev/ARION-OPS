# Cancelamentos — operação NOC

Estado: segunda entrega operacional em DEV (correção da elegibilidade OTRS e refinamento da lista). O módulo acompanha cancelamentos de circuitos com triagem humana, tratativa ISP, equipamentos, pendências simultâneas, obrigações documentais e encaminhamento manual ao Financeiro. Não houve deploy nem carga histórica. Não representa a versão 1.4.0.

## Acesso e telas

- `/cancelamentos/`: lista, pesquisa, indicadores e abas Entrada OTRS, Pedido, Solicitação, Pendências e Concluídos.
- `/cancelamentos/entrada/<id>/`: detalhe da entrada, origem, circuitos candidatos, responsável e decisões.
- `/cancelamentos/processos/<id>/`: processo oficial, tratativa ISP, equipamentos, pendências, evidências, histórico e informações financeiras autorizadas.
- `/cancelamentos/exportar/`: CSV operacional do mesmo recorte da lista, até 10.000 registros, sem campos financeiros.
- `/cancelamentos/configuracao/`: ativação da projeção local e validade da observação da origem, sob permission específica.

Os caminhos usam IDs locais; TicketID OTRS e Ticket# são preservados separadamente. Navegação principal, mobile e Visão Geral oferecem acesso apenas com `cancelamentos.view_cancellations`. É possível conceder apenas este módulo, sem liberar Dashboard, Chamados ou outros módulos.

## Entrada OTRS

A fonte é o espelho `OTRSTicket`, com relações `OTRSTicketConfigItem`. O módulo não abre conexão externa durante requests. Usa a fila exata **NOC - CANCELAMENTOS NGO**, definida em `integracoes/otrs_queues.py`; o QueueID observado vem do espelho e não é inferido por URL/perfil de busca.

A entrada atual reproduz o **KPI 1**, combinando fila exata, presença atual e estado **em atendimento**. Reutiliza `cancellation_stage_expression`/`classify_cancellation_stage` e `CANCELLATION_REQUEST_STATES`, já utilizados pela integração analítica; os IDs vêm das dimensões resolvidas pelo sincronizador, não de URLs ou títulos. Estados terminais ou mapeamento UNKNOWN não são admitidos automaticamente. Não se inventa exclusão por procedência: o KPI verificado não filtra responsabilidade. Aprovar continua sendo decisão humana explícita.

O backlog verifica também o mirror atual, para não manter como nova entrada um ticket que saiu do KPI entre duas projeções. O worker reconcilia `eligible`, preservando decisões, históricos e timestamps anteriores. Em **Mais filtros → Presença no KPI 1**, pode-se consultar entradas atuais, histórico fora do KPI ou ambos. A situação da triagem é independente da presença atual.

Na consulta ao espelho DEV em 09/11/2026 foram observados os QueueIDs 164 (Cancelamentos NGO), 165 (retirada) e 168 (pendente pagamento), além de 107 (NOC Atacado). São evidência do snapshot, não constantes novas no módulo. O KPI “NOC CANC. EM TRATATIVA” já é classificado no serviço analítico por estados da fila de Cancelamentos; não foi criada uma quinta fila com esse rótulo. Na segunda entrega, os perfis foram conferidos na origem pelo client existente em consultas MySQL somente leitura, sem alteração remota.

O worker existente chama a projeção local depois de avaliar as sincronizações. A projeção exige integração habilitada, registro de FULL aplicado com a cobertura atual do Cockpit e espelho recente. Cadência de projeção: 2 minutos; falhas usam backoff progressivo até 30 minutos. A validade padrão da observação é 60 minutos e pode ser configurada entre 2 e 1440 minutos. A configuração não muda credenciais, filas ou cobertura global.

Para reconciliação local explícita em DEV, sem leitura externa:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py projetar_cancelamentos
```

O comando força a avaliação local, mas mantém as mesmas exigências de origem válida. Não é importador histórico nem comando de aprovação.

Há lease e estado próprios (`cancellation_intake`) nos mecanismos centrais. Cada entrada já gravada é recuperável por TicketID, mesmo se a execução falhar antes de confirmar o checkpoint geral. Execuções aparecem em `IntegrationRun` como `RECONCILE`, identificadas por `request_id=cancellation_intake`. O job nunca cria `CancellationProcess` e não gera eventos funcionais repetidos em reexecuções.

Tickets sem origem configurada resultam em estado vazio explicativo. Saída da fila, perda de mirror e novas sincronizações preservam aprovações/rejeições e seus históricos. Polling não garante captura de passagem pela fila entre duas observações; leitura adicional de eventos é uma evolução futura, não uma capacidade desta entrega.

## Diagnóstico da divergência 120 × 15

Referências em **09/11/2026, America/Sao_Paulo**: diagnóstico local às **11:19:18**, comparação somente leitura com a origem às **11:20:09** e conferência após reconciliação às **11:34:48**. São fotografias datadas, não contagens fixas do produto.

| População | Antes | Após reconciliação |
|---|---:|---:|
| Total de tickets do espelho OTRS | 12.800 | 12.800 |
| Tickets presentes na fila 164 | 120 | 120 |
| Tickets elegíveis pelo estado do KPI 1 | 15 | 15 |
| Entradas persistidas | 120 | 120 |
| Backlog apresentado como triagem atual | 120 | 15 |
| Históricas fora do KPI 1 | Sem separação | 105 |
| Entradas que saíram da fila física | 0 | 0 |
| Decisões aprovadas / rejeitadas | 0 / 0 | 0 / 0 |
| Entradas WAITING persistidas (inclui histórico) | 120 | 120 |
| Processos oficiais | 0 | 0 |

Causa comprovada: a primeira projeção exigia somente nome da fila e `is_present_in_queue`, enquanto a listagem contava todos os WAITING sem recortar elegibilidade. Os 120 continuavam fisicamente na fila: **86 closed successful + 2 resolvido + 8 cancelado + 9 em tratativa + 15 em atendimento**. A diferença era o filtro de estado do KPI, não outra fila, Data Scope ou diferença entre os TicketIDs comparados. Os 105 registros fora do KPI foram mantidos com `eligible=False`, sem exclusão ou pedido automático.

Definições verificadas em `kpi.search`:

| KPI / perfil observado | Fila | Estados da pesquisa | Tickets |
|---|---|---|---:|
| 1 · Cancelamentos NGO (681) | QueueID 164 | StateID 10 — em atendimento | 15 |
| 2 · Em tratativa (725) | QueueID 164 | 13, 17, 18, 19, 11, 21, 22 | 9 |
| 3 · Retira equipamento (680) | QueueID 165 | Conjunto já catalogado em `CANCELLATION_PROFILE_ACTIVE_STATES` | 1 |
| 4 · Pendente pagamento (697) | QueueID 168 | Mesmo conjunto ativo catalogado | 0 |

Os TicketIDs do KPI 1 na origem e no espelho foram iguais: **15 em comum, zero faltantes e zero extras**. Os números 681/725/680/697 identificam perfis e nunca são tratados como QueueID. KPI 1 → KPI 2 pode ocorrer por mudança de estado na mesma fila, sem mudança de responsável ou QueueID. As outras filas oferecem contexto e não promovem etapas locais ou admissão automática.

A reconciliação é retomável por TicketID. Houve disputa transitória de escrita no SQLite com o worker DEV ainda carregado com código anterior. Após verificar sua identidade, somente esse processo DEV foi reiniciado, com backup e liberação das leases do processo encerrado. A reexecução preservou os lotes já aplicados e as 120 entradas. Nenhum serviço de produção foi alterado.

## Triagem e circuito principal

A lista oferece **Todos / Meus / Sem responsável**, sobre `assigned_to` na triagem e `owner` nos processos, com Data Scope aplicado antes dos filtros. A barra principal reúne busca por Ticket#, TicketID, código do pedido, circuito, cliente ou ISP, Cliente/Projeto e Responsável. Não há consulta a cada tecla. **Mais filtros** começa fechado, indica filtros ativos e contém apenas opções adequadas à aba; valores ficam na URL e na paginação. Limpar mantém a aba.

Atribuir/Reatribuir reutiliza o catálogo existente e valida o acesso do destino no backend. Autor, data, responsáveis anterior/novo e revisão são auditados. Conflitos são rejeitados e atribuições sem alteração não duplicam eventos. Após confirmar, o recorte e a página são preservados; a linha pode sair naturalmente do filtro Sem responsável. Assumir não toma registro de outro analista e não altera proprietário OTRS.

O analista pode assumir, manter pendente com observação, rejeitar com justificativa ou aprovar. Rejeição permanece documentada e não reaparece como nova entrada em sync posterior. Reconsideração/reabertura de decisão não foi disponibilizada nesta entrega.

A aprovação exige origem recente e consistente com a revisão apresentada. O processo é criado na mesma transação da decisão e auditoria. Há unicidade do TicketID na entrada, relação um-para-um entrada/processo e controle de revisão para repetição HTTP e concorrência.

Um processo por ticket é a regra inicial. O operador escolhe explicitamente um circuito principal entre relações Ticket–CI atuais; não há associação por semelhança de designação. Outros circuitos vinculados podem ser consultados na triagem global, mas não geram processos adicionais. A admissão de vários processos por ticket dependerá de modelagem/decisão posterior.

Um operador global autorizado pode admitir sem vínculo confiável com justificativa, mantendo as lacunas visíveis. A conclusão normal exige identificação completa e circuito validado. A ação Revisar associação CMDB permite completar o vínculo a partir de candidatos atuais, com justificativa, permissões de triagem e alteração de etapa. Clientes, projetos, ISP e usuários são reutilizados; nenhum cadastro paralelo é criado.

## Etapa principal e pendências

`CancellationProcess.stage` diferencia **Pedido**, **Solicitação** e **Concluído**. **Pendências** é uma visão dos processos em andamento com pendências abertas, e pode coexistir com Pedido/Solicitação. Assim o operador mantém o contexto da tratativa mesmo quando há retirada, documento e boleto simultâneos.

Pedido → Iniciar tratativa → Solicitação → Concluir NOC. As mudanças são explícitas, autorizadas e locais. Nenhuma mudança de fila OTRS avança o processo. Concluídos ficam disponíveis para consulta; esta entrega não oferece reabertura ou edição posterior da conclusão.

O detalhe permite responsável atual, cliente final, próxima ação e observações anexadas à timeline. As identidades CMDB permanecem snapshots controlados pela associação, sem edição livre de cliente/projeto que contorne o escopo. Retornos ISP são preservados no histórico de cada atualização, além do estado atual de tratativa.

## Tratativa ISP e equipamentos

`ISPTreatment` registra protocolo, contato, solicitação, retorno, confirmação do serviço, data efetiva, confirmação/data do fim do faturamento e referência de evidência. Confirmação do serviço exige data efetiva, momento da confirmação e evidência. Encerramento de faturamento possui confirmação separada.

`CancellationEquipment` admite vários equipamentos por processo. Situações: a retirar, retirada solicitada, agendada, retirado/devolvido, dispensada pelo ISP, impossibilidade de retirada e não devolvido. Agendamento exige data; devolução e dispensa exigem evidência, e devolução exige sua data. Não há equipamento presumido nem obrigação financeira criada automaticamente por uma situação de retirada.

## Pendências

`CancellationPending` mantém tipo, descrição, valor opcional Decimal, previsão, responsável, situação, criação, resolução, evidência e justificativa de providência. Categorias iniciais normalizadas:

- Mensalidades atrasadas, multa equipamento e multa contrato.
- Aguardando distrato e aguardando termo para baixa.
- Retirada de equipamento, boleto pendente e documentação pendente.
- Outros, com descrição obrigatória.

Pode haver várias abertas simultaneamente. Resolver ou encaminhar à área responsável exige justificativa e evidência, com data registrada. Encaminhada não significa pagamento realizado. Pendência monetária não cria obrigação automaticamente: a obrigação é o registro financeiro próprio, evitando interpretar previsão ou multa anotada como cobrança já confirmada.

Pendências monetárias e categorias financeiras exigem permissions financeiras para consulta/alteração; responsáveis sem acesso ao processo não podem ser atribuídos. Sem permission financeira, valores e pendências financeiras são omitidos do detalhe; indicadores gerais ainda apontam a existência de providências no processo, sem revelar documentos ou quantias.

## Obrigações e encaminhamento manual

`FinancialObligation` distingue mensalidade proporcional, mensalidade atrasada, multa contratual, indenização de equipamento e outras cobranças. Mantém valores **previsto** e **cobrado** separados, vencimento, fornecedor do mirror, referência, situação documental e documento privado referenciado. Valores usam `DecimalField`, com validação não negativa. Nenhum formulário aceita confirmação de pagamento.

Os campos de situação financeira, valor pago, data e ator confirmador ficam preparados para evolução futura. O estado inicial é **Não confirmado** e permanece assim após envio/conclusão NOC. Esta entrega não concede permission de confirmar pagamento nem cria grupo Financeiro.

O resumo para encaminhamento consolida ticket, pedido, cliente/projeto, ISP, circuito, localidade, datas, fim do faturamento, valores, documentos, evidências e observações. Copiar texto não envia mensagem e não registra encaminhamento. O usuário envia o e-mail por seu meio corporativo e depois registra data passada/atual, destinatário/área, referência e evidência obrigatórias.

`FinancialHandoff` preserva responsável, resumo do momento e valores/documentos das obrigações incluídas por `HandoffObligation`. Alteração posterior da obrigação exige novo encaminhamento antes da conclusão normal. Envio manual e situação financeira permanecem separados. Não há integração Graph, Outlook ou Brevo neste fluxo.

Documentos/evidências são referências privadas, não upload de arquivos ou URLs buscadas pelo servidor. Não há acesso público, download proxy nem leitura de conteúdo de boleto. Não cadastrar senhas ou conteúdo bancário sensível em observações/evidências. Detalhes financeiros ficam somente no modelo/histórico autorizado; auditoria central registra identificadores e ações, sem texto de documento, referência bancária ou valores.

## Conclusão operacional NOC

A conclusão normal exige:

1. Tratativa iniciada, circuito/cliente/projeto/ISP identificados e vínculo CMDB validado.
2. Serviço confirmado pelo ISP, data efetiva e evidência, com encerramento do faturamento confirmado.
3. Pendências resolvidas ou formalmente encaminhadas com evidência.
4. Equipamentos devolvidos ou dispensados com evidência, quando existirem.
5. Para cada cobrança, valor cobrado conhecido, documento disponível e encaminhamento correspondente à versão atual da obrigação.

Pagamento não é requisito de conclusão NOC. Bloqueios aparecem no detalhe e são verificados no backend. Exceções exigem `override_cancellation_completion`, justificativa e evidência, além de permission de conclusão; mantêm os fatos pendentes e a justificativa na consulta posterior. Não existe conclusão silenciosa de equipamento, documento ou dívida.

## Segurança e administração

Permissions `cancelamentos.*` registradas no catálogo de Grupos e acessos, sem concessão automática aos grupos existentes:

| Codename | Capacidade |
|---|---|
| `view_cancellations` | Acesso ao módulo e consultas autorizadas |
| `triage_cancellations` | Decisões humanas e validação de vínculo |
| `assign_cancellations` | Assumir e atribuir responsáveis |
| `change_cancellation_stage` | Início, tratativa, dados, observações e evidências operacionais |
| `manage_cancellation_pending` | Pendências e equipamentos |
| `complete_cancellations` | Conclusão NOC |
| `override_cancellation_completion` | Exceções justificadas na conclusão |
| `view_cancellation_finance` | Documentos, valores, resumo e histórico financeiro |
| `manage_cancellation_finance` | Obrigações e registro manual de encaminhamento, também exige consulta financeira |
| `export_cancellations` | CSV operacional escopado |
| `configure_cancellations` | Configuração da projeção local |
| `import_cancellation_history` | Reserva para futura execução controlada; não existe importador nesta entrega |

Conceder permissions explicitamente na Central de Configurações, com `GroupDataScope` apropriado. Superuser mantém acesso nativo. A permissão de administrar integrações não admite pedidos; acesso ao Cockpit não concede acesso ao módulo.

`CancellationScope` adapta o `DataScopeService` existente. CUSTOM exige cliente contratante e projeto, por interseção; sem escopo, dados são negados. Entradas sem vínculo confiável, relações não resolvidas ou links fora do recorte ficam restritas à triagem global. Snapshot e contexto atual precisam continuar autorizados. Mudanças/remoções de vínculo restringem acesso em vez de preservar grants antigos.

Escopo é aplicado antes de filtros, indicadores, paginação, detalhe, POST e export. Referência manual a item de outro processo não autoriza alteração. CSRF, validação de forms, transações, locks e revisões protegem as ações. Auditoria central e eventos funcionais são gravados na mesma transação de sucesso. OTRS e CMDB permanecem somente leitura.

## Indicadores e exportação

A página principal mantém cinco indicadores compactos clicáveis, cada um levando à sua aba. **Concluídos** perdeu o sufixo NOC apenas na navegação da lista; a conclusão continua operacional e independente do pagamento. A coexistência de Pendências com Pedido/Solicitação é explicada discretamente no indicador. A tabela de Pendências mostra tipos, quantidade visível, previsões e valores informados quando autorizados.

A sincronização usa um indicador compacto na linha das abas: atualizado, sincronizando, desatualizado ou falha. Seu detalhe apresenta os horários reais de sucesso do espelho/projeção e acesso à Central de Integrações quando permitido. Considera leases ativas e erros posteriores ao sucesso; recarregar a página não renova freshness.

Todos os indicadores usam registros reais do recorte autorizado. Aguardando triagem conta entradas WAITING, sem confundi-las com pedidos oficiais. Pedido, Solicitação e Concluídos são contagens de processos com etapas mutuamente exclusivas. Com pendências conta processos não concluídos com pelo menos uma pendência OPEN; não soma o número de pendências. Documentos/boletos conta processos em andamento com pendência documental aberta ou obrigação aguardando documento. Encaminhados conta processos distintos com algum envio registrado, não quantidade de e-mails nem pagamentos.

Os indicadores de pendências/documentos/envios podem se sobrepor às etapas principais e são identificados assim na tela. Pesquisa, responsável, cliente, projeto, ISP e período da abertura do ticket são aplicados ao mesmo universo. CSV reutiliza a consulta e protege células contra fórmulas; financeiro não é incluído na exportação inicial.

## Models, migrations e worker

App: `arion_geo/aplicativos/cancelamentos/`. Models: `IntakeSettings`, `CancellationIntake`, `CancellationProcess`, `ISPTreatment`, `CancellationPending`, `CancellationEquipment`, `FinancialObligation`, `CancellationEvidence`, `FinancialHandoff`, `HandoffObligation`, `CancellationEvent` e `LegacyCancellationReference`.

Migrations aditivas: `0001_initial`, `0002_cancellationevent_details`. Aplicar pelo Python da `.venv` no banco DEV confirmado. O ambiente desta entrega está configurado para SQLite, sem bootstrap/candidato MySQL; isso não é validação MySQL e não altera a configuração operacional de outros ambientes. Build frontend inclui `frontend_src/js/cancelamentos/cancelamentos.js` pelo mecanismo existente.

## Nota original OTRS — dependência

A leitura genérica do client bloqueia artigos/corpos/anexos. `fetch_quality_articles` é uma leitura específica e transitória para correlação QID da Qualidade, sem o contrato de autor e data da primeira nota. Ela não constitui autorização para exibir comunicações em Cancelamentos.

Nesta entrega nenhum corpo de artigo foi consultado, persistido ou enviado ao frontend/auditoria. A evolução exige contrato autorizado por TicketID, autor/horário, seleção do primeiro artigo válido, Data Scope, armazenamento privado e apresentação segura do conteúdo. A origem será identificada como **OTRS**, separada das observações locais **ARION OPS**. Essa dependência não bloqueia o fluxo atual.

## Migração histórica futura e limites

`LegacyCancellationReference` reserva origem, entidade, PK, lote, versão de mapeamento, checksum, destino e data com unicidade de origem/entidade/PK. Histórico tem datas próprias e modelos separados para observações/valores. O importador ainda não existe; o banco real do legado não foi disponibilizado. `ArionBroken1`/RECs e os 162 registros de uma captura não são fonte de Cancelamentos e não foram carregados.

Procedimento futuro: identificar esquema real → extrair snapshot autorizado → mapear chaves/estados/datas/valores → preview privado → dry-run sem alterar domínio → reconciliar totais novos/já importados/conflitos/inválidos → revisar → aplicar em lotes com checkpoint/backup → conferir novamente. Resolver TicketID, Ticket#, pedido e CI sem deduplicar por texto semelhante; reaplicar lote não pode criar outro processo. Preservar datas legadas sem inventar decisões atuais. Ver [plano de integração](cancelamentos-plano-integracao.md).

Permanecem futuros: migração histórica, reconciliação CMDB, múltiplos processos por ticket, reconsideração/reabertura, upload privado de anexos, envio automático, painel Financeiro, confirmação de pagamento e gráficos financeiros. Não há automação remota, dados fabricados ou deploy nesta entrega.

## Validação

`check` passou e `makemigrations --check --dry-run` não detectou alterações pendentes. As duas migrations foram confirmadas como aplicadas no DEV configurado, com backup local antes da alteração de schema.

Na primeira entrega passaram 61 testes focados de Cancelamentos, worker e contratos documentais, além da validação da administração de acessos; os 35 testes do módulo foram repetidos após a revisão final do serviço de conclusão. Os cenários incluem replay e falha parcial da projeção, admissão única, decisões preservadas, vínculo CMDB, pendências simultâneas, equipamentos, cobranças e documentos, encaminhamento, exceções, escopo, CSRF, auditoria e exportação. Smoke tests cobriram as principais páginas autenticadas depois da inclusão da navegação.

O navegador validou lista e detalhe em claro/escuro nas larguras 1920, 1440, 1366, 1024 e 390 px, sem overflow global ou erros JavaScript, incluindo observação, aprovação e rejeição por modal/POST. O build de produção gerou os 33 bundles pelo script existente, usando Node 24 disponível no ambiente. Fixtures de interface foram restritas ao banco de teste isolado.

Esses testes foram executados sobre SQLite porque é o backend ativo deste checkout, sem candidato MySQL configurado. Não constituem homologação MySQL, teste de concorrência real no servidor MySQL ou validação da origem remota; nenhuma configuração foi trocada para simular esse resultado.

Na segunda entrega, a elegibilidade e a reconciliação foram verificadas com a população real e TicketIDs equivalentes ao KPI 1. Passaram os **44 testes do módulo**, incluindo estados históricos, mudança de estado na mesma fila, replay, decisões preservadas, responsáveis locais, conflitos, filtros por aba, contagens, permissões e Data Scope. `check`, verificação de migrations e `git diff --check` também passaram. A lista foi revisada em claro/escuro nas larguras 1920, 1440, 1366, 1024 e 390 px, com filtros avançados, atribuição e retorno ao recorte, sem overflow global. O build frontend passou pelo script existente. Não houve alteração de models ou nova migration; o MySQL consultado foi a fonte OTRS somente leitura, enquanto os testes de aplicação permanecem no SQLite DEV configurado.
