# 09 · Sincronização

## Agendamento

integration_worker mantém um scheduler persistente dedicado. A cadência CMDB_CADENCE é 10 minutos. O processo acorda no intervalo configurado (padrão 5 segundos), avalia IntegrationRun e IntegrationSyncState e inicia sincronizar_cmdb com --apply e --scheduler-lease quando a execução vence. Falhas entram no estado de retry do worker. O scheduler também controla OTRS; as cadências e cursores OTRS estão detalhados na [referência OTRS](otrs-sincronizacao.md).

O sync manual usa Lease("cmdb"), mantido no banco ARION com heartbeat e validade de 180 segundos. A opção --scheduler-lease evita tomar uma segunda lease quando o worker já controla a execução. Isso previne duplicidade entre processos, além da atomicidade local.

## Etapas

§§§mermaid
flowchart TD
S[Search IDs por classe] --> G[Get em lotes]
G --> V[Validar IDs e classe]
V --> N[Normalizar e montar payloads]
N --> U[Upsert transacional local]
U --> R[Marcar ausentes no snapshot]
§§§

1. Busca IDs de Operadoras e Links de internet, sem Limit local.
2. Carrega detalhes em lotes; batch_size padrão 50, máximo 100.
3. Sanitiza estruturas e exige ID único, retorno integral do Get e classe esperada.
4. Extrai campos whitelisted, rejeita IDs/Numbers duplicados ou colisões com outro ConfigItem.
5. Deriva identidade e resolve ISP, preservando referências sem correspondência.
6. Em apply, grava ambas as classes e reconcilia ausentes dentro de transaction.atomic.

## Modos

O comando sincronizar_cmdb sem --apply (inclusive --dry-run) calcula contagens sem aplicar os espelhos. --apply grava somente o banco local ARION; a operação externa continua sendo somente leitura. O comando de produção do scheduler passa explicitamente --apply. Toda chamada cria IntegrationRun e atualiza estado/auditoria. Em falha, o comando grava estado seguro de falha e não reconcilia a lista parcial.

## Freshness e estados

source_last_seen_at e source_synced_at são atualizados para itens presentes no snapshot; is_present_in_source é marcado verdadeiro. Itens que existiam no mirror mas não vieram no snapshot completo passam a falso sem apagar a linha. IntegrationSyncState registra início, último sucesso/erro, duração e quantidade de Links. IntegrationRun registra contagens, duração e status.

Um snapshot incompleto cancela antes do upsert. A interface pode então continuar servindo o último espelho válido, que estará mais antigo que a fonte. O indicador de integração conectada significa leitura/sync bem-sucedido em seu último checkpoint, não garantia de frescor absoluto em cada request.

## Limites observáveis

Cada Search sem limite e cada Get contam como POST independente. get_batches é estimado a partir de quantidade de itens e lote; http_requests registra solicitações ocorridas. O cliente não possui retry/backoff HTTP próprio. O agendador rege repetição e lease. Não há evidência no código de paginação nativa da instância ServiceUp; confirme esse contrato antes de depender de volumes acima do retorno do Search.

Ver [Espelho local](cmdb-espelho-local.md), [Diagnóstico](cmdb-diagnostico.md) e [Endpoints](cmdb-endpoints.md).
