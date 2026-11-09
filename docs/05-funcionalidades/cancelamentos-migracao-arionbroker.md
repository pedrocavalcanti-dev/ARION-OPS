# Cancelamentos — importação histórica ArionBroker

Estado em 09/11/2026: **importação histórica executada com sucesso no SQLite DEV**. O importador foi refatorado para suportar duas modalidades: SQLite DEV (via `--allow-sqlite-dev`, restrito a ambiente de desenvolvimento) e MySQL 8 DEV (para migrações futuras com schema `arion_geo_dev`). Todas as 2.147 referências de origem foram importadas e reconciliadas, preservando contas, permissões, triagens OTRS e vínculos CMDB existentes.

## Snapshot conferido

Os quatro arquivos ficam fora do Git, em diretório privado definido por `--source-dir` ou `ARION_CANCELAMENTOS_MIGRATION_DIR`. Não são copiados para static, media ou fixtures. O leitor não executa SQL: interpreta apenas INSERTs com colunas explícitas e literais, validando aspas, escapes, Unicode, NULL, Decimal, PKs e referências. O schema é lido como metadados; seus DROP/CREATE nunca são enviados ao destino.

| Entidade | Extraídos | Importados (SQLite DEV) |
|---|---:|---:|
| Cancelamentos | 162 | 162 |
| Histórico de cancelamento | 1.084 | 1.084 |
| Movimentações de fila | 594 | 594 |
| Observações | 135 | 135 |
| Pendências | 172 | 172 |
| **Total** | **2.147** | **2.147** |

SHA-256 do dump `cancelamentos-dados-20261109-133631.sql`: `0414d3006d24258bcd9d9705d8c0438eac6565841edba92b604493bea2606774`.

Na reconciliação com o mirror DEV em **09/11/2026 às 15:06 (America/Sao_Paulo)**: **161 tickets identificados**, um sem correspondência; **149 vínculos CMDB** (146 por relação Ticket–CI e três por circuito exato único com cliente correspondente); 13 sem associação segura, incluindo uma relação ambígua. Os 162 processos foram importados como candidatos novos, sem conflito ou registro inválido. Produção e origens OTRS/CMDB não foram alteradas.

Anomalias preservadas:

- 141 na fila CONCLUIDO, mas somente 139 com `concluido_em`. IDs legados **6 e 8** têm status `CANCELAMENTO_CONFIRMADO` e conclusão sem data; ficam em Concluídos com aviso de situação histórica a revisar.
- ID **164** tem abertura no ano **0026**. O valor bruto é mantido; a data operacional fica sem preenchimento, sem converter para 2026.
- Cinco aberturas em 2025 são incluídas: todos os 162 cadastros são de 2026. A seleção usa cadastro, não ano de abertura.
- Sete IDs de autores/responsáveis históricos não possuem nomes no snapshot. Preservam-se IDs, sem criar usuários ou copiar IDs para Django Auth. Uma exportação mínima futura pode trazer apenas ID, username, primeiro nome, sobrenome e, se necessário, e-mail corporativo; nunca hashes, senhas, grants ou sessões.

## Mapeamento do domínio

O destino oficial continua sendo `CancellationProcess`. `origin=ARIONBROKER` identifica a admissão histórica, sem aprovação humana fictícia. `intake` passa a ser opcional somente para essa origem; uma constraint exige entrada de triagem nos processos nativos. `source_ticket` referencia o mirror quando encontrado e `ticket_number` preserva identidade textual única, incluindo zeros. Não são criadas entradas artificiais com TicketID/QueueID inventados.

| Fila / status original | Destino | Quantidade |
|---|---|---:|
| PEDIDO / NOVO | ORDER — Pedido | 14 |
| SOLICITACAO / NOVO | REQUEST — Solicitação | 5 |
| PENDENCIA / PENDENCIA_IDENTIFICADA | PENDING — Pendências históricas | 2 |
| CONCLUIDO / CONCLUIDO_CANCELAMENTO | COMPLETED — classificação histórica | 139 |
| CONCLUIDO / CANCELAMENTO_CONFIRMADO | COMPLETED, sem fabricar data/confirmação | 2 |

PENDING preserva a fila principal histórica sem classificar os dois registros artificialmente como Solicitação. A ação humana existente Iniciar tratativa permite retomar esses registros em REQUEST. Nos processos atuais, as pendências continuam coexistindo com a etapa principal. A aba Pendências também mostra pendências abertas de processos históricos concluídos; a contagem sobrepõe outras etapas e não deve ser somada aos totais exclusivos Pedido/Solicitação/Concluídos.

| Colunas de origem | Destino / preservação |
|---|---|
| `id` | `LegacyCancellationReference`: origem + entidade + PK única, checksum, lote e versão |
| `ticket_arion`, `ticket_url` | Número operacional normalizado apenas nas bordas/`#`; valor bruto e URL original preservados. URL legada é texto, não link executável |
| `designacao_circuito`, `cliente`, `cliente_final`, `isp`, `cidade`, `uf` | Campos existentes do processo, com valores originais também no snapshot |
| Projeto / ConfigItemID | Somente identidades existentes comprovadas pelo mirror; sem novos clientes, projetos, provedores ou links |
| `protocolo`, `contato_isp` | `ISPTreatment.protocol/contact`; não implica confirmação do ISP |
| `fila`, `status` | Etapa explícita da tabela anterior; os dois valores originais ficam separados no snapshot e no detalhe |
| `criado_em`, `atualizado_em`, `concluido_em` | Datas do processo preservadas; `completed_by` não é inventado |
| `data_criacao_chamado` | `ticket_opened_on` quando representável; valor bruto sempre preservado |
| `responsavel_id`, `atendente_id`, `apropriado_em` | Identidades/datas históricas no snapshot; responsável atual começa não atribuído |
| `cod_cliente`, endereço, número, complemento, bairro, CEP, canal de contato | Campos estruturados do snapshot, exibidos por grupo no detalhe |
| `agendamento`, `data_primeiro_contato`, `data_proxima_acao`, `data_ultima_interacao`, `proxima_acao` | Campos históricos identificados individualmente; não inventar horário para datas sem hora |
| `data_cancelamento_confirmado` | Fato histórico separado; não preenche automaticamente confirmação/evidência atuais do ISP |
| `observacao`, `observacao_financeira` | Textos originais no snapshot, separados e protegidos; sem transformá-los em notas OTRS |
| Valores principais e faturamento | Tratamento financeiro abaixo; todos os valores brutos permanecem estruturados |

Cada linha das cinco tabelas é preservada integralmente em `LegacyCancellationReference.source_data`, com vínculo para processo e, conforme a entidade, evento ou pendência. Não se descarta coluna sem equivalente nem se concatena o registro inteiro em uma observação genérica.

O dump declara UTC, mas colunas DATETIME não carregam fuso embutido. O comando exige `--source-timezone`: confirmar o fuso de armazenamento no Django legado antes do Apply. O preview preparatório utilizou UTC explicitamente; valores brutos permanecem disponíveis. Ambiguidades de horário de verão são recusadas, sem escolha silenciosa de offset.

## Histórico, pendências e valores

Os **1.813 fatos históricos** (1.084 + 594 + 135) viram `CancellationEvent` com data original e autor local nulo, identificado no detalhe por seu ID legado. A timeline distingue Histórico, Movimentação e Observação ArionBroker de Ação ARION OPS. Movimentações são fatos importados diretamente, não chamadas de workflow. A observação principal/financeira permanece no cadastro histórico, sem inventar quando seu texto foi escrito.

Até revisão de classificação, textos livres históricos exigem `view_cancellation_finance`: descrições/notas podem conter valores. Essa proteção inclui pendências legadas Outros sem valor preenchido; alterá-las exige também gerenciamento financeiro. Movimentações estruturadas de fila são visíveis sob o escopo do processo. O backend filtra antes de renderizar/paginar; não envia JSON bruto ao frontend. A auditoria central registra somente a importação de cada processo, sem replicar os 1.813 fatos como ações atuais do importador.

Todas as **172 pendências** são importadas: `mensalidades_atrasadas→OVERDUE`, `multa_contrato→CONTRACT_FINE`, `multa_equipamento→EQUIPMENT_FINE`, `outros→OTHER`; `ABERTA→OPEN` (158), `RESOLVIDA→RESOLVED` (3), `DESCARTADA→DISCARDED` (11). Categoria/status originais, descrição, valor, criação, prazo, resolução e autor ficam preservados. Conclusão do processo não resolve suas pendências.

Valores principais não nulos geram previsões em `FinancialObligation`: multa→CONTRACT, equipamento→EQUIPMENT, outros→OTHER. No snapshot são **421 campos informados**, incluindo **267 zeros**; NULL não gera obrigação. Usar Decimal, sem arredondamento via float. `charged_amount`, pagamento, comprovante e envio financeiro não são inventados. Cada campo legado é único por processo.

Uma previsão com pendência da mesma categoria recebe `POSSIBLE_DUPLICATE`; as demais ficam `UNRECONCILED`. Isso sinaliza correspondência possível, sem eliminar evidência ou declarar equivalência. **Não somar valores principais e pendências.** Baixa financeira, última competência e confirmação sem nova cobrança são fatos históricos separados e não comprovam liquidação bancária.

Responsável atual pode ser atribuído inclusive em histórico concluído, com o RBAC, escopo, revisão e auditoria existentes. Isso não altera responsável legado nem reabre o processo.

## Preview, dry-run, idempotência e recuperação

- Preview valida arquivos/colunas/tipos, integridade, enums, datas, contagens e anomalias; não consulta origem externa nem escreve domínio.
- Dry-run acrescenta correspondências exatas OTRS, candidatos CMDB, conflitos e contagens por entidade. Suporta `--allow-sqlite-dev` em ambiente DEV (resultando `ready_for_apply=true` quando a integridade for validada) ou validações completas em MySQL 8 DEV.
- Cada entidade recebe uma categoria mutuamente exclusiva: nova, existente, conflito ou inválida; a soma deve ser igual ao extraído.
- Fonte alterada para uma chave já importada conflita por checksum/versão. Processo oficial preexistente ou decisão humana conflitante não é sobrescrito.
- Uma entrada WAITING comprovadamente correspondente pode ser vinculada ao processo importado, preservando a triagem original. Ela deixa o backlog de novas admissões sem receber aprovação fictícia. A proteção considera também tickets que só apareçam no mirror após a importação.
- Apply exige ambiente DEV (`ARION_ENV=development` e `ARION_PRODUCTION=False`). Em SQLite DEV, exige a flag explícita `--allow-sqlite-dev`, integridade SQLite verificada (`PRAGMA integrity_check`), backup íntegro com restauração comprovada e migrations em dia. Em MySQL 8 DEV, exige schema loopback `arion_geo_dev`, usuário sem root, charset utf8mb4, strict mode e tabelas InnoDB. Conta ARION ativa precisa de consulta, importação e escopo global.
- O relatório aprovado precisa ter o mesmo fingerprint de snapshot, schema, versão, fuso e plano atual. Exige backup do destino, SHA-256 e declaração de restauração verificada.
- Lease central impede dois importadores. Cada árvore de cancelamento é transacional; falha reverte essa árvore e preserva as já concluídas. Novo dry-run identifica as existentes e permite retomar, sem duplicar processos, eventos, notas, pendências ou previsões e sem sobrescrever responsável atual.
- `LegacyImportRun` identifica lote/ator/estado/contagens. Ausência de relatório após interrupção pode ser recuperada por novo dry-run e referências persistidas. Não há comando de reset ou exclusão destrutiva por lote.

Relatórios reais ficam em `var/migracao-cancelamentos/` (ignorado pelo Git), sem payloads de notas/credenciais. Os dumps permanecem no diretório privado externo.

## Execução realizada no SQLite DEV (09/11/2026)

A importação no banco SQLite do ambiente DEV foi autorizada expressamente e executada com sucesso:

1. **Backup pré-importação:** gerado via `sqlite3.Connection.backup()` em `var/backups/arion_geo_dev-before-import-20261109-150029.sqlite3` (32.620.544 bytes, SHA-256 `bbb2c0c2140b338d60c70e8e5fe7f8f5d64f87c33a0dc043a35c3076cb25a5b9`). Integridade e restauração em cópia isolada verificadas com sucesso.
2. **Preview executado:** 2.147 registros conferidos, 0 erros no lexer/parser.
3. **Dry-run executado (`--allow-sqlite-dev`):** 162 candidatos novos, 161 tickets OTRS identificados, 149 vínculos CMDB identificados, 0 conflitos, `ready_for_apply=true`.
4. **Apply executado:** lote `191937e19dbc4d60aa75c1996f43ebe3`, status `SUCCESS`. 162 cancelamentos criados, 1.084 eventos históricos, 594 movimentações, 135 observações, 172 pendências, 421 obrigações financeiras e 2.147 referências persistidas.
5. **Dry-run pós-importação:** confirmou 162 processos existentes, 0 novos, 0 conflitos, 0 duplicidades.
6. **Interface `/cancelamentos/`:** exibe os 162 cancelamentos nas abas correspondentes (Pedido: 14, Solicitação: 5, Pendências: 2 + sobreposições, Concluídos: 141), com navegação detalhada e consulta de todas as dimensões históricas e financeiras.

```powershell
# Comandos executados para a importação em SQLite DEV:
$env:ARION_CANCELAMENTOS_MIGRATION_DIR = Join-Path ([Environment]::GetFolderPath('Desktop')) 'arion-migracao'

# Preview
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-timezone UTC --preview --report var/migracao-cancelamentos/preview-real.json

# Dry-run
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-timezone UTC --dry-run --allow-sqlite-dev --actor-id 1 --report var/migracao-cancelamentos/dry-run-sqlite.json

# Apply
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-timezone UTC --apply --allow-sqlite-dev --actor-id 1 --approved-report var/migracao-cancelamentos/dry-run-sqlite.json --backup-file var/backups/arion_geo_dev-before-import-20261109-150029.sqlite3 --backup-sha256 bbb2c0c2140b338d60c70e8e5fe7f8f5d64f87c33a0dc043a35c3076cb25a5b9 --restore-verified --report var/migracao-cancelamentos/apply-sqlite.json

# Dry-run pós-apply (verificação de idempotência)
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-timezone UTC --dry-run --allow-sqlite-dev --actor-id 1 --report var/migracao-cancelamentos/dry-run-after-apply.json
```

## Preparar MySQL 8 DEV sem perder o SQLite atual (Migração Futura)

Procedimento mantido para quando o ambiente MySQL 8 DEV dedicado for provisionado:

1. Disponibilizar MySQL 8 local dedicado ao DEV, ouvindo somente loopback, com InnoDB, utf8mb4 e strict mode. Criar schema vazio `arion_geo_dev` e usuário exclusivo `arion_ops_dev`, com senha local segura. Limitar os grants ao schema DEV: SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX e REFERENCES para migrations; sem privilégios administrativos globais. Não usar o schema da aplicação em produção nem credenciais do ArionBroker/OTRS.
2. Registrar o candidato no mecanismo existente **Banco ARION** da Central de Integrações. Usar host `127.0.0.1`, porta local, `arion_geo_dev` e conta exclusiva. A senha fica protegida pelo mecanismo local existente. Não editar `.env` para usar produção e não ativar candidato vazio, perdendo usuários/mirrors atuais.
3. Parar somente Web/Worker DEV durante o corte. Fazer backup consistente e imutável do SQLite atual e registrar seu caminho. Reutilizar a migração existente do próprio ARION para transportar suas contas, escopos, integrações, mirrors, entradas e os cancelamentos já importados.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --prepare
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --dry-run --source-sqlite $arionSqliteSnapshot
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --copy-local-data --source-sqlite $arionSqliteSnapshot
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --validate --source-sqlite $arionSqliteSnapshot
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --activate
```

4. Reiniciar somente processos DEV com a configuração validada. Confirmar no novo processo `ARION_ENV=development`, backend mysql, `DATABASE()=arion_geo_dev`, versão 8, usuário/porta locais; conferir contagens transportadas, RBAC e mirrors. O mecanismo suporta rollback ao SQLite; manter o backup. Não imprimir a configuração com senha/URL completa.
5. Executar `check` e verificar migrations no MySQL.

## Validação desta etapa

- Preview real: 2.147 registros, estrutura/colunas/tipos e relacionamentos conferidos; nenhum SQL de origem executado.
- Reconciliação e Apply SQLite DEV: 162 cancelamentos, 1.084 eventos, 594 movimentações, 135 observações, 172 pendências e 421 obrigações financeiras importados com sucesso; zero falhas, zero duplicações, transações atômicas por processo.
- Idempotência validada: dry-run após o Apply reconheceu 162 existentes e 0 novos.
- Passaram **23 testes unitários focados** em `test_legacy_import.py` e **67 testes completos** do módulo `cancelamentos`.
- `gerenciar.py check` e `gerenciar.py makemigrations --check` executados sem pendências.

Referências técnicas do leitor: [literais MySQL 8](https://dev.mysql.com/doc/refman/8.0/en/string-literals.html) e [mysqldump](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html). Arquitetura operacional: [Cancelamentos](cancelamentos.md) e [Banco ARION](../02-arquitetura/banco-de-dados.md).
