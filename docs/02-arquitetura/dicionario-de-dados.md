# Dicionário de Dados

Este índice é a referência para localizar campos pelo nome, model, tipo, origem e significado operacional. A busca e o filtro de origem abaixo atuam somente sobre a tabela já carregada nesta página; não fazem request novo nem ampliam acesso a dados.

## Como ler a origem

- {{badge:OTRS}} / {{badge:CMDB}}: valor lido da fonte oficial e preservado no mirror.
- {{badge:ARION}}: estado, preferência ou relação criada e mantida localmente.
- {{badge:DERIVADO}}: interpretação ARION calculada a partir de um valor de origem; não deve sobrescrever o raw.

`null=True` significa SQL NULL permitido; `blank=True` trata validação/formulário. Unique pode gerar índice implícito mesmo quando `db_index` não está explicitamente ligado. Para tamanho, default, índices compostos e delete behavior ver [Banco de Dados e Modelos](banco-de-dados.md).

## Identificadores não intercambiáveis

| Valor | Model | Tipo Django | Origem | Semântica | Chave? |
|---|---|---|---|---|---|
| `ticket.id` → `external_id` | [OTRSTicket](banco-de-dados.md#otrsticket) | `PositiveBigIntegerField` | {{badge:OTRS}} | ID técnico do ticket para upsert, lookup FAST/FULL e tracking. | Sim, unique no mirror |
| `ticket.tn` → `ticket_number` | [OTRSTicket](banco-de-dados.md#otrsticket) | `CharField(80)` | {{badge:OTRS}} | Número legível para exibição e busca. | Sim, unique no mirror |
| `OTRSTicket.id` | [OTRSTicket](banco-de-dados.md#otrsticket) | `BigAutoField` | {{badge:ARION}} | PK local do ORM, não ID OTRS. | PK |
| `TicketTracking.ticket_external_id` | [TicketTracking](banco-de-dados.md#tickettracking) | `PositiveBigIntegerField` | {{badge:ARION}} | Guarda o ID externo OTRS; vínculo lógico, sem ForeignKey ao mirror. | Sim, unique |
| `ConfigItemID` → `source_config_item_id` | [CMDBLinkMirror](banco-de-dados.md#cmdblinkmirror) | `CharField(40)` | {{badge:CMDB}} | ID técnico usado na relação Ticket–CI e resolução Provider. | Sim, unique |
| CMDB `Number` → `source_number` | CMDB mirrors | `CharField(40)` | {{badge:CMDB}} | Identificador de negócio/display; não é a chave Ticket–CI. | Unique quando presente |
| PK local `id` | Models ARION | Varia | {{badge:ARION}} | Identidade da linha do banco ARION. | PK |

## Índice de campos

| Campo | Model | Tipo Django | Origem | Semântica | Chave? |
|---|---|---|---|---|---|
| `queue_external_id` | [OTRSTicket](banco-de-dados.md#otrsticket) | `PositiveIntegerField` | {{badge:OTRS}} | ID externo da fila resolvida por nome exato. | Índice simples e índice composto |
| `queue_name` | OTRSTicket | `CharField(250)` | {{badge:OTRS}} | Nome da fila de origem. | Não |
| `external_state_id` | OTRSTicket | `PositiveIntegerField` | {{badge:OTRS}} | ID do estado atual da origem. | Índice simples |
| `external_state_name` / `external_state_type` | OTRSTicket | `CharField` | {{badge:OTRS}} | Nome e tipo fonte mantidos junto do status normalizado. | Não |
| `business_status` | OTRSTicket | `CharField(16)` | {{badge:DERIVADO}} | OPEN, PENDING, SCHEDULED, RESOLVED, CANCELED ou UNKNOWN. CLOSED é aceito somente para leitura compatível de linhas legadas e normalizado pela migration 0014. | Índice simples e composto |
| `is_business_open` | OTRSTicket | `BooleanField` | {{badge:DERIVADO}} | Verdadeiro para OPEN/PENDING/SCHEDULED. | Índice simples |
| `source_created_at` | OTRSTicket | `DateTimeField` | {{badge:OTRS}} | `ticket.create_time`; coorte de criação. | Índice simples e composto |
| `source_changed_at` | OTRSTicket | `DateTimeField` | {{badge:OTRS}} | `ticket.change_time`; cursor incremental. | Índice simples |
| `closing_code_raw` | OTRSTicket | `CharField(250)` | {{badge:OTRS}} | Valor bruto de `CodigoFechamento`; canonicalizador não apaga o original. | Índice simples |
| `responsibility_raw` | OTRSTicket | `CharField(120)` | {{badge:OTRS}} | Valor original de `RvalidacaoProcedenteImprocedente`. | Índice simples |
| `responsibility_normalized` | OTRSTicket | `CharField(20)` | {{badge:DERIVADO}} | PROCEDENTE, IMPROCEDENTE ou INDETERMINADO; informativo. | Índice simples |
| `resolved_at` | OTRSTicket | `DateTimeField` | {{badge:OTRS}} | `ticketResolvido.value_date`; data operacional principal de resolução. | Índice simples |
| `terminal_state_at` | OTRSTicket | `DateTimeField` | {{badge:DERIVADO}} | Último StateUpdate compatível com o estado terminal atual; diagnóstico e fallback quando `resolved_at` não existe. | Índice simples |
| `canceled_at` | OTRSTicket | `DateTimeField` | {{badge:DERIVADO}} | Data terminal de cancelamento mantida à parte. | Índice simples |
| `is_present_in_queue` | OTRSTicket | `BooleanField` | {{badge:DERIVADO}} | Presença na fila no snapshot FULL válido. | Índice simples e composto |
| `config_item_id` | [OTRSTicketConfigItem](banco-de-dados.md#otrsticketconfigitem) | `CharField(40)` | {{badge:OTRS}} | ConfigItemID da relação remota, preservado mesmo se não houver mirror. | Índice e unique composto |
| `cmdb_link` | OTRSTicketConfigItem | `ForeignKey` nullable | {{badge:ARION}} | Referência local opcional ao Link resolvido; `SET_NULL`. | Índice |
| `source_config_item_id` | [CMDBLinkMirror](banco-de-dados.md#cmdblinkmirror) | `CharField(40)` | {{badge:CMDB}} | ConfigItemID do objeto; identidade técnica do mirror. | Unique |
| `source_number` | CMDBLinkMirror / CMDBOperatorMirror | `CharField(40)` | {{badge:CMDB}} | Número público/negócio opcional do ConfigItem. | Unique nullable |
| `name` | CMDBLinkMirror | `CharField(500)` | {{badge:CMDB}} | Nome original do Link, preservado como entrada de identidade. | Não |
| `isp_raw` | CMDBLinkMirror | `CharField(300)` | {{badge:CMDB}} | Atributo ISP recebido; valor bruto para resolver Provider. | Não |
| `operator` | CMDBLinkMirror | `ForeignKey` nullable | {{badge:ARION}} | Relação local para CMDBOperatorMirror, semanticamente Provider/ISP; `SET_NULL`. | Índice |
| `operator_resolution_method` | CMDBLinkMirror | `CharField(20)` | {{badge:DERIVADO}} | Método/resultado do match da FK por ConfigItemID ou nome exato. | Índice |
| `contracting_client_raw` | CMDBLinkMirror | `CharField(300)` | {{badge:DERIVADO}} | Segmento original reconhecido como cliente; não é atributo independente do CMDB. | Índice |
| `contracting_client` | CMDBLinkMirror | `CharField(300)` | {{badge:DERIVADO}} | Cliente canônico calculado pelas regras atuais. | Índice |
| `business_project` | CMDBLinkMirror | `CharField(300)` | {{badge:DERIVADO}} | Projeto derivado do nome/aliases; não confundir com Projeto oficial. | Índice |
| `business_account` | CMDBLinkMirror | `CharField(300)` | {{badge:DERIVADO}} | Segmento de conta quando parser encontra estrutura suficiente. | Não |
| `business_id` | CMDBLinkMirror | `CharField(80)` | {{badge:DERIVADO}} | Token reconhecido pelo padrão configurado BD/BL mais dígito. | Não |
| `business_identity_confidence` / `_source` | CMDBLinkMirror | `CharField` | {{badge:DERIVADO}} | Confiança e regra que produziram a identidade. | Não |
| `legacy_project` | CMDBLinkMirror | `CharField(300)` | {{badge:ARION}} | Projeto histórico importado; fallback somente quando `business_project` está vazio. | Não |
| `source_last_seen_at` | CMDB mirrors / OTRSTicket | `DateTimeField` | {{badge:ARION}} | Última observação em sincronização local. | Índice varia por model |
| `is_present_in_source` | CMDBLinkMirror / CMDBOperatorMirror | `BooleanField` | {{badge:DERIVADO}} | Presença no último snapshot CMDB válido. | Índice simples; alguns compostos |
| `internal_status` | [TicketTracking](banco-de-dados.md#tickettracking) | `CharField(24)` | {{badge:ARION}} | Estado do acompanhamento local, independente do estado OTRS. | Índice composto simples |
| `internal_priority` | TicketTracking | `CharField(12)` | {{badge:ARION}} | Prioridade definida no fluxo local Chamados. | Não |
| `assigned_to` / `updated_by` | TicketTracking | `ForeignKey` nullable | {{badge:ARION}} | Usuário responsável/última atualização; `SET_NULL` na exclusão. | Índice simples |
| `revision` | TicketTracking | `PositiveIntegerField` | {{badge:ARION}} | Versão usada para controle concorrente de edição. | Não |
| `next_action_at` | TicketTracking | `DateTimeField` nullable | {{badge:ARION}} | Próxima ação programada localmente. | Índice simples |
| `body` | [TicketTrackingNote](banco-de-dados.md#tickettrackingnote) | `TextField` | {{badge:ARION}} | Conteúdo de nota local; autor usa `PROTECT`. | Não |
| `old_value` / `new_value` | [TicketTrackingEvent](banco-de-dados.md#tickettrackingevent) | `CharField(240)` | {{badge:ARION}} | Valores anterior/novo do evento de tracking, não do OTRS. | Não |
| `filters` | [TicketQueueView](banco-de-dados.md#ticketqueueview) | `JSONField` | {{badge:ARION}} | Filtros pessoais salvos, não permissão nem fila compartilhada. | Unique `(user, name)` |
| `mode` | [GroupDataScope](banco-de-dados.md#groupdatascope) | `CharField(8)` | {{badge:ARION}} | `ALL` ou `CUSTOM`; o backend continua impondo scope. | Não |
| `change_cursor` | [IntegrationSyncState](banco-de-dados.md#integrationsyncstate) | `DateTimeField` nullable | {{badge:ARION}} | Cursor delta OTRS; avança após apply local. | Não |
| `last_full_success_at` | IntegrationSyncState | `DateTimeField` nullable | {{badge:ARION}} | Último FULL concluído com sucesso. | Não |
| `owner_token` / `expires_at` | [IntegrationLease](banco-de-dados.md#integrationlease) | `CharField` / `DateTimeField` | {{badge:ARION}} | Proprietário e expiração do lock persistido. | Key unique; expires index |
| `secret_ciphertext` | [Integration](banco-de-dados.md#integration) | `TextField` | {{badge:ARION}} | Configuração protegida no backend; nunca devolver o valor ao frontend. | Não |
| `metadata` | [AuditEvent](banco-de-dados.md#auditevent) | `JSONField` | {{badge:ARION}} | Metadados sanitizados de evento; sem tokens/senhas/headers. | Não |
| `configuration` | [SavedAnalyticsView](banco-de-dados.md#savedanalyticsview) | `JSONField` | {{badge:ARION}} | Definição local da view analítica salva pelo usuário. | Unique `(user, name)` |

## Models por grupo

| Grupo | Models | Referência |
|---|---|---|
| OTRS | `OTRSTicket`, `OTRSTicketConfigItem`, `OTRSStateMapping` | [OTRS](..\06-integracoes-e-dados\otrs.md) e schema no [Banco](banco-de-dados.md) |
| CMDB | `CMDBLinkMirror`, `CMDBOperatorMirror` | [CMDB](..\06-integracoes-e-dados\cmdb.md) |
| Integrações | `Integration`, `IntegrationRun`, `IntegrationSyncState`, `IntegrationLease` | [Worker](worker-e-sincronizacao.md) |
| Chamados | `TicketTracking`, `TicketTrackingNote`, `TicketTrackingEvent`, `TicketQueueView` | [Chamados](..\05-funcionalidades\chamados.md) |
| Segurança | `GroupDataScope`, `LegacyUserDataScope`, `UserPreference` | [Usuários e Escopos](..\05-funcionalidades\usuarios-grupos-e-escopos.md) |
| Ciclo de acesso | `UserSecurityProfile`, `AccessToken`, `VerificationChallenge`, `PasswordPolicy` | [Convites e senhas](../03-seguranca-e-qualidade/politica-de-senhas-e-acessos.md) |
| Cancelamentos | `CancellationProcess`, `CancellationIntake`, filhos, `LegacyPerson`, `LegacyCancellationReference` | [Fluxo e financeiro](../05-funcionalidades/cancelamentos.md) |
| Histórico OTRS | `OTRSOperationalSnapshot` e filhos; `OTRSBacklogHistory`, `OTRSBacklogInterval` | [Cockpit](../05-funcionalidades/cockpit.md) |
| Auditoria e analytics | `AuditEvent`, `SavedAnalyticsView` | [Auditoria](..\05-funcionalidades\auditoria.md), [Cockpit](..\05-funcionalidades\cockpit.md) |

As relações, os tipos de coluna e as constraints completas estão em [Banco de Dados e Modelos](banco-de-dados.md). Verifique também migrations antes de alterar schema.

## Glossário operacional de contagens

- **Tickets**: quantidade de IDs de chamados distintos no universo, estados, período e filtros definidos pela métrica em questão.
- **Tickets ativos agora**: tickets presentes em `OPEN`, `PENDING` ou `SCHEDULED`, conforme fila/contexto. Aging da fila do Dashboard e Chamados incluem agendados; backlog e aging do Cockpit usam somente `OPEN`/`PENDING`, excluindo improcedentes. Dashboard, Chamados e Rankings usam `NOC - ATACADO`; Cockpit permite Cancelamentos/Geral, com classificação de etapa própria.
- **Backlog histórico no Cockpit Suporte**: intervalos de estado/fila/procedência reconstruídos dos eventos OTRS autorizados, incluindo saídas e reaberturas. Não deriva de saldo simples do mirror atual nem presume baseline zero. Capturas FULL são observações separadas; lacunas não ganham valores inventados.
- **Total informado de Cancelamentos**: soma Decimal do ledger principal (multa, equipamento e outros), sem repetir pendências; NULL continua ausente, zero explícito continua zero e subtotal incompleto é parcial. Não representa pagamento.
- **Links afetados**: quantidade distinta de Links CMDB locais presentes que têm ao menos uma relação Ticket–ConfigItem válida no conjunto de Tickets contado. Ticket sem CI ou CI sem Link local continua podendo contar como Ticket, mas não produz Link afetado.
- **Links reincidentes**: quantidade distinta de Links que satisfazem a regra de reincidência do recorte, normalmente pelo menos dois Tickets distintos; não é sinônimo de Links afetados.

O Data Scope é aplicado antes das agregações. A cobertura geográfica do Mapa, os filtros da Fila, as linhas de Provedor e os períodos históricos podem definir universos próprios; consulte a documentação do módulo antes de comparar contagens entre telas.
