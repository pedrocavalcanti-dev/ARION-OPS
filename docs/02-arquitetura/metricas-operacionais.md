# Métricas Operacionais Canônicas (GEO, Dashboard, Cockpit e Backlog)

Documentação técnica das definições canônicas, regras de elegibilidade, deduplicação, timezone e critérios de reconciliação entre **GEO Inteligência**, **Dashboard**, **Cockpit** e **Backlog** no ARION OPS.

## 1. Regras transversais obrigatórias

- **Timezone oficial:** `America/Sao_Paulo` para todos os limites de dia, semana e mês (`_date_boundaries`, `_month_buckets`, `_operational_buckets`, `_datetime_bounds`).
- **Deduplicação:** todas as métricas de chamados contam `TicketID` distinto (`Count("pk", distinct=True)` / `Count("ticket_external_id", distinct=True)`). Um chamado vinculado a múltiplos links CMDB conta 1 única vez nos totais.
- **Data Scope:** aplicado no backend antes de qualquer filtro, agregação, paginação ou exportação.
- **Fontes oficiais:** CMDB e OTRS são somente leitura; nenhuma tela inventa dados históricos nem aplica percentuais simulados.

## 2. GEO Inteligência — Modo Normal × Modo Apresentação

- **População única de incidentes válidos (`MapAnalyticsService.query` e `state_detail`):**
  - Ambos os modos (`fixed_client=False` e `fixed_client=True`) avaliam a **mesma população autorizada** de `ticket_candidates` e aplicam a mesma regra de correspondência (`context_matches`) e política por operadora (`IncidentRules.valid_for_operator`):
    - **Vivo:** exclui `CANCELADO`, `IMPROCEDENTE` e `SEM INTERVENÇÃO`.
    - **Claro:** exclui `CANCELADO` e `IMPROCEDENTE` (mantém `SEM INTERVENÇÃO`).
    - **Demais operadoras:** mantêm a regra padrão documentada.
  - O isolamento do cliente no Modo Apresentação (`fixed_client=True`) continua restringindo catálogos (`clients`, `projects`, `providers`, base de links e UFs autorizadas), sem descartar incidentes válidos cujo link ainda esteja sem operadora na CMDB.
  - Projetos genéricos (`PROJETO DIVERSOS`) no título OTRS não sobrescrevem projetos específicos da CMDB (como `Carrefour` / `Banco CSF`).
- **Classificação sanitizada de motivos na apresentação (`presentation.py`):**
  - Motivos técnicos verdadeiros são preservados: `Massiva`, `Rompimento`, `Equipamento`, `Energia`, `Configuração`, `Reset remoto`.
  - Ocorrências restritas (`Pendência Financeira`) e não técnicas (`Demais motivos técnicos`) são agrupadas sob **`Outros motivos`**, sem expor a classificação financeira em nenhum campo do payload ou drawer da apresentação.
  - `Sem classificação` é preservado quando não há dados suficientes.
  - **Projeção simulada desativada:** `distribution_projected` não redistribui incidentes financeiros em causas técnicas inventadas. A soma das categorias equivale exatamente a `ticket_count`.

## 3. Dashboard × Cockpit — Gráficos mensais e indicadores de fluxo

- **Fonte central compartilhada (`AnalyticsQueryService.canonical_created_tickets`):**
  - **Tickets criados / Novos no período:** contagem distinta de `TicketID` na fila `NOC - ATACADO` (`is_present_in_queue=True`) cuja abertura efetiva (`source_created_at`) ocorreu no intervalo em `America/Sao_Paulo`, nos estados operacionais conhecidos (`OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED`, `CLOSED` + `CANCELED` quando `include_cancelled=True`), com paridade exata entre **Dashboard → Evolução mensal → Chamados no período** e **Cockpit → Evolução operacional → Novos no período**.
  - **Resolvidos no período:** contagem distinta de `TicketID` com `business_status=RESOLVED` e data efetiva de resolução (`operational_resolution_expression()`) no intervalo, sem confundir encerramento administrativo com resolução técnica.
  - **Cancelados no período:** contagem distinta de `TicketID` com `business_status=CANCELED` e data operacional de cancelamento (`canceled_at` / `terminal_state_at`), sem misturar com o módulo comercial de Cancelamentos.
  - **Links reincidentes:** contagem distinta de links CMDB com 2 ou mais ocorrências válidas distintas no período (`OPERATIONAL_VALID_STATES`, excluindo `CANCELED` e `IMPROCEDENTE`).

## 4. Backlog — Situação Atual × Histórico por Horário

- **Backlog atual:** tickets distintos elegíveis em `OPEN` ou `PENDING` (excluindo `IMPROCEDENTE`) no último instante sincronizado do espelho OTRS. Exibido com referência explícita **`Atualizado em DD/MM/AAAA HH:MM`** no Dashboard e no Cockpit, acompanhado de **Tickets acima de 24h** e **Tickets acima de 72h**.
  - Observação: a fila operacional em **Chamados** e o card *Chamados abertos agora* do Dashboard incluem também `SCHEDULED` (agendados), explicitamente diferenciado do backlog estrito (`OPEN` + `PENDING`).
- **Backlog histórico reconstruído (`painel/services/backlog_history.py`):**
  - Calculado exclusivamente a partir de intervalos reais de `OTRSBacklogHistory` / `OTRSBacklogInterval` (`ticket_history` de estados, filas e procedência), **nunca** a partir do status atual dos tickets.
  - No Cockpit Suporte, os KPIs históricos superiores (`Backlog inicial` / `Backlog final` / `Backlog observado`) e a curva do gráfico utilizam a **mesma série reconstruída**, sempre exibindo **`Observado em DD/MM/AAAA HH:MM`** e a janela de cobertura (`earliest_at` até `through`).
  - Para buckets multi-hora (dia, semana, mês), calcula também o **pico real de chamados simultâneos (`backlog_peak` / `backlog_peak_at`)** via sweep-line exato, permitindo distinguir o fechamento das 23:59 dos picos intradiários (ex.: 07h–12h).
  - Consulta pontual por data/hora disponível em `GET/POST /api/analytics/backlog-at/?at=YYYY-MM-DDTHH:MM` (`America/Sao_Paulo`), retornando `total`, `open`, `pending`, `over_24h`, `over_72h` ou aviso explícito quando o horário estiver fora da cobertura.
- **Coleta periódica horária persistente (`OTRSBacklogCheckpoint`):**
  - Migration aditiva `integracoes.0023_otrsbacklogcheckpoint`.
  - `capture_hourly_backlog_checkpoint()` registra automaticamente um checkpoint horário leve e idempotente (`scope_signature`, `slot_at`) ao final de cada sincronização OTRS aplicada, sem consultas adicionais ao OTRS e sem sobrescrever slots anteriores.

## 5. Comparação Histórica da Planta CMDB × Volume de Chamados

- **Persistência mensal de snapshots da planta (`CMDBPlantMonthlySnapshot` e `CMDBPlantMonthlySlice`):**
  - Migration aditiva `integracoes.0024_cmdb_plant_monthly_snapshot`.
  - Cada sincronização CMDB aplicada (`CMDBSyncService.run(apply=True)`) e o comando idempotente `capturar_snapshot_planta_cmdb` registram o tamanho real da planta no mês corrente (`total_links`, `active_links`, `canceled_links`, `maintenance_links`) e recortes dimensionais por `(contracting_client, canonical_project, uf)` compatíveis com Data Scope.
  - Importações verificadas em `base_links.CMDBImport` podem ser consolidadas explicitamente (`--include-imports`) com cobertura marcada como `PARTIAL` e data de captura visível.
  - **Proibição de retroprojeção:** meses anteriores ao primeiro snapshot verificado exibem **`Base histórica indisponível`** (`coverage_status="UNAVAILABLE"`), sem projetar a planta atual para o passado nem inventar datas de ativação/cancelamento.
- **Serviço e endpoint analítico (`painel/services/plant_history.py`, `/api/analytics/plant-history/`):**
  - Reutiliza `AnalyticsQueryService.canonical_created_tickets` para manter paridade exata de chamados mensais com Dashboard e Cockpit.
  - Calcula `tickets_per_100_links = (tickets / active_links) * 100`, `affected_links_pct = (affected_links / active_links) * 100` e variações mês a mês apenas quando há denominador confiável (`active_links > 0`), sinalizando explicitamente meses em andamento (`is_partial_month=True`).
  - Integrado ao Dashboard (`/dashboard/`) na seção **Evolução da planta × Volume de chamados**, com alternância entre **Taxas proporcionais** e **Valores absolutos** e filtros por operadora contratante, projeto/cliente canônico e UF respeitando Data Scope no backend.
