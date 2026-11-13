# Cancelamentos — carga histórica no MySQL de produção

## Contrato e proteções

O importador existente possui modo produtivo explícito `--production-import`, separado do deploy 1.4.0. Sem essa opção, produção continua bloqueada. Esta atualização de código não executa a carga no servidor, não muda versão/tag e não exige novas migrations, instalação ou build frontend.

O destino permitido é exclusivamente **MySQL 8, schema `arion_geo`, loopback, usuário não-root**, `ARION_ENV=production`, `ARION_PRODUCTION=True` e `DEBUG=False`. Exige tabelas InnoDB, utf8mb4, strict mode e migrations já aplicadas. A configuração ativa é apenas conferida; não há troca de conexão. Nunca mudar o ambiente para development nem utilizar `--allow-sqlite-dev` para contornar essas proteções.

Todos os modos produtivos exigem ator ativo com `import_cancellation_history`, `view_cancellations` e Data Scope global. Preview e Dry-run não escrevem domínio. Apply exige confirmação literal, Dry-run apto com fingerprint atual, hashes dos arquivos, backup do destino e evidência privada do restore testado.

A origem são INSERTs das cinco tabelas `cancelamentos_cancelamento`, `cancelamentos_historicocancelamento`, `cancelamentos_historicofila`, `cancelamentos_observacaocancelamento` e `cancelamentos_pendenciacancelamento`. O parser existente lê literais/colunas/PKs/FKs; **nenhum SQL do dump/schema é executado**, e nenhuma conexão ao ArionBroker é aberta.

Dump, schema, backup, evidência de restore e relatórios produtivos devem ficar fora de qualquer checkout Git e de static/media, em pasta privada com ACL do Windows. O comando verifica localização e hashes; a operação é responsável por conferir ACL e a veracidade da evidência de restore. Não publicar dados pessoais, backups ou credenciais.

## Preservação e reconciliação

O contexto informado para o servidor em 10/11/2026 é zero processos oficiais e 13 entradas OTRS. Isso não é uma pré-condição fixa nem uma consulta remota realizada pelo DEV: o Dry-run lê o estado real no momento da execução.

- Cada processo legado é reconciliado por origem + entidade + PK, número OTRS normalizado e TicketID real do mirror. Relações Ticket–CI atuais ou circuito/cliente exatos e únicos fornecem candidatos CMDB. Ambiguidades ficam explícitas, sem associação arbitrária ou cadastros paralelos.
- As 13 entradas não são convertidas em pedidos por existirem na fila. Somente processos efetivamente presentes na extração histórica são importados. Uma entrada WAITING correspondente pode ser vinculada ao processo histórico sem mudar sua decisão, revisão ou responsável. A consulta do módulo exclui da triagem pendente o ticket já admitido historicamente. Decisão humana incompatível ou processo oficial sem reconciliação segura bloqueia a carga.
- Etapas, status, datas/fuso, protocolos, contatos, valores Decimal, NULL e zeros são preservados pelo mapeamento existente. O snapshot original e os IDs de autores/responsáveis são conservados nas referências e fatos históricos. Nomes ausentes são enriquecimento pendente, não motivo isolado de bloqueio. Não criar contas Django nem atribuir responsável atual automaticamente.
- Não há aprovação atual fictícia, confirmação ISP, comprovação de pagamento, escrita OTRS/CMDB ou alteração de status de link. Valores das pendências não são somados novamente aos três campos principais.
- Repetir o mesmo snapshot reconhece existentes sem duplicar processos, filhos ou valores. Conteúdo alterado, filhos ausentes ou identidade já admitida incompatível são conflitos bloqueantes; não há sobrescrita automática de alterações locais.

## Preparação no Windows Server

Após publicação e atualização segura do código pelo operador, executar a partir do checkout abaixo. A aplicação 1.4.0 e suas migrations já devem estar instaladas. Conferir o corte do legado e a janela autorizada de escrita: atividade concorrente pode invalidar o Dry-run, exigindo nova revisão. Não parar serviços automaticamente por este roteiro.

O dump atualizado informado fica em `producao-20261110-130653`; confirmar seu hash por canal confiável, a consistência da extração e o fuso dos datetime do legado. Não assumir fuso pelo relógio do Windows. O snapshot anterior de referência tem 162 processos, mas a extração atual determina os totais e etapas; não forçar contagens antigas.

```powershell
Set-Location 'C:\Users\Arion\Desktop\ARION-OPS'
$arionFonte = 'C:\Users\Arion\Desktop\arion-migracao\producao-20261110-130653'
$arionDump = 'cancelamentos-dados.sql'
$arionRelatorios = Join-Path $arionFonte 'relatorios-importacao'
New-Item -ItemType Directory -Force -Path $arionRelatorios | Out-Null
$arionAtorEntrada = Read-Host 'ID do usuário ARION ativo autorizado, com escopo global'
$arionAtor = [int]$arionAtorEntrada
$arionFuso = Read-Host 'Fuso IANA comprovado dos datetime do legado (ex.: UTC)'
$arionSnapshotHash = (Get-FileHash -LiteralPath (Join-Path $arionFonte $arionDump) -Algorithm SHA256).Hash.ToLowerInvariant()
$arionComum = @('--source-dir', $arionFonte, '--dump', $arionDump,
    '--source-timezone', $arionFuso, '--actor-id', "$arionAtor",
    '--production-import', '--snapshot-sha256', $arionSnapshotHash)

# 1. Preview: estrutura, contagens, etapas, warnings e identidade da origem.
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py importar_cancelamentos_legado @arionComum --preview --report (Join-Path $arionRelatorios 'preview.json')
if ($LASTEXITCODE -ne 0) { throw 'Preview bloqueado; revisar o relatório privado.' }

# 2. Dry-run: revisão obrigatória contra o destino real, sem gravar processos.
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py importar_cancelamentos_legado @arionComum --dry-run --report (Join-Path $arionRelatorios 'dry-run.json')
if ($LASTEXITCODE -ne 0) { throw 'Dry-run bloqueado; não executar Apply.' }
```

Revisar no JSON: `ready_for_apply`, `errors`, `warnings`, `blocking_conflicts`, `counts`, `stages`, `queue_status`, `entities`, `reconciliation`, `otrs_matched`, `intakes_matched`, `cmdb_matched`, `ambiguous_links`, `records`, `derived_financial_values`, `financial_totals` e `financial_unknown_counts`. As categorias novos/existentes/conflitos/inválidos são exclusivas. Os cinco datasets incluem os fatos históricos, movimentações, observações e pendências; valores principais derivados são conferidos à parte. Warnings de datas históricas ou nomes ausentes exigem revisão, sem inventar informação.

## Backup e evidência do restore

Utilizar o backup MySQL consistente do **destino `arion_geo`**, com identificação `MySQL dump` / `Database: arion_geo` no cabeçalho. Verificar restauração em ambiente isolado e registrar contagens/integridade, executor, horário com fuso e referência ao relatório desse ensaio. O comando valida o hash e a declaração; não executa backup nem restore e não verifica sozinho que um operador realmente os realizou.

O JSON privado de evidência exige os campos abaixo. Este exemplo é apenas o formato; preencher com fatos comprovados, sem emitir declaração automática de sucesso:

```json
{
  "database": "arion_geo",
  "backup_sha256": "SHA256_REAL_DO_BACKUP",
  "restore_verified": true,
  "verified_at": "DATA_HORA_ISO8601_COM_FUSO",
  "validated_by": "OPERADOR_RESPONSAVEL",
  "verification_reference": "REFERENCIA_PRIVADA_DO_ENSAIO_E_CONFERENCIAS"
}
```

Não utilizar backup do ArionBroker, arquivo de outro destino ou simples cópia de relatório como evidência de restauração. Preservar também a configuração e o storage conforme o procedimento operacional do servidor.

```powershell
$arionBackup = Read-Host 'Caminho absoluto do backup MySQL arion_geo com restore verificado'
$arionRestore = Read-Host 'Caminho absoluto do JSON privado da evidência de restore'
$arionBackupHash = (Get-FileHash -LiteralPath $arionBackup -Algorithm SHA256).Hash.ToLowerInvariant()

# 3. Somente após revisar Dry-run, backup/restore e autorizar a janela:
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py importar_cancelamentos_legado @arionComum --apply --approved-report (Join-Path $arionRelatorios 'dry-run.json') --backup-file $arionBackup --backup-sha256 $arionBackupHash --restore-verified --restore-evidence $arionRestore --confirm-production-apply IMPORTAR_CANCELAMENTOS_ARION_GEO --report (Join-Path $arionRelatorios 'apply.json')
if ($LASTEXITCODE -ne 0) { throw 'Apply interrompido; conferir lote e falha antes de nova tentativa.' }

# 4. Pós-importação: exige todos os processos da origem reconciliados como existentes.
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py importar_cancelamentos_legado @arionComum --validate --report (Join-Path $arionRelatorios 'validacao.json')
if ($LASTEXITCODE -ne 0) { throw 'Reconciliação incompleta; não declarar carga concluída.' }

# 5. Idempotência sem nova escrita: todos existentes, zero novos/conflitos/inválidos.
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py importar_cancelamentos_legado @arionComum --dry-run --report (Join-Path $arionRelatorios 'idempotencia.json')
if ($LASTEXITCODE -ne 0) { throw 'Revisar divergências na verificação de idempotência.' }
$arionReplay = Get-Content -LiteralPath (Join-Path $arionRelatorios 'idempotencia.json') -Raw | ConvertFrom-Json
if (-not $arionReplay.ready_for_apply -or $arionReplay.reconciliation.existing -ne $arionReplay.counts.cancelamento) { throw 'Idempotência não comprovada.' }
```

Os comandos permanecem em produção e utilizam a configuração ativa; nenhum contém senha. Não reutilizar Dry-run de outro snapshot/destino. Se o arquivo mudar, obter novo hash, Preview, Dry-run e aprovação técnica.

## Atomicidade, aceite e recuperação

Apply produtivo adquire o lease existente de importação e revalida o plano dentro de **uma única transação InnoDB para todos os processos e filhos**. Revalida entradas/identidades sob bloqueio e preserva unicidade. Uma falha reverte processos, pendências, valores, referências e auditorias de sucesso da tentativa; o lote FAILED e a auditoria sanitizada de falha permanecem rastreáveis fora da transação revertida. A política DEV de transação por processo continua independente.

Conferir `validated=true`, totais por etapa, fatos/pendências/referências e valores Decimal; amostrar detalhes, timeline e resumo financeiro sob permissões adequadas. Comparar as 13 entradas, decisões/revisões, responsáveis atuais, contas/grupos/escopos, Qualidade e mirrors com o inventário anterior. Uma importação histórica vinculada não é nova aprovação OTRS. Conferir o Dashboard de Cancelamentos e a retirada da espera apenas dos tickets com processo histórico correspondente.

Não apagar lote já bem-sucedido nem restaurar todo o banco automaticamente: isso pode destruir trabalho posterior de usuários. Se o plano acusar mudança do legado, investigar conflito e preparar reconciliação específica; não contornar removendo referências. Restore completo permanece operação autorizada de recuperação, considerando dados posteriores ao backup. `rollback_banco_arion.py` não restaura MySQL; a [transferência completa](../migracao-completa-arion-mysql.md) exige destino vazio e não mescla o servidor em uso.

O comando de reconciliação Excel continua DEV; esta autorização produtiva cobre somente o importador SQL histórico. [Contrato e mapeamento](../../05-funcionalidades/cancelamentos-migracao-arionbroker.md) e [procedimento Windows](../../08-releases/v1.4.0-implantacao-windows.md).

## Homologação isolada desta atualização

Em 10/11/2026, o modo produtivo foi exercitado em MySQL **8.4.13 descartável**, em loopback/porta própria, com schema `arion_geo` e usuário exclusivo não-root. Nenhuma conexão ao servidor real, mudança de `.env` ou alteração do banco DEV foi realizada.

O snapshot local de referência de 09/11/2026 percorreu Preview, Dry-run, backup/restore real, Apply, validação e replay: **162 processos, 1.813 eventos, 172 pendências, 421 obrigações principais e 2.147 referências**. Os totais financeiros Decimal coincidiram com a origem. Treze entradas sintéticas do contexto de ensaio mantiveram todos os seus campos; não houve criação de usuários ou atribuição atual. O segundo Apply importou **zero** registros. A falha injetada no segundo processo de um cenário sintético reverteu também o primeiro e suas auditorias de sucesso.

Testes focados cobrem os guardas de ambiente/destino/hash/backup/confirmação/permissão, conflito por decisão humana ou origem alterada, preservação dos mirrors/triagem e comandos. As regressões do importador DEV usam seu banco de teste independente, sem substituir a homologação MySQL. `check` passou e não há alteração de models/migrations; frontend não foi modificado.

O snapshot atualizado `producao-20261110-130653` permanece no Windows Server e não foi lido no DEV. Os resultados acima não aprovam seus conteúdos nem substituem o Preview/Dry-run no destino real com o hash atualizado e o contexto corrente.
