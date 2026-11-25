# Operação NOC Atacado — N1 e N2

O universo de suporte consolida **NOC - TRIAGEM (N1)** e **NOC - ATACADO (N2)**. OTRS permanece a fonte oficial; o ARION consulta seu espelho local e nunca altera a origem. A identidade única é `OTRSTicket.external_id` (TicketID), inclusive nas transferências, retornos e reaberturas.

## Filas e coleta

`integracoes/otrs_queues.py` define `NOC_QUEUE_NAMES` e acrescenta a Triagem a `COCKPIT_QUEUE_NAMES`, preservando as filas específicas de Cancelamentos. O sincronizador resolve cada nome exato na tabela oficial `queue`, exige resultado único e executa a coleta pelos IDs reais. Na homologação de 25/11/2026 foram confirmados **107 = Atacado** e **181 = Triagem**; esses números são evidência da origem, não IDs fixados em regras de negócio.

O Worker existente utiliza as mesmas credenciais e os mesmos ciclos FULL/FAST. A mudança de assinatura obriga FULL antes do primeiro FAST. A coleta não restringe a estados abertos: tickets resolvidos e cancelados também são espelhados. O filtro inclui passagens históricas pela Triagem, inclusive quando o ticket já saiu dela antes da leitura. Tickets conhecidos continuam acompanhados por ID após sair das filas. FULL reconcilia somente as filas selecionadas; lotes de backfill nunca marcam outros tickets como ausentes.

`OTRSTicketEvent` armazena os eventos oficiais `NewTicket`, `Move`, `StateUpdate` e `Merged`, com ID único do evento, TicketID, fila, estado e horário. Não contém artigos, autores, notas ou o payload textual do histórico. Eventos repetidos são idempotentes; divergência de um evento já armazenado interrompe o lote, preservando a transação anterior. Leituras e gravações dos eventos usam lotes de até 400. `has_support_history` preserva a participação comprovada na operação após transferências, sem transformar a fila atual em evidência do passado.

O único ticket encontrado na Triagem em DEV possui vínculo oficial ao CI **5524**, identificado como **Claro / Correios**. Não se observou atendimento de outra operação nessa fila no recorte disponível. Essa amostra não comprova exclusividade futura da fila: eventual compartilhamento deve ter sua regra de elegibilidade homologada antes de ampliar o escopo. RBAC e Data Scope por operadora/projeto continuam aplicados antes de filtros, agregações e paginação.

## Definições dos indicadores

- **Criados:** TicketIDs distintos pela data oficial de criação, conforme a definição canônica anterior, incluindo tickets que participaram da Triagem e os resolvidos exclusivamente pelo N1. Transferência não é criação.
- **Resolvidos:** estado atual normalizado Resolvido e data efetiva de resolução canônica (`ticketResolvido` / evento do ciclo vigente, conforme a regra já existente). Um fechamento administrativo posterior não substitui a resolução.
- **Cancelados:** preservam estado oficial, classificação e data próprios; não são promovidos a incidentes válidos.
- **Ativos / aging da fila:** fila atual efetiva N1 ou N2, presença confirmada e estados `OPEN`, `PENDING` ou `SCHEDULED`.
- **Backlog atual:** fila atual efetiva N1 ou N2 e estados `OPEN` ou `PENDING`, excluindo improcedentes. Agendados continuam fora do backlog, como antes da integração.
- **Histórico e observações:** os intervalos usam a fila de cada evento, nunca a fila atual; capturas FULL e checkpoints usam uma assinatura nova para N1 + N2. Evidências antigas de N2 permanecem armazenadas, mas não representam cobertura consolidada.
- **Reincidência / links afetados:** mantêm a relação oficial Ticket–ConfigItem e a deduplicação por TicketID e CI. Passar de N1 a N2 não cria ocorrência adicional. Qualidade, Rankings, Base e GEO reutilizam o universo compartilhado e mantêm suas regras de validade, exclusões por operadora, identidade operacional e proteção financeira no Modo Apresentação.
- **Planta × Chamados:** amplia somente o universo de tickets; nenhum snapshot da planta CMDB é reescrito.

O Dashboard oferece um resumo compacto de N1/N2; Chamados identifica fila atual e nível, permite filtro por fila e exibe até 80 eventos oficiais recentes no detalhe, separados do histórico de acompanhamento ARION. O consolidado é o padrão. O Cockpit Suporte também retorna o resumo complementar em `operational.attendance`.

Os complementos distinguem:

| Indicador | Definição |
| --- | --- |
| Atualmente N1 / N2 | Ativos na fila atual, incluindo agendados; independente do período de criação |
| Resolvidos N1 / N2 | TicketIDs distintos com estado atual resolvido, data efetiva no período e evento oficial de resolução nessa data comprovando a fila |
| Encaminhados N1 → N2 | TicketIDs distintos com evento `Move` no período para Atacado e evento anterior na Triagem; múltiplos encaminhamentos do mesmo ticket contam uma vez |
| Resolução sem nível comprovado | Resolução canônica sem evento correspondente que permita atribuir N1/N2; não inferir pela fila atual |

Essas métricas possuem bases temporais diferentes e não são parcelas aditivas dos criados. Uma reabertura retira o ticket do total de resolvidos do ciclo atual, preservando os eventos anteriores. Não há atribuição automática de responsabilidade pessoal ao N1 ou N2.

## Histórico e produção

Migration aditiva: **`integracoes.0025_noc_triagem_lifecycle`**. Cria eventos oficiais e progresso do backfill, acrescenta participação histórica ao ticket e fila aos intervalos de backlog.

Na próxima atualização de produção, após preparar código e assets pelos procedimentos existentes:

```text
python arion_geo/gerenciar.py migrate --noinput
python arion_geo/gerenciar.py sincronizar_historico_triagem --apply --batch-size 200 --max-batches 10
python arion_geo/gerenciar.py sincronizar_otrs --apply --full
python arion_geo/gerenciar.py sincronizar_historico_backlog --apply
```

Em DEV, utilizar `.venv/Scripts/python.exe`. Em Linux, usar o Python do ambiente virtual do serviço. Não executar em paralelo com outro sincronizador: os comandos compartilham a lease OTRS. A publicação desta alteração não realiza deploy no Windows Server nem cria tag de release.

O backfill usa paginação por TicketID, descobre fila atual e histórico sem restrição de status, aplica o mesmo sincronizador e avança `OTRSQueueBackfill.last_ticket_id` na mesma transação do lote. Reexecutar até `complete=true`; falha mantém os lotes já confirmados e o cursor anterior ao lote que falhou. Sem `--apply`, não escreve tickets, eventos ou progresso. `--restart` revisa a origem desde o início sem apagar o espelho. FULL também descobre passagens pela Triagem; a rotina separada permite carga controlada e retomável antes da reconciliação completa. FAST acompanha alterações e passagens breves pelo histórico desde o cursor, com a janela de segurança existente.

Não se presume histórico completo. Ausência ou tipos incompletos de eventos impede atribuir resolução por nível; a interface mantém as resoluções sem nível comprovado. Lacunas de estado, fila ou procedência continuam indisponíveis no histórico de backlog. Capturas antigas não recebem Triagem retroativamente.

## Reconciliação DEV — 25/11/2026

Consultas somente leitura na origem, migração e sincronizações executadas no SQLite DEV. Contagens comparadas por TicketID, sem valores fixados no frontend.

| Evidência | Resultado |
| --- | --- |
| FULL | 13.300 tickets nas cinco filas configuradas; 88.009 eventos oficiais de atendimento |
| Triagem exclusiva em relação ao N2 | 1 ticket: **103911** |
| Passagem comprovada pelas duas filas | 0 |
| Resolvidos pelo N1 | 0 na população disponível |
| Novembro: criados sem / com Triagem | 702 / 703 |
| Referência da comparação ao vivo | **09:24:30 America/Sao_Paulo** |
| Ativos Atacado / Triagem / consolidado | 22 / 1 / **23** |
| Backlog Atacado / Triagem / consolidado | 19 / 1 / **20** |
| Agendados | 3, incluídos nos ativos e excluídos do backlog |
| TicketIDs faltantes / extras | **0 / 0**, tanto em ativos quanto em backlog |
| Cache histórico consolidado | 13.640 tickets; 109.150 eventos; 18.064 intervalos |
| Cobertura histórica | até **09:22:33**; 209 intervalos históricos indeterminados, nenhum indeterminado no extremo final da cobertura |

A captura inicial da tarefa (23 + 1) não é uma meta fixa: um ticket foi resolvido durante a homologação e o FAST registrou essa alteração. O comparativo acima usa a mesma leitura de origem e o espelho já atualizado. A atualização FULL também encontrou tickets novos de outras filas desde a sincronização anterior; o aumento global de registros não deve ser atribuído integralmente à Triagem.

Os cenários ainda ausentes nos dados reais — resolução exclusiva N1, encaminhamento N1/N2 e retornos — são exercitados por testes sintéticos de integração e indicadores, sem fabricar evidência na origem. O histórico de novembro contém resoluções sem nível comprovado; elas permanecem nos indicadores canônicos e não são atribuídas ao N1/N2 por suposição.

## Validação da implementação

- Django `check` e `makemigrations --check`: sem erros ou alterações pendentes.
- 455 testes da integração, Worker, indicadores, GEO, Chamados, Qualidade e Base: aprovados. A execução ampliada utiliza hasher rápido somente no processo de testes; não modifica a configuração da aplicação. O subconjunto de 38 testes de sincronização e Triagem também passou com a configuração normal.
- Orçamento de até 20 consultas do Dashboard preservado: níveis e status compartilham uma agregação, sem consultas por ticket.
- Oito rotas autenticadas principais responderam HTTP 200. Dashboard e Cockpit reconciliados novamente após a agregação final: 703 criados e backlog atual de 20.
- Build de produção: 42 bundles e Service Worker gerados. Layouts revisados em claro/escuro, inclusive em 1024 e 1366 px; o filtro secundário permanece dentro da tela em notebooks.
- Migration aplicada somente no SQLite DEV. A origem MySQL foi consultada somente para leitura; não houve deploy, alteração de tags ou escrita no OTRS.
