# Cancelamentos — operação NOC

O [Dashboard Gerencial](cancelamentos-dashboard.md) está em `/cancelamentos/dashboard/`, acessível pelo botão **Dashboard** da lista. Apresenta seis KPIs, quatro gráficos e resumo por cliente/projeto, sempre com permissions e Data Scope; valores financeiros exigem acesso específico.

Estado: operação em DEV com 162 cancelamentos históricos importados do ArionBroker no SQLite DEV autorizado. A fila Pendências usa a etapa principal; pendências individuais são consultadas separadamente. Triagem humana, tratativa ISP, responsáveis, equipamentos e encaminhamento manual ao Financeiro permanecem locais, sem escrita OTRS/CMDB. Não houve deploy nem alteração de versão.

## Acesso e telas

- `/cancelamentos/`: lista, pesquisa, indicadores e abas Entrada OTRS, Pedido, Solicitação, Pendências e Concluídos.
- `/cancelamentos/entrada/<id>/`: detalhe da entrada, origem, circuitos candidatos, responsável e decisões.
- `/cancelamentos/processos/<id>/`: processo oficial, tratativa ISP, equipamentos, pendências, evidências, histórico e informações financeiras autorizadas.
- `/cancelamentos/exportar/`: CSV operacional do mesmo recorte da lista, até 10.000 registros; campos financeiros somente para quem possui `view_cancellation_finance`, em processos oficiais.
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

## Conferência da Entrada OTRS em 09/11/2026

Às 15:56 (America/Sao_Paulo), a consulta somente leitura à origem identificou 16 tickets do KPI 1; o espelho local tinha 15. A pesquisa real `kpi.search` de ID 681 foi conferida: QueueIDs 164 e StateIDs 10, sem filtro adicional de subfila ou tipo de estado. Dos 16 tickets, 14 já possuíam processo oficial de origem ArionBroker e 2 não estavam admitidos. O espelho/projeção tinham último sucesso às 14:52; faltava um ticket novo. FAST pelo sincronizador existente e projeção local criaram uma entrada de triagem, sem criar processos oficiais ou mudar decisões.

Durante a conferência, um ticket já importado passou para **pendente operadora** na origem e saiu do KPI 1. Novo FAST/projeção às **16:22:54 / 16:22:56** reconciliou a alteração. Às **16:28:36**, os TicketIDs da origem e do espelho coincidiam integralmente: **15 elegíveis, 13 já admitidos e 2 aguardando validação**. Total oficial: **162**, distribuído em **14 Pedidos, 5 Solicitações, 2 Pendências e 141 Concluídos**. A diferença anterior de um contra dois era atraso de atualização, sem necessidade de alterar a regra de elegibilidade ou fixar contadores. Os horários são evidências dessa conferência; a interface usa sempre os timestamps atuais da integração.

## Triagem e circuito principal

A lista oferece **Todos / Meus / Sem responsável**, sobre `assigned_to` na triagem e `owner` nos processos, com Data Scope aplicado antes dos filtros. A barra principal reúne busca por Ticket#, TicketID, código do pedido, circuito, cliente ou ISP, Cliente/Projeto e Responsável. Não há consulta a cada tecla. **Mais filtros** começa fechado, indica filtros ativos e contém apenas opções adequadas à aba; valores ficam na URL e na paginação. Limpar mantém a aba.

Atribuir/Reatribuir reutiliza o catálogo existente e valida o acesso do destino no backend. Autor, data, responsáveis anterior/novo e revisão são auditados. Conflitos são rejeitados e atribuições sem alteração não duplicam eventos. Após confirmar, o recorte e a página são preservados; a linha pode sair naturalmente do filtro Sem responsável. Assumir não toma registro de outro analista e não altera proprietário OTRS.

O analista pode assumir, manter pendente com observação, rejeitar com justificativa ou aprovar. Rejeição permanece documentada e não reaparece como nova entrada em sync posterior. Reconsideração/reabertura de decisão não foi disponibilizada nesta entrega.

A aprovação exige origem recente e consistente com a revisão apresentada. O processo é criado na mesma transação da decisão e auditoria. Há unicidade do TicketID na entrada, relação um-para-um entrada/processo e controle de revisão para repetição HTTP e concorrência.

Um processo por ticket é a regra inicial. O operador escolhe explicitamente um circuito principal entre relações Ticket–CI atuais; não há associação por semelhança de designação. Outros circuitos vinculados podem ser consultados na triagem global, mas não geram processos adicionais. A admissão de vários processos por ticket dependerá de modelagem/decisão posterior.

Um operador global autorizado pode admitir sem vínculo confiável com justificativa, mantendo as lacunas visíveis. A conclusão normal exige identificação completa e circuito validado. A ação Revisar associação CMDB permite completar o vínculo a partir de candidatos atuais, com justificativa, permissões de triagem e alteração de etapa. Clientes, projetos, ISP e usuários são reutilizados; nenhum cadastro paralelo é criado.

## Etapa principal e pendências

`CancellationProcess.stage` diferencia quatro etapas exclusivas: **Pedido**, **Solicitação**, **Pendências** e **Concluído**. A aba e o KPI principal Pendências usam somente `stage=PENDING`. Em **Mais filtros → Com pendências abertas**, a aba Pendências consulta qualquer etapa, com recorte opcional por etapa principal; nas demais abas o filtro mantém a etapa selecionada. O indicador secundário abre essa consulta ampliada. Pendências individuais não movem etapas nem resolvem a si mesmas.

Pedido → Iniciar tratativa → Solicitação → Concluir NOC permanece disponível. Mover / retornar etapa permite destinos abertos Pedido, Solicitação e Pendências, com justificativa obrigatória. Concluídos exige a ação de conclusão existente e suas validações; não é destino livre no formulário de movimento. Reabrir exige `reopen_cancellations` **e** `change_cancellation_stage`, destino aberto e justificativa; data, autor e eventual exceção da conclusão anterior ficam preservados no evento. Atribuir não reabre o processo. Todos os movimentos são locais, transacionais, escopados, auditados e protegidos por revisão concorrente.

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

Pendências monetárias e categorias financeiras exigem permissions financeiras para consulta/alteração; responsáveis sem acesso ao processo não podem ser atribuídos. Sem permission financeira, valores e pendências financeiras são omitidos do detalhe; contagens e filtros de pendências consideram apenas filhos autorizados, sem revelar documentos ou quantias.

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
| `change_cancellation_stage` | Status local, etapas abertas, retornos, tratativa, dados e observações |
| `reopen_cancellations` | Reabertura explícita justificada; exige também alteração de etapas |
| `manage_cancellation_pending` | Pendências e equipamentos |
| `complete_cancellations` | Conclusão NOC |
| `override_cancellation_completion` | Exceções justificadas na conclusão |
| `view_cancellation_finance` | Documentos, valores, resumo e histórico financeiro |
| `manage_cancellation_finance` | Obrigações e registro manual de encaminhamento, também exige consulta financeira |
| `export_cancellations` | CSV operacional escopado |
| `configure_cancellations` | Configuração da projeção local |
| `import_cancellation_history` | Execução controlada do importador histórico, com consulta e escopo global; Apply DEV controlado, com autorização adicional explícita para SQLite |

Conceder permissions explicitamente na Central de Configurações, com `GroupDataScope` apropriado. Superuser mantém acesso nativo. A permissão de administrar integrações não admite pedidos; acesso ao Cockpit não concede acesso ao módulo.

`CancellationScope` adapta o `DataScopeService` existente. CUSTOM exige cliente contratante e projeto, por interseção; sem escopo, dados são negados. Entradas sem vínculo confiável, relações não resolvidas ou links fora do recorte ficam restritas à triagem global. Snapshot e contexto atual precisam continuar autorizados. Mudanças/remoções de vínculo restringem acesso em vez de preservar grants antigos.

Escopo é aplicado antes de filtros, indicadores, paginação, detalhe, POST e export. Referência manual a item de outro processo não autoriza alteração. CSRF, validação de forms, transações, locks e revisões protegem as ações. Auditoria central e eventos funcionais são gravados na mesma transação de sucesso. OTRS e CMDB permanecem somente leitura.

## Indicadores e exportação

A página principal mantém seis indicadores compactos: Total Geral, Aguardando triagem, Pedidos, Solicitação, Pendências e Concluídos. Total Geral conta somente processos oficiais do recorte autorizado e exclui entradas ainda não admitidas. Os outros indicadores são clicáveis. Os filtros ficam em uma linha, com Mais filtros inicialmente fechado. As tabelas usam linhas compactas com tipografia legível, largura fixa proporcional ao contêiner, Ticket em destaque, truncamento com título e ações visíveis. Solicitação e Concluídos não exibem Protocolo na lista; o dado permanece no detalhe e formulário. De 1024 px para cima a tabela cabe no contêiner; em telas menores há rolagem interna controlada. Visualizar e Assumir ficam acessíveis na linha; quando o responsável é o próprio usuário, aparece Comigo. O menu Ações usa popover nativo na camada superior do navegador, fora do empilhamento das células fixas e do overflow da tabela; ajusta a abertura ao espaço da janela e aceita teclado, Escape e clique externo. Abre os mesmos formulários do detalhe em modais dentro da lista. Após salvar, a consulta escopada é renderizada novamente no servidor e atualiza tabela e indicadores sem navegação, preservando aba, filtros e paginação. Não há segundo backend de formulários. Pendências mostra quantidade com link ao detalhe, sem empilhar filhos; Pedido, Solicitação e Concluídos têm colunas adequadas à etapa. Scroll horizontal fica restrito à tabela.

A sincronização usa um indicador compacto na linha das abas: atualizado, sincronizando, desatualizado ou falha. Seu detalhe apresenta os horários reais de sucesso do espelho/projeção e acesso à Central de Integrações quando permitido. Considera leases ativas e erros posteriores ao sucesso; recarregar a página não renova freshness.

Todos os indicadores usam o recorte autorizado antes de contagens e paginação. Pedido, Solicitação, Pendências e Concluídos são exclusivos. No snapshot DEV de 09/11/2026: **14 / 5 / 2 / 141**. Há **93 processos com pendências abertas** (92 Concluídos + 1 Pendências). A consulta antiga unia esses 93 aos 2 da etapa PENDING, com interseção de 1, produzindo 94. Nenhum registro ou pendência foi excluído para corrigir a população. O filtro ampliado não substitui o KPI principal de 2. A triagem atual continua usando o KPI 1 e excluindo tickets com processo oficial.

Documentos/boletos e encaminhamentos permanecem conceitos secundários que podem se sobrepor às etapas. Pendências abertas consultadas/contadas respeitam também a autorização financeira. Pesquisa, responsável, cliente, projeto, ISP e período da abertura do ticket usam o mesmo universo; CSV reutiliza a consulta e protege células contra fórmulas. Usuários com `view_cancellation_finance` também recebem valores principais, total informado, indicação de parcial e conciliação em colunas separadas; a triagem não exporta financeiro.

`operational_status` é independente da etapa e do status original imutável: Em análise, Aguardando retorno ou Pendência identificada. Alterar status exige justificativa e permission de alteração; não confirma ISP, pagamento, resolução ou conclusão. Sem status local, a lista mostra o status histórico ou a etapa. Tratativa ISP pode ser atualizada em Solicitação ou Pendências; a conclusão é admitida em Solicitação ou Pendências, sempre com as verificações existentes. Pedido exige iniciar a tratativa. Registros concluídos aceitam observações de acompanhamento autorizadas sem reabrir o processo.

Pendências e Concluídos exibem Multa, Equipamento, Outros e **Total (R$)** em BRL, alinhados à direita e somente com permission financeira. O cálculo central (`services/financial_totals.py`, função `money_columns`) é compartilhado por tabela, detalhe, resumo para o Financeiro, exportação CSV autorizada e Dashboard Gerencial. Utiliza `Decimal` sobre obrigações registradas (`charged_amount` quando presente, senão `estimated_amount`) somadas às pendências monetárias não descartadas ainda sem cobrança vinculada. A coluna **Pendências** apresenta também a quantidade aberta, o total monetário aberto e a diferenciação entre pendências com valor e sem valor. A deduplicação exige vínculo explícito via `FinancialObligation.source_pending` ou proveniência legada comprovada do ArionBroker (`legacy_field` com `LegacyCancellationReference`, incluindo `valor_outros` cobrindo `OVERDUE` e `OTHER`), nunca por simples coincidência de valor: sem vínculo formal, obrigação e pendência permanecem contabilizadas com sinalização de possível duplicidade para revisão. `NULL` permanece distinto de zero: campos ausentes não viram `R$ 0,00` e produzem total identificado marcado como **Parcial**; sem nenhum valor conhecido, mostra **Não informado**. O bloco Financeiro distingue total identificado, previsto em cobranças, em aberto, pago e resolvido/descartado; resolver uma pendência nunca declara pagamento realizado. Reconciliação `UNRECONCILED`/`POSSIBLE_DUPLICATE` permanece visível em **Conciliação financeira: A conferir**, sem alterar fatos históricos.

Datas válidas do snapshot são exibidas sem alterar a tratativa atual. A data histórica de cancelamento usa `data_cancelamento_confirmado` quando não existe data efetiva local; não representa nova confirmação do ISP. Campo opcional ausente aparece como Não informada; ano anômalo ou conclusão sem data confiável recebe Data a revisar, preservando o valor bruto. O detalhe separa responsável anterior e atual, data de atribuição e timeline. Nomes históricos vêm do enriquecimento privado; sem correspondência, mostra Nome não disponível. IDs e datas brutas ficam na rastreabilidade autorizada. Nenhum usuário é criado ou associado por coincidência de ID.

O detalhe reúne Visão geral, Dados do link e tratativa ISP, Observações, Pendências, Financeiro, Histórico e Evidências, com navegação por seções e expansão progressiva em um único painel compacto, sem empilhar cartões vazios. Voltar, Assumir/Atribuir e Ações ficam alinhados no cabeçalho. A timeline mostra ação em linguagem natural, autor e horário, com expansão de textos longos e paginação. Informações técnicas ficam recolhidas e exigem as permissions existentes de inspeção de integrações ou auditoria.

Adicionar pendência usa tipo, descrição, valor BRL opcional e data prevista, com responsável/evidência opcionais recolhidos. O catálogo já suportado é preservado. **Adicionar outra pendência** salva a primeira e mantém o modal pronto para a próxima com revisão atualizada. Editar pendência aberta exige as mesmas proteções financeiras; grava estado anterior/posterior e não altera o snapshot legado. Resolver/encaminhar/descartar exige justificativa e evidência. A seleção de pendência é limitada ao processo e aos itens autorizados.

`LegacyPerson` resolve nomes históricos por origem/ID, sem criar contas Django. O enriquecimento do Excel original foi aplicado no DEV: 7 identificações recuperam 151 responsáveis de processos, os 172 autores de pendências e os autores dos 1.813 eventos históricos. Os 11 processos sem responsável na origem continuam sem identificação; não há associação presumida. As 172 pendências foram conciliadas sem ambiguidades. A coluna Responsável da aba Pendências identifica o atendimento pai (163 identificadas, 9 sem responsável na origem), não uma atribuição individual da pendência. Preview, Apply DEV, relatório privado e replay idempotente estão descritos em [nomes históricos](../06-integracoes-e-dados/arionbroker/usuarios-historicos.md). CSV de usuários continua disponível como alternativa de evidência. IDs permanecem no banco e na rastreabilidade autorizada; nomes e Excel ficam fora do Git. Notas originais não disponíveis pelo contrato atual continuam consultáveis no OTRS, sem copiar corpos de artigos para o módulo.

## Models, migrations e worker

App: `arion_geo/aplicativos/cancelamentos/`. Models: `IntakeSettings`, `CancellationIntake`, `CancellationProcess`, `ISPTreatment`, `CancellationPending`, `CancellationEquipment`, `FinancialObligation`, `CancellationEvidence`, `FinancialHandoff`, `HandoffObligation`, `CancellationEvent`, `LegacyImportRun` e `LegacyCancellationReference`.

Migrations aditivas: `0001_initial`, `0002_cancellationevent_details`, `0003_cancellationprocess_origin_and_more`, `0004_alter_financialobligation_legacy_field_and_more`, `0005_alter_cancellationprocess_options_and_more`, `0006_legacy_people` e `0007_financialobligation_source_pending`. A 0005 adiciona status local inicialmente vazio e a permission de reabertura, sem concedê-la a grupos nem alterar fatos históricos. A `0006_legacy_people` acrescenta apenas o cadastro de nomes históricos, sem contas ou alterações nos fatos importados. A `0007_financialobligation_source_pending` acrescenta o vínculo opcional `source_pending` em `FinancialObligation` para deduplicação financeira explícita no mesmo processo, sem alterar valores ou vinculações históricas existentes. As migrations 0003/0004 preservam identidades/fatos históricos e admissão sem triagem somente para origem ArionBroker. Aplicar pelo Python da `.venv` no banco DEV confirmado. O checkout continua em SQLite, sem candidato MySQL; as migrations locais preservaram dados após backup e não constituem validação MySQL. Build frontend inclui `frontend_src/js/cancelamentos/cancelamentos.js` pelo mecanismo existente.

## Nota original OTRS — dependência

A leitura genérica do client bloqueia artigos/corpos/anexos. `fetch_quality_articles` é uma leitura específica e transitória para correlação QID da Qualidade, sem o contrato de autor e data da primeira nota. Ela não constitui autorização para exibir comunicações em Cancelamentos.

Nesta entrega nenhum corpo de artigo foi consultado, persistido ou enviado ao frontend/auditoria. A evolução exige contrato autorizado por TicketID, autor/horário, seleção do primeiro artigo válido, Data Scope, armazenamento privado e apresentação segura do conteúdo. A origem será identificada como **OTRS**, separada das observações locais **ARION OPS**. Essa dependência não bloqueia o fluxo atual.

## Importação histórica e limites

O snapshot inicial de 09/11 e as conferências nas seções anteriores são evidências datadas. Após reconciliações autorizadas, a referência DEV de **10/11/2026** contém 162 processos: **11 Pedidos, 5 Solicitações, 3 Pendências e 143 Concluídos**, com 177 pendências, 426 obrigações, 1.819 eventos e 2.156 referências. Não significa carga desses dados no MySQL de produção. O relatório de auditoria referencia as validações originais sem executar nova importação nesta revisão.

`importar_cancelamentos_legado` oferece preview, dry-run e Apply protegido. O snapshot real foi importado em SQLite DEV por autorização explícita anterior, com `--allow-sqlite-dev`, backup e reconciliação. Esta correção não reexecuta o importador. Permanecem 162 processos, 1.084 históricos, 594 movimentações, 135 observações, 172 pendências, 421 previsões financeiras e 2.147 referências. O caminho MySQL 8 DEV continua exigindo configuração e validação próprias; SQLite não o homologa.

O processo oficial importado reutiliza `CancellationProcess`, sem criar triagem ou aprovação fictícia quando não houver entrada correspondente. Fila determina a etapa; status original, responsável legado e datas brutas ficam separados da operação atual. Concluídos sem data são sinalizados, ano 0026 não é convertido, DESCARTADA é distinta de RESOLVIDA e pendências abertas continuam visíveis mesmo em histórico concluído. Identidade OTRS usa número operacional exato; vínculo CMDB exige evidência. Atribuição atual, inclusive em concluídos, não altera responsável legado. Textos livres importados têm exposição financeira conservadora até revisão. Ver [mapeamento completo, configuração e comandos](cancelamentos-migracao-arionbroker.md) e [plano de integração](cancelamentos-plano-integracao.md).

Permanecem futuros: homologar MySQL de ensaio, liberar carga seletiva produtiva, reconciliação de status CMDB, múltiplos processos por ticket, reconsideração de triagem, upload privado de anexos, envio automático, painel Financeiro e confirmação de pagamento. **Gráficos financeiros já existem** no Dashboard Gerencial 1.4.0; valores informados não significam pagamento. O CSV é da lista operacional, não uma exportação própria de gráficos. A [carga histórica para produção](../06-integracoes-e-dados/arionbroker/carga-producao.md) é separada e permanece bloqueada pelo importador atual.

## Validação

Na revisão financeira e de consistência da candidata 1.4.1 em 13/11/2026: **`aplicativos.cancelamentos` (128 testes descobertos: 123 aprovados e 5 ignorados exclusivos de MySQL no runner)**, `check`, `makemigrations --check --dry-run`, `migrate --check`, build frontend (`36` bundles e Service Worker) e `git diff --check` passaram sobre SQLite DEV. Ensaio somente leitura em cópia isolada do banco DEV validou o caso real do ticket `2026071036000568` (`R$ 22,00` em pendência `OVERDUE` sem cobrança vinculada aparecendo na listagem, detalhe, resumo, CSV e Dashboard), a deduplicação dos processos históricos (`valor_outros` e `POSSIBLE_DUPLICATE`), a soma exata entre listagem e Dashboard no recorte completo (`162` processos) e HTTP `200` nas principais rotas autenticadas. MySQL DEV permanece pendente.

Na verificação anterior passaram os **83 testes focados do módulo**, `check`, `makemigrations --check --dry-run`, build frontend e diff check. A migration aditiva 0006 está aplicada no DEV (complementada pela `0007_financialobligation_source_pending` nesta revisão). O navegador validou 70 listagens e oito detalhes em claro/escuro nas larguras 1920, 1440, 1366, 1280, 1024, 768 e 390 px, além da revisão das últimas correções em 1280/390. As linhas mediram 50 px, sem scroll horizontal da tabela no desktop nem overflow global. Operações pela lista preservaram a página: atribuir/reutilizar atribuição, assumir, alterar status, adicionar duas pendências no mesmo modal, editar/resolver pendência, registrar tratativa, mover, concluir, reabrir, aprovar e rejeitar.

Os testes de mutação usaram somente banco sintético isolado. Na base real, os 162 detalhes e cinco listas foram renderizados com conexão local somente leitura. Snapshots, pendências, valores e 1.813 fatos importados permaneceram íntegros. Uma atribuição real auditada durante o trabalho foi preservada, sem confundi-la com alteração do legado. Contas e concessões existentes permaneceram intactas; o novo model recebeu apenas as permissions técnicas padrão do Django, sem concessão automática. O importador original não foi reexecutado.

Na primeira entrega passaram 61 testes focados de Cancelamentos, worker e contratos documentais, além da validação da administração de acessos; os 35 testes do módulo foram repetidos após a revisão final do serviço de conclusão. Os cenários incluem replay e falha parcial da projeção, admissão única, decisões preservadas, vínculo CMDB, pendências simultâneas, equipamentos, cobranças e documentos, encaminhamento, exceções, escopo, CSRF, auditoria e exportação. Smoke tests cobriram as principais páginas autenticadas depois da inclusão da navegação.

O navegador validou lista e detalhe em claro/escuro nas larguras 1920, 1440, 1366, 1024 e 390 px, sem overflow global ou erros JavaScript, incluindo observação, aprovação e rejeição por modal/POST. O build de produção gerou os 33 bundles pelo script existente, usando Node 24 disponível no ambiente. Fixtures de interface foram restritas ao banco de teste isolado.

Esses testes foram executados sobre SQLite porque é o backend ativo deste checkout, sem candidato MySQL configurado. Não constituem homologação MySQL, teste de concorrência real no servidor MySQL ou validação da origem remota; nenhuma configuração foi trocada para simular esse resultado.

Na segunda entrega, a elegibilidade e a reconciliação foram verificadas com a população real e TicketIDs equivalentes ao KPI 1. Passaram os **44 testes do módulo**, incluindo estados históricos, mudança de estado na mesma fila, replay, decisões preservadas, responsáveis locais, conflitos, filtros por aba, contagens, permissões e Data Scope. `check`, verificação de migrations e `git diff --check` também passaram. A lista foi revisada em claro/escuro nas larguras 1920, 1440, 1366, 1024 e 390 px, com filtros avançados, atribuição e retorno ao recorte, sem overflow global. O build frontend passou pelo script existente. Não houve alteração de models ou nova migration; o MySQL consultado foi a fonte OTRS somente leitura, enquanto os testes de aplicação permanecem no SQLite DEV configurado.
