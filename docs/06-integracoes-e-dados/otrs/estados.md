# Estados dos tickets

## Estado bruto e categoria ARION

O mirror preserva external_state_id, external_state_name e external_state_type. OTRSSyncService._normalize_tickets usa o nome normalizado do estado para preencher business_status; o tipo técnico de ticket_state.type_id é armazenado como contexto, mas não decide a categoria.

| Nome de estado reconhecido no código | Categoria ARION | Efeito |
|---|---|---|
| new, em atendimento, projeto especial | OPEN | Backlog |
| pendente, pendente aprovação, pendente cliente endereço não localizado, pendente cliente local fechado, pendente operadora, pendente outros, aguardando aprovação do gestor, aguardando aprovação do solicitante | PENDING | Backlog |
| agendado | SCHEDULED | Ativo de negócio, fora do backlog |
| merged, resolvido, closed successful, encerrado com sucesso, encerrado com viabilidade | RESOLVED | Resolvido |
| cancelado | CANCELED | Cancelamento, não é backlog |
| Qualquer nome sem regra explícita | UNKNOWN | Não classificado; não deve ser presumido aberto ou resolvido |

A normalização remove acentos e compara sem diferença de caixa. O catálogo reflete o mapa em services/otrs_status.py; ticket_state_type.name não substitui essa tabela de aliases.

## Resolvido versus Encerrado

A categoria operacional de resolvido é RESOLVED. O código tolera CLOSED em registros legados: normalize_business_status converte CLOSED para RESOLVED e os filtros de compatibilidade incluem ambos. A migration integracoes.0014_normalize_closed_operational_status normaliza o armazenamento histórico local. external_state_name continua preservando o nome original recebido do OTRS.

Portanto, ticket bruto com nome “Encerrado com sucesso” pode ser mostrado como Resolvido, sem mudar o estado do OTRS. Uma linha do mirror representa um único ticket e as agregações usam contagem distinta, não somam RESOLVED e CLOSED como duas classes sobre o mesmo ticket.

## Backlog e estados especiais

- BUSINESS_OPEN_STATUSES: OPEN, PENDING e SCHEDULED.
- BACKLOG_STATUSES: OPEN e PENDING. Agendado não entra no backlog.
- Estados terminais operacionais: RESOLVED e CANCELED; consultas também toleram CLOSED legado.
- UNKNOWN fica fora de indicadores que exigem estado operacional conhecido; alguns resumos gerenciais podem exibi-lo separadamente.
- A lista de estado atual não é o histórico de transições. A data terminal é lida separadamente de ticket_history.

Reabertura altera o estado atual retornado por ticket. O mirror passa a refletir esse estado na próxima leitura; histórico não reconstrói sozinho uma sequência completa de estados na aplicação.

## Filas de cancelamento

O código configura três filas para cancelamentos e separa etapas usando estado e fila. A fila de solicitação considera “em atendimento”; filas/etapas de tratamento consideram nomes como agendado, pendente, aguardando aprovação, em atendimento, new e Projeto Especial. A lista completa está em aplicativos/integracoes/otrs_queues.py. Esses perfis delimitam classificação operacional; não são identidades de ticket.

Leia [datas](otrs-datas.md), [indicadores](otrs-indicadores.md) e [consultas](otrs-consultas-sql.md).
