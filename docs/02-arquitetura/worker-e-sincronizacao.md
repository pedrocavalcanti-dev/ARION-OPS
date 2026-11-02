# Worker e Sincronização

## Processo separado

`integration_worker` é uma management command do ARION OPS executada em processo separado do servidor HTTP. Views, middleware e `AppConfig.ready()` não iniciam a sincronização. A web lê o último snapshot local e continua disponível se o worker parar; o dado pode ficar stale.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py integration_worker
.venv\Scripts\python.exe arion_geo\gerenciar.py integration_worker --once
.venv\Scripts\python.exe arion_geo\gerenciar.py integration_worker --interval 10
```

`--once` avalia um ciclo. `--interval` configura polling do scheduler (default 5 segundos, máximo 60), não a frequência de leitura das fontes.

## Cadências configuradas

O scheduler compara a última execução e o estado sincronizado de cada fonte. FULL OTRS, quando devido, encerra a avaliação daquela passagem e adia FAST; falha agenda retry em 2 minutos; execução RUNNING/PENDING só volta a ser considerada após o prazo de lease. CMDB é avaliado separadamente.

| Rotina | Cadência | Trabalho |
|---|---:|---|
| OTRS FAST | 2 min | Delta por `ticket.change_time`; janela de segurança de 2 min. |
| OTRS FULL | 30 min | Reconcilia as quatro filas configuradas para o Cockpit e relações; tem prioridade sobre FAST. Um FULL é antecipado quando a cobertura configurada ainda não foi aplicada. |
| CMDB FULL | 10 min | Busca Search/Get completa e reconcilia snapshot validado. |
| Retry de falha | 2 min | Agenda nova tentativa e preserva o mirror atual. |

Primeira carga e reconstrução requerem FULL. O scheduler é verificado em intervalos menores do que as cadências. Horários abaixo mostram somente uma sequência ilustrativa de um ciclo estável; início real depende do processo e não é promessa de minuto civil:

```text
Exemplo relativo:       00   02   04   06   08   10   ...   30
OTRS FAST:               ·    F    F    F    F    F    ...    F
OTRS FULL:               U    ·    ·    ·    ·    ·    ...    U
CMDB FULL:               C    ·    ·    ·    ·    C    ...    C
```

FULL OTRS substitui/coordena o delta devido à precedência; os horários não definem SLA de freshness nem a ordem exata de início.

## Fluxo de dados

```mermaid
flowchart TD
O[OTRS MySQL read-only] --> W[Worker FAST/FULL]
C[CMDB ServiceUp Search/Get] --> W
W --> V[Validar snapshot]
V --> A[Aplicar transação local]
A --> S[IntegrationSyncState]
```

OTRS usa MySQL read-only com SQL restrito. CMDB usa `ConfigItemSearch` e `ConfigItemGet`. Apenas o banco ARION recebe writes. Requests de tela não abrem conexão remota.

### OTRS FAST

FAST inicia pelo cursor salvo, subtrai a safety window e seleciona tickets alterados antes de carregar Dynamic Fields/relações. Como `ticket.change_time` no OTRS é um `DATETIME` sem fuso, o limite da consulta é convertido para a hora local definida por `TIME_ZONE` e enviado sem `tzinfo`; o cursor interno continua timezone-aware. Após um apply bem-sucedido, o cursor avança para o início da leitura ou para o `change_time` observado mais recente, se posterior, mesmo quando o delta está vazio. A janela de segurança reaplica as alterações próximas ao limite na execução seguinte.

```mermaid
flowchart TD
C[Cursor persistido] --> W[Cursor menos 2 min]
W --> T[Tickets alterados]
T --> D[Dynamic Fields e relações]
D --> A[Apply local]
A --> N[Commit e avanço do cursor]
```

### OTRS FULL e CMDB FULL

OTRS FULL resolve os nomes exatos das filas selecionadas, lê tickets atuais e dimensões, lê relações/fields, faz upsert e reconcilia presença somente nas filas incluídas, preservando os demais registros. Por padrão, o conjunto do Cockpit contém `NOC - ATACADO`, `NOC - CANCELAMENTOS NGO`, `NOC - RETIRA EQUIPAMENTO` e `NOC – PENDENTE PAGAMENTO` (travessão no nome real). FAST usa o cursor compartilhado e exige exatamente esse conjunto após um FULL bem-sucedido com a mesma cobertura. CMDB FULL lista os IDs, carrega detalhes, valida classe/completude/duplicação e só então aplica a reconciliação. Um snapshot parcial não deve marcar como ausentes objetos não recebidos.

## Lease e concorrência

`IntegrationLease` mantém uma chave única por fonte, token proprietário, início, heartbeat e expiração. Heartbeat ocorre a cada 20 segundos; o lease vence depois de 180 segundos sem atualização. O lock persistido coordena workers e comandos manuais e permite recuperação após encerramento abrupto.

```mermaid
flowchart LR
A[Worker A] --> L[Adquirir lease]
L --> H[Heartbeat a cada 20 s]
H --> R[Concluir e liberar]
B[Worker B] --> C[Lease ocupado]
C --> K[Pular esta execução]
```

Execução sem lease não deve duplicar trabalho. O lease é coordenação, não garantia de exactly-once: services e upserts ainda precisam permanecer idempotentes.

## Falha, retry e snapshot

```mermaid
flowchart LR
E[Falha remota ou validação] --> F[IntegrationRun FAILED]
F --> M[Preservar mirror anterior]
M --> R[Retry programado em 2 min]
M --> V[Views leem último snapshot]
```

Se apply local falha, cursor FAST não avança. Snapshot incompleto ou inválido não é usado para marcar ausências. Requests web podem continuar; a consequência é menor freshness, não espera síncrona pela origem. Retry automático não substitui FULL controlado após corrupção/indisponibilidade prolongada.

## Estado e freshness

| Registro/campo | O que informa | Como interpretar |
|---|---|---|
| `IntegrationRun` | Tipo/operação, estado, início/fim, duração, contagens e detalhes sanitizados | Diferencie `SUCCESS`, `PARTIAL` e `FAILED`; uma tentativa recente não prova snapshot recente. |
| `IntegrationSyncState.last_started_at` | Tentativa mais recente iniciada | Pode ser falha ou execução ainda em curso. |
| `last_success_at` | Último apply bem-sucedido | Freshness geral do último sucesso local. |
| `last_full_success_at` | Última reconciliação completa bem-sucedida | Confirma quando ocorreu o FULL mais recente. |
| `change_cursor` | Posição incremental OTRS FAST | Só avança depois do apply local. |
| `IntegrationLease.heartbeat_at` / `expires_at` | Dono vivo e vigência do lock | Ajuda a distinguir processo ativo de lease abandonado. |
| `source_last_seen_at` e `is_present_*` | Observação no snapshot da entidade | Não substituem o estado agregado da integração. |

Para avaliar freshness, compare tipo/status/duração do `IntegrationRun`, sucesso geral, sucesso FULL, cursor FAST e heartbeat. Não conclua que o dado está fresco apenas porque o worker está vivo.

## Sincronização manual

Os comandos manuais usam o mesmo mecanismo de lease para evitar concorrência. Use-os somente em ambiente operacional autorizado; eles leem as origens e alteram mirrors/estado local.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_cmdb --apply
.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_otrs --full --apply
.venv\Scripts\python.exe arion_geo\gerenciar.py sincronizar_otrs --fast --apply
```

Sem `--queue`, o comando usa as quatro filas do Cockpit. `--queue` pode ser repetido para um FULL de conjunto específico; FAST não aceita cobertura parcial. Um FULL parcial não torna o conjunto completo apto para FAST.

Não coloque credenciais nos argumentos de shell compartilhado nem em logs.

## Diagnóstico

1. Confira `IntegrationRun` e diferencie sucesso completo, parcial e falha.
2. Compare `last_success_at`, `last_full_success_at` e `change_cursor`.
3. Verifique owner/heartbeat/expiração do lease e se há processo concorrente.
4. Confirme conectividade e configuração mascarada; nunca revele secret.
5. OTRS: confirme schema e resolução única de todas as filas do conjunto esperado para o Cockpit. CMDB: confirme Search e Get completos para as classes esperadas.
6. Corrija a causa e rode FULL controlado se o delta não for confiável.

### Troubleshooting por sintoma

| Sintoma | Checagem e recuperação |
|---|---|
| Worker parado | Confirme o processo/serviço que inicia `integration_worker` separadamente do HTTP. `--once` avalia só um ciclo e encerra. O processo web não é supervisor do Worker. |
| Lease ocupado ou aparentemente preso | Confira a chave e `heartbeat_at` / `expires_at`. Heartbeats ocorrem a cada 20 s; a expiração é 180 s. Depois de confirmar que o dono morreu, o próximo `acquire` pode tomar a lease expirada; não remova a linha manualmente enquanto houver um processo vivo. |
| Primeira inicialização sem dados | O primeiro OTRS delta exige cursor; rode OTRS FULL. CMDB faz FULL. Valide conexão e snapshot antes da aplicação. |
| FULL falhou | Confira `IntegrationRun` e erro sanitizado. Validação/consulta falha preserva o mirror anterior; corrija a origem/schema e refaça FULL controlado. |
| FAST falha por cursor inexistente | É esperado antes do primeiro FULL; o comando recusa avançar sem cursor. Rode FULL autorizado e depois acompanhe `change_cursor`. |
| Cursor não avança | FAST só persiste watermark após apply local bem-sucedido. Compare hora de término, `last_success_at` e `change_cursor`; dry-run não deve ser interpretado como apply. |
| OTRS/CMDB indisponível ou retry frequente | Worker agenda nova tentativa após 2 min, mantendo o mirror existente. Confira credencial mascarada, conectividade, timeout, schema/fila ou resposta Search/Get; não coloque secret em log. |
| Tela mostra snapshot stale | Worker vivo não garante leitura recente. Compare último FULL, último sucesso geral, `IntegrationRun` e lease. A aplicação continua usando o último snapshot local. |
| Relações Ticket–CI incompletas | Compare quantidade de relações atuais e resolvidas para Link; OTRS pode não ter relação válida ou ConfigItem pode não existir no mirror CMDB. Ver [OTRS](..\06-integracoes-e-dados\otrs.md) e [CMDB](..\06-integracoes-e-dados\cmdb.md). |

Não há política automática de retenção de `IntegrationRun` documentada como contrato. Models, tabelas físicas, ownership e restore estão em [Banco de Dados e Modelos](banco-de-dados.md); detalhes das origens em [OTRS](..\06-integracoes-e-dados\otrs.md) e [CMDB](..\06-integracoes-e-dados\cmdb.md); implantação em [Implantação e Produção](implantacao-e-producao.md).
