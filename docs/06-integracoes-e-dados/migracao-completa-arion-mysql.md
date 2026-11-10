# Transferência completa do ARION para MySQL

O comando existente `migrar_banco_arion` ganhou o modo explícito `--full-snapshot`. Ele prepara uma transferência integral para **schema candidato dedicado e vazio**, incluindo Cancelamentos, histórico, pendências, financeiro, pessoas históricas, auditoria, usuários, RBAC, Data Scope, demais módulos e mirrors. Os dados não acompanham o Git.

O modo anterior usa uma seleção de modelos e não cobre Cancelamentos. Sua cópia é bloqueada quando a origem contém processos desse módulo, para evitar uma transferência incompleta. Não use `rollback_banco_arion.py` para importar: ele apenas troca a configuração de conexão para o próximo início.

## Estado e limites

- A origem atual do DEV é SQLite. Isso não constitui validação de compatibilidade do destino MySQL.
- A preparação de código não executa deploy, conexão ao banco de produção, ativação ou substituição do banco existente do servidor.
- O modo completo preserva PKs e FKs, IDs contidos em JSON/auditoria, tabelas intermediárias M:N, valores Decimal, datas e hashes de senha Django. A ordem respeita dependências dos modelos instalados.
- A origem deve ser um backup consistente e imutável, com todas as migrations do mesmo commit aplicadas. Nunca use uma cópia comum do `.sqlite3` enquanto houver escrita ou transfira um journal isolado.
- Sessões e leases de execução não são transferidos. Os usuários fazem novo login; leases são adquiridos normalmente pelo worker.
- Credenciais protegidas das integrações são removidas **apenas no destino**, e as integrações ficam desabilitadas. DPAPI depende da conta/máquina Windows; reconfigure as credenciais na Central de Configurações sob a conta do servidor. Não copie `.env` nem o arquivo de configuração protegido do DEV para produção.
- Arquivos privados/anexos não estão dentro do banco. Transfira o storage correspondente separadamente, preservando caminhos e ACLs. Um banco validado não comprova disponibilidade desses arquivos.
- O modo não mescla bases. Se o servidor já contém dados próprios, usuários ou acompanhamento novo, o comando bloqueia a transferência completa. Preserve o banco atual e use um schema novo; uma eventual consolidação de duas bases exige plano próprio de conflitos e corte.

## Preparação do pacote privado no DEV

Na raiz do projeto, após encerrar escritas no marco de corte:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py check
.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check --dry-run
.venv\Scripts\python.exe arion_geo\gerenciar.py shell -c "from aplicativos.integracoes.services.arion_database_migration import create_sqlite_backup; print(create_sqlite_backup())"
```

O último comando retorna o caminho privado em `var/backups/`. Verifique `PRAGMA integrity_check` e `PRAGMA foreign_key_check` no backup, registre SHA-256 e as contagens e mantenha uma cópia recuperável. Transporte o snapshot e, quando necessários, os arquivos privados por canal corporativo restrito. Excel, CSV, dumps, snapshots, relatórios e nomes reais permanecem fora do repositório.

O pacote preparado nesta entrega está em `var/transferencia-arion/manifest.json`: contém caminho do snapshot, SHA-256, integridade e inventário das tabelas. Ele descreve o DEV no instante da coleta; mudanças posteriores exigem novo snapshot.

## Configuração do destino e ensaio obrigatório

Provisione primeiro MySQL 8 dedicado ao DEV/ensaio, schema `arion_geo_dev`, charset `utf8mb4`, tabelas InnoDB e usuário exclusivo com grants de migrations e leitura/escrita limitados a esse schema. A conta runtime pode ter grants menores após a preparação. Não reutilize a conta somente leitura `arion_migracao` do ArionBroker como conta Django.

Configure e teste esse banco como **candidato**, sem alterar o banco ativo, em Central de Configurações → Integrações → Banco Arion. Use armazenamento seguro local da senha. O fluxo de configuração existente testa a conexão e registra o teste; editar JSON à mão não substitui essa etapa. Para host remoto, valide rede e TLS conforme a configuração corporativa suportada. Não inclua senha em linha de comando.

No ambiente autorizado e com o mesmo código/migrations, defina o caminho do snapshot transportado:

```powershell
$arionSnapshot = 'C:\ARION\privado\arion-snapshot.sqlite3'

.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --prepare --full-snapshot

.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --dry-run --full-snapshot --source-sqlite $arionSnapshot --report var/transferencia-arion/mysql-preview.json
```

`--prepare --full-snapshot` verifica dados preexistentes antes das migrations e remove somente os grupos/escopos padrão criados pelo bootstrap de um candidato vazio. Grupos próprios, escopos personalizados ou outros dados bloqueiam a preparação. Não execute o prepare normal entre o preview e a cópia completa, pois ele pode recriar o bootstrap.

Revise conflitos, cobertura das tabelas, contagens e fingerprint do preview. Somente com candidato vazio e preview correspondente à mesma origem:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --copy-local-data --full-snapshot --source-sqlite $arionSnapshot --approved-report var/transferencia-arion/mysql-preview.json --report var/transferencia-arion/mysql-copy.json

.venv\Scripts\python.exe arion_geo\gerenciar.py migrar_banco_arion --target mysql --validate --full-snapshot --source-sqlite $arionSnapshot --report var/transferencia-arion/mysql-validation.json
```

A cópia ocorre em uma transação de dados e compara contagens e SHA-256 do conteúdo de **cada tabela**, já considerando a remoção deliberada das credenciais. Divergência dentro da cópia reverte a transação. A validação repete a comparação e verifica migrations; nenhum desses comandos ativa o banco. Uma cópia interrompida antes do commit pode ser repetida com o preview ainda válido. Se já houve commit, use `--validate`; a repetição de `--copy-local-data` encontra destino preenchido e recusa sobrescrevê-lo.

## Antes de disponibilizar no servidor

Conclua o ensaio real MySQL e valide login, grupos/escopos, consultas, tabelas de Cancelamentos, detalhes, histórico, totais e exportação. Compare contagens com o manifesto do corte. Reconfigure/teste as credenciais das integrações sob a conta Windows correta e confirme os arquivos privados. Mantenha web/worker parados durante a troca autorizada; não permita duas aplicações gravando em destinos diferentes durante o corte.

A ativação/reinício no servidor é uma etapa operacional separada, após backup do servidor, plano de retorno e autorização. O código dessa entrega não executa essa etapa. Retornar a conexão ao SQLite não traz de volta registros novos gravados no MySQL; preserve ambos os destinos e reconcilie divergências antes de qualquer retorno.

Testes estruturais e de proteção do modo completo não substituem `--copy-local-data` e `--validate` em MySQL real. Sem candidato configurado/testado, essas etapas permanecem bloqueadas.
