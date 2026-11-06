# Glossário OTRS e ARION OPS

| Termo | Significado nesta integração |
|---|---|
| OTRS | Fonte externa oficial de tickets, filas, estados e relações operacionais. |
| ARION OPS | Aplicação de exploração operacional que mantém um espelho local do recorte sincronizado. |
| TN | Número de ticket apresentado ao operador; vem de ticket.tn e é armazenado localmente como ticket_number. |
| ID externo | Chave interna numérica ticket.id; armazenada como OTRSTicket.external_id. Não é o TN. |
| Queue / fila | Agrupamento do OTRS. A sincronização de tickets inclui as filas explicitamente configuradas. |
| TicketState | Estado externo do ticket. Nome e tipo são preservados separadamente no espelho. |
| Estado operacional | Categoria derivada pelo ARION OPS a partir do nome normalizado do estado, como OPEN, PENDING ou RESOLVED. |
| ConfigItem / CI | Objeto de configuração do OTRS relacionado a um ticket; a relação é polimórfica e não equivale a um link CMDB já identificado localmente. |
| CMDBLinkMirror | Espelho local de links enriquecidos pela integração CMDB. Pode receber uma relação de CI quando o ID de origem é resolvido. |
| Dynamic Field | Campo configurável do OTRS. O código consulta nomes específicos, incluindo valores de fechamento e data de resolução. |
| FULL | Reconciliação do conjunto de tickets e relações para as filas configuradas. |
| FAST | Atualização incremental por change_time, com janela de sobreposição e IDs já conhecidos. Depende de FULL bem-sucedida. |
| Cursor | Marca local usada para limitar uma próxima leitura incremental; não é um registro completo do histórico de mudanças do OTRS. |
| Espelho / mirror | Projeção local dos dados necessários à operação e análise; não é cópia completa nem fonte oficial. |
| TQ | Tratativa de Qualidade criada no ARION OPS e enviada para análise externa. |
| QID | Identificador ARION-QID- mais 32 dígitos hexadecimais, inserido no corpo para correlacionar uma TQ com artigo/ticket. |
| Artigo | Mensagem associada a um ticket no OTRS. O corpo é lido transitoriamente apenas no fluxo autorizado de correlação TQ. |
| Data Scope | Restrição backend por Operadora/Projeto aplicada aos links locais antes de filtros e agregações que dependem desse escopo. |
| Estado bruto | Nome e identificador de estado recebidos do OTRS e preservados no mirror. |
| Data terminal | Data derivada de eventos de histórico que correspondem ao estado terminal atual; não substitui a data do Dynamic Field. |

## Identificadores e regras

- ticket.id identifica o registro interno do OTRS; ticket.tn identifica o ticket para operadores.
- ConfigItemID é um identificador de origem. Só se torna relação local com link CMDB quando encontra correspondência no mirror CMDB.
- RESOLVED é a categoria canônica local para determinados estados de sucesso; CLOSED é compatibilidade histórica local, não um novo estado externo.
- “Criado no período e atualmente resolvido” usa data de criação e estado atual. Não significa “resolvido durante o período”.

Veja [estados](otrs-estados.md), [datas](otrs-datas.md), [relacionamentos](otrs-relacionamentos.md) e [rastreabilidade](otrs-rastreabilidade.md).
