# 10 · Espelho local

## Modelos e migrações

CMDBOperatorMirror e CMDBLinkMirror são definidos em aplicativos/integracoes/models.py e foram criados pela migração 0002_otrsstatemapping_integrationrun_records_stale_and_more.py. A migração 0004 adiciona legacy_operator_classification para classificação histórica. Esta documentação não cria nem altera schema.

Os dois espelhos têm source_config_item_id único, source_number único/anulável, campos de origem permitidos, datas de freshness e is_present_in_source. Link tem índices para Provider/presença, UF/cidade, projeto legado/presença e estados. Operadora tem índice por nome/presença. O FK Link.operator é anulável e usa SET_NULL. Veja o catálogo completo em [Dicionário de dados](cmdb-dicionario.md).

## Reconciliação

CMDBSyncService prepara os valores antes de gravar. Em apply, sincroniza primeiro Operadoras para então resolver os IDs locais das FKs Link.operator; ambas as etapas compartilham transação atômica. A chave de upsert é source_config_item_id. source_number é submetido a restrição única e verificação de conflito.

Itens não vistos no snapshot são marcados ausentes, não apagados. Um erro de Search/Get, identidade duplicada, classe inesperada ou colisão de Number interrompe a execução sem aplicar reconciliação. Um registro não resolvido continua no mirror com isp_raw e FK vazia.

## Histórico e dados legados

O espelho não mantém uma versão por mudança para cada atributo. Guarda valores atuais, última presença, última sincronização e se o item apareceu no snapshot. legacy_project e legacy_operator_classification são valores locais de compatibilidade e não campos atualizados pelo payload atual. O serviço não atualiza CMDBLink, que é um modelo separado ligado ao legado de importação XLSX em Base de Links.

## Contagens e operação

IntegrationRun contém resultado por execução e contagens agregadas. IntegrationSyncState conserva checkpoints e erros do scheduler. A tela de Integrações exibe estado agregado sem retornar segredos. O mirror é reconstruível desde que a fonte e a configuração estejam disponíveis; campos legados não presentes na ServiceUp precisam de uma origem histórica independente.

Ver [Sincronização](cmdb-sincronizacao.md), [Base de Links](../05-funcionalidades/base.md) e [Banco de Dados](../02-arquitetura/banco-de-dados.md).
