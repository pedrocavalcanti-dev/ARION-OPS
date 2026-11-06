# Fontes de Dados e Escopo

> **Governança:** OTRS e CMDB são as fontes oficiais em seus domínios. O banco do ARION OPS funciona como espelho sincronizado, cache operacional, camada analítica e repositório dos registros internos do próprio sistema, nunca como substituto oficial do OTRS ou da CMDB.

## Papéis das fontes oficiais

### CMDB — Fonte oficial de cadastro de rede

A CMDB (ServiceUp) é a fonte oficial de:

- **Link** e **ConfigItem** (`ConfigItemID`, `Number`, `Name`);
- **Designação** e circuito;
- **Cliente** e **Projeto** (atributos de origem e base para derivação controlada);
- **ISP / Provedor** (classe `Operadoras` e relacionamento com o link);
- **UF**, cidade e endereço cadastral;
- **Velocidade**, tipo de acesso e **situação** (`CurDeplState`, `CurInciState`).

### OTRS — Fonte oficial de chamados operacionais

O OTRS é a fonte oficial de:

- **Tickets** (`ticket.id` técnico e `ticket.tn` operacional);
- **Fila** (`NOC - ATACADO` e filas operacionais de Cancelamentos);
- **Estado** oficial do chamado (`business_status` e estado bruto);
- **Responsável** e proprietário no OTRS;
- **Motivos de fechamento** (`CodigoFechamento` / `closing_code_raw` e campos dinâmicos de falha massiva);
- **Datas** de abertura (`source_created_at`), atualização (`source_Updated_at`), resolução operacional (`resolved_at`) e encerramento (`terminal_state_at`);
- **Tratativas** e vínculos Ticket–ConfigItem (`link_relation`).

### ARION OPS — Fonte dos registros operacionais locais

O banco local do ARION OPS é a fonte de:

- **Preferências locais** do usuário (`UserPreference`, tema claro/escuro, `SavedAnalyticsView`, `TicketQueueView`);
- **RBAC e Data Scope** (`GroupDataScope`, vínculo com grupos e permissões Django);
- **Acompanhamento interno da fila** (`TicketTracking`, notas e eventos locais em Chamados);
- **Auditoria** administrativa e operacional (`AuditEvent`, `QualityAuditEvent`);
- **Ordens de Qualidade (OQs)**, classificações manuais da Qualidade (`QualityOccurrence`, níveis R1–R5), checklist (`QualityAction`), **monitoramento RREIP** (`QualityMonitoringCycle`) e diretório de contatos (`QualityContact`);
- **Consolidações e relatórios internos** (competências, baseline, pausas, evidências e decisões do módulo Claro Multas em `relatorios`).

## Matriz resumida de origem

| Informação | Fonte oficial | Mirror / model no ARION OPS | Uso principal |
|---|---|---|---|
| Ticket, fila e status | OTRS | `OTRSTicket` | Chamados, Dashboard, Cockpit, Mapa, Rankings e Qualidade. |
| Relação Ticket–CI | OTRS `link_relation` | `OTRSTicketConfigItem` | Associação entre chamados e circuitos CMDB. |
| Link / ConfigItem | CMDB | `CMDBLinkMirror` | Base, dimensões analíticas e carteira da Qualidade. |
| Provider / ISP | CMDB classe `Operadoras` | `CMDBOperatorMirror` | Nome operacional do provedor e catálogo de operadoras. |
| Cliente / Projeto | Nome CMDB + regras ARION OPS | Campos raw e derivados em `CMDBLinkMirror` | Semântica de negócio com origem e nível de confiança explícitos. |
| Acompanhamento NOC | ARION OPS | `TicketTracking`, notes/events | Status interno, responsável local, notas e linha do tempo. |
| Qualidade Operacional | ARION OPS + espelhos OTRS/CMDB | `QualityOrder`, `QualityOccurrence`, `QualityMonitoringCycle`, `QualityContact` | Gestão de reincidências, OQs, ciclos RREIP e contatos. |
| Relatórios (Claro Multas) | Planilha original + espelhos + ARION OPS | `PenaltyCompetence`, `PenaltySourceRow`, `PenaltyTreatmentRow` | Apuração, enriquecimento controlado e tratativa auditada. |
| Usuários, permissões e escopo | ARION OPS (Django Auth) | Tabelas auth + `GroupDataScope` | Autenticação, RBAC e Data Scope no backend. |
| Auditoria | ARION OPS | `AuditEvent` | Rastreabilidade de eventos administrativos e operacionais. |

## Fluxo e freshness

O processo `integration_worker` lê o OTRS via conexão MySQL somente leitura e a CMDB via GenericInterface (`ConfigItemSearch` e `ConfigItemGet`), valida a integridade do lote e reconcilia os espelhos locais. As requests web consultam exclusivamente o banco local do ARION OPS. Caso uma fonte remota fique indisponível, o último snapshot válido é preservado para leitura, ficando progressivamente stale até a próxima sincronização bem-sucedida. Detalhes de cadência, cursores e leases estão em [Worker e Sincronização](worker-e-sincronizacao.md).

## Status e universos analíticos

Os estados operacionais normalizados são `OPEN`, `PENDING`, `SCHEDULED`, `RESOLVED`, `CANCELED` e `UNKNOWN`. `CLOSED` é normalizado como `RESOLVED`; o estado bruto do OTRS permanece separado para auditoria. Tickets ativos podem incluir `SCHEDULED`, mas o backlog e o aging atuais incluem somente `OPEN` e `PENDING`. Em Cancelamentos/Geral, tickets ativos sem classificação de etapa permanecem visíveis como `UNKNOWN` para investigação, fora do backlog. `CANCELED` permanece uma categoria separada.

O indicador **Tickets criados no período** filtra por `source_created_at` e mostra o status atual dessa população. A distribuição de status usa TicketIDs distintos e deve totalizar o resultado consultado. O indicador **Resolvidos no período** filtra por data efetiva de resolução, conta TicketIDs distintos atualmente classificados como `RESOLVED` e pode incluir tickets criados antes do início selecionado. Registros sem data efetiva válida não entram nessa contagem. Portanto, o status atual dos tickets criados no período e as resoluções ocorridas no período são populações diferentes; seus totais não devem ser tratados como equivalentes nem subtraídos entre si. A data efetiva usa `resolved_at`, respeitando os campos disponíveis para o ciclo atual; `terminal_state_at` só é fallback nos estados em que ele representa a resolução operacional.

O fluxo do Cockpit agrupa tickets criados e resolvidos por suas datas efetivas, com deduplicação por TicketID e os filtros ativos. O espelho guarda o estado atual, não um ledger completo de reaberturas ou mudanças de fila; esse fluxo descreve os registros atualmente espelhados que possuem datas utilizáveis, e o saldo interno de eventos não representa uma variação comprovada do backlog. Informações sobre cobertura e datas ausentes ficam nos diagnósticos internos e nesta documentação, não como avisos repetidos no painel gerencial.

O backlog histórico usa exclusivamente snapshots imutáveis de sincronizações FULL bem-sucedidas com cobertura das filas do Cockpit. Uma observação só é considerada quando a captura e seus detalhes estão completos; o valor e seu horário representam a captura real. Intervalos sem captura ficam ausentes, sem reconstrução pelo estado atual, interpolação ou preenchimento com zero. Quando não há observações, a série e os indicadores históricos de backlog são omitidos. O backlog atual continua calculado separadamente a partir dos tickets atualmente `OPEN` ou `PENDING`.

KPIs gerais contam tickets distintos. Em agregações por Link, um chamado vinculado a múltiplos circuitos pode ser contabilizado em cada circuito associado. Quando o usuário possui Data Scope `CUSTOM`, tickets sem vínculo confirmável com um Link autorizado não são expostos.

## Dados derivados e dados demonstrativos

`CodigoFechamento` é preservado em `closing_code_raw` e agrupado por `ClosingReasonCanonicalizer` sem sobrescrever o valor original. `FalhaMassiva`, `Falha Massiva` e `Defeito Massivo` são tratados como aliases canônicos de “Falha Massiva”. A classificação de procedência é informativa e não altera isoladamente o Índice ARION nos Rankings.

Provider/ISP provém da entidade CMDB relacionada (`CMDBOperatorMirror`). Cliente e Projeto são derivados do nome original do ConfigItem por `LinkBusinessIdentityService`, mantendo explícitos a regra de origem e o grau de confiança. `legacy_project` permanece apenas como fallback histórico restrito, e a antiga importação XLSX não alimenta o fluxo operacional vigente.

As telas operacionais consomem os espelhos reais de OTRS/CMDB. O comando `seed_qualidade_demo` existe exclusivamente para desenvolvimento local (`DEBUG=True` em banco local), identifica todos os registros gerados com marcador explícito de demonstração e nunca deve ser utilizado em produção.

## Data Scope e proteção de credenciais

O Data Scope é aplicado no backend antes de qualquer filtro, agregação, paginação, visualização de detalhe ou exportação. No modo `CUSTOM`, as dimensões configuradas operam por interseção (`AND`). O superuser técnico mantém visão global.

Senhas de portal ou PPPoE não são persistidas nos espelhos CMDB. O cliente OTRS não consulta senhas de usuários, tokens, artigos ou corpos de mensagens. Segredos de integração são protegidos via DPAPI no Windows e jamais são expostos em respostas HTTP, logs ou metadados de auditoria.

## Estudos históricos

Levantamentos de schema e contagens pontuais em `docs/07-estudos/` representam fotografias datadas de análise técnica e não devem ser interpretados como contagens fixas da operação.
