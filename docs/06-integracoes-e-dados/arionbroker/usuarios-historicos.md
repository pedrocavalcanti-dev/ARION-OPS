# Nomes históricos dos Cancelamentos

Os fatos importados preservam os IDs de responsáveis, atendentes e autores no ArionBroker. `LegacyPerson` acrescenta somente o nome de exibição por `source + external_id`. Não é uma conta de acesso, não possui senha ou permissões e não referencia `auth.User` do ARION OPS. Atribuição atual e autoria histórica continuam independentes.

## Extração no Windows Server

A conta `arion_migracao` acessa o schema `arionbroker` somente no servidor. Não abrir acesso remoto apenas para esta carga. Executar a consulta abaixo com a conexão local somente leitura já autorizada e exportar o resultado em CSV privado, com cabeçalho e codificação UTF-8. Não executar `SELECT *` nem exportar outras colunas de `auth_user`.

```sql
SELECT u.id AS legacy_user_id, u.username, u.first_name, u.last_name
FROM arionbroker.auth_user AS u
JOIN (
    SELECT responsavel_id AS user_id FROM arionbroker.cancelamentos_cancelamento
    WHERE responsavel_id IS NOT NULL
    UNION
    SELECT atendente_id FROM arionbroker.cancelamentos_cancelamento
    WHERE atendente_id IS NOT NULL
    UNION
    SELECT realizado_por_id FROM arionbroker.cancelamentos_historicocancelamento
    WHERE realizado_por_id IS NOT NULL
    UNION
    SELECT movido_por_id FROM arionbroker.cancelamentos_historicofila
    WHERE movido_por_id IS NOT NULL
    UNION
    SELECT criado_por_id FROM arionbroker.cancelamentos_observacaocancelamento
    WHERE criado_por_id IS NOT NULL
    UNION
    SELECT criada_por_id FROM arionbroker.cancelamentos_pendenciacancelamento
    WHERE criada_por_id IS NOT NULL
) AS referenced_user ON referenced_user.user_id = u.id
ORDER BY u.id;
```

Transferir pelo procedimento privado já utilizado para o legado. Caminho combinado no DEV:

```text
%USERPROFILE%\Desktop\arion-migracao\cancelamentos-usuarios-legados.csv
```

Cabeçalho exato: `legacy_user_id,username,first_name,last_name`. São aceitos CSV com vírgula, ponto e vírgula ou tabulação, UTF-8 (com/sem BOM) ou UTF-16 com BOM. JSON com esses quatro campos também é aceito; `id` pode substituir `legacy_user_id`. E-mail não é necessário nesta carga. Password, hashes, tokens, sessões, grupos, permissions e colunas adicionais são recusados.

## Preview e carga no DEV

Informar em `$actorId` o ID local de um usuário ativo com `view_cancellations`, `import_cancellation_history` e escopo global, ou superuser técnico. Não usar o ID de uma pessoa do legado como ator local.

```powershell
$arquivoPessoas = "$env:USERPROFILE\Desktop\arion-migracao\cancelamentos-usuarios-legados.csv"
# Definir $actorId com o usuário local autorizado.
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_pessoas_cancelamentos --file $arquivoPessoas --actor $actorId
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_pessoas_cancelamentos --file $arquivoPessoas --actor $actorId --apply
```

Sem `--apply`, a execução é somente preview. O resultado contém apenas contagens de novos, atualizados, inalterados e IDs ainda sem correspondência. O comando limita o arquivo a 2 MB, valida colunas, IDs inteiros positivos únicos, tamanhos dos nomes e participação nas referências efetivamente importadas. Erro de validação recusa a carga inteira.

O Apply é transacional, restrito ao DEV e idempotente. Repetir o mesmo arquivo não duplica pessoas, não muda timestamps sem necessidade e não gera nova auditoria para dados inalterados. A auditoria registra origem, IDs e contagens, sem nomes, usernames ou conteúdo do arquivo. Nenhuma linha de cancelamento, pendência, evento ou referência original é reescrita.

O detalhe resolve responsável e atendente anteriores; a timeline resolve autores históricos; pendências resolvem o autor anterior quando informado. Sem correspondência, a interface apresenta **Nome não disponível**. IDs e fatos originais permanecem armazenados. Informações técnicas são consultáveis somente com as permissões administrativas existentes de inspeção/auditoria.

CSV, JSON de pessoas, bancos, backups e relatórios ficam fora do Git. Não disponibilizar esses arquivos por `static`, templates ou rotas públicas. A carga não altera OTRS, CMDB, contas ou concessões de acesso e não constitui deploy.

## Enriquecimento pelo Excel original

O comando `enriquecer_nomes_cancelamentos` lê somente as abas **Cancelamentos** e **Pendências** do relatório original. Ele reutiliza `LegacyPerson`; não exige migrations nem reimporta processos. O Excel é tratado como evidência de dados, sem execução de fórmulas ou instruções de células.

```powershell
$arquivoExcel = "$env:USERPROFILE\Downloads\relatorio_cancelamentos_20261109_162444.xlsx"
# Definir $actorId com o ID local autorizado, como na carga CSV.
.venv\Scripts\python.exe arion_geo\gerenciar.py enriquecer_nomes_cancelamentos --file $arquivoExcel --actor $actorId --report var/cancelamentos-excel/preview.json
.venv\Scripts\python.exe arion_geo\gerenciar.py enriquecer_nomes_cancelamentos --file $arquivoExcel --actor $actorId --report var/cancelamentos-excel/apply.json --apply
```

O preview não escreve nomes. O relatório JSON deve ficar em `var/`, fora do Git, e contém contagens de correspondências, nomes recuperáveis/recuperados, inalterados, ausentes e conflitos por linha/ID, sem nomes pessoais. O comando é restrito ao DEV, exige a mesma autorização de importação histórica e registra checksum do arquivo, IDs legados e contagens na auditoria do Apply. Replay inalterado não altera timestamps nem cria nova auditoria.

A associação de cancelamentos exige **Ticket Arion** único e compatível com a referência importada e o ticket local. Na ausência de ID individual de pendência, exige correspondência única da combinação ticket, tipo, descrição com espaços normalizados, valor decimal, status original, data prevista e criação com precisão de segundos. A criação do dump é convertida do fuso registrado no lote de importação para o fuso do relatório (`--report-timezone`, padrão `America/Sao_Paulo`); não se presume um fuso ausente. Valores e datas são usados apenas para conferir a evidência, sem atualizar o domínio. Linhas duplicadas ou correspondências ambíguas não identificam autores.

O nome **Responsável** é associado ao `responsavel_id` do processo; **Criada por** identifica `criada_por_id` da pendência conciliada. Na aba Pendências, **Responsável** corresponde ao responsável do atendimento pai: a tabela legada de pendências não possui responsável individual. A interface explicita esse contexto e mantém a atribuição atual independente. Só se publica um nome por ID legado quando todas as evidências desse ID concordam; divergência com nome já confirmado é reportada e preserva o cadastro existente. Células vazias e rótulos como “Sem responsável” não criam pessoas nem apagam nomes.

Os eventos históricos resolvem o nome pelo próprio ID de autor preservado no evento, sem usar o responsável do processo como autor presumido. Visão geral e pendências continuam submetidas ao RBAC e Data Scope existentes. O comando não altera responsáveis atuais, decisões, etapas, valores, observações, eventos originais ou snapshots importados.
