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
