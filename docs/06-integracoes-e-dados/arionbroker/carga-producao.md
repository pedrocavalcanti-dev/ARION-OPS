# Cancelamentos — roteiro de carga histórica em produção

## Estado e bloqueio de execução

Procedimento futuro, separado do deploy 1.4.0. **Não executado.** A origem é o banco ArionBroker e suas extrações autorizadas; SQLite DEV é evidência de ensaio, não substituto do MySQL do servidor. Git leva código/migrations, não os 162 processos do snapshot nem seus arquivos privados.

O comando `importar_cancelamentos_legado` **recusa qualquer modo em produção**, inclusive Preview. O Apply MySQL também exige `arion_geo_dev`, loopback, MySQL 8 e conta exclusiva não-root. `reconciliar_cancelamentos_excel` possui guardas DEV. A release não contém liberação seletiva para o schema produtivo. Não trocar `ARION_ENV`, não renomear o banco de produção e não usar `--allow-sqlite-dev` para contornar proteção.

Este roteiro prepara entradas, conferências e critérios; a execução produtiva depende de tarefa própria que implemente/homologue o destino, autorizadores e preservação dos dados existentes. Não existe comando produtivo de Apply seguro a publicar nesta revisão.

## Fonte e snapshot atualizado

Solicitar exportação consistente das cinco tabelas `cancelamentos_cancelamento`, `cancelamentos_historicocancelamento`, `cancelamentos_historicofila`, `cancelamentos_observacaocancelamento` e `cancelamentos_pendenciacancelamento`, acompanhada de schema/dicionário, contagens/status reais e fuso comprovado. São os nomes em `legacy_dump.TABLE_COLUMNS`; confirmar o esquema do legado antes de nova extração. Ler o dump como dados pelo parser existente; nunca executá-lo no schema ARION.

O snapshot inicial reconciliou **162 processos, 1.084 históricos, 594 movimentações, 135 observações, 172 pendências, 421 valores principais e 2.147 referências de origem**. Históricos/movimentações/observações geraram 1.813 fatos. Reconciliações posteriores no DEV atualizaram etapas, nomes, pendências e valores: a conferência de 10/11/2026 registrou 11 Pedidos, 5 Solicitações, 3 Pendências e 143 Concluídos, 177 pendências, 426 obrigações e 1.819 eventos. São snapshots datados, não meta fixa do servidor.

Um dump inicial antigo sozinho não contém os deltas posteriores. Incluir extração atualizada ou relatórios complementares autorizados, com versão, checksum e corte inequívocos. Excel não prova uma autoria/data que não contém. Nomes históricos exigem evidência confiável, sem criar contas Django ou importar senhas.

## Pré-requisitos

1. Inventariar no MySQL do servidor processos já existentes, intakes, TicketIDs, PKs e referências legadas, responsáveis locais, grants, dados de Qualidade/auditoria e contagens. Não presumir destino vazio.
2. Definir corte/congelamento ou delta legado e responsável pelas divergências. Separar horário de extração, fuso da origem, datas de cadastro, abertura, conclusão e referência financeira.
3. Guardar dump, workbook/CSV, schemas e relatórios em pasta privada com ACL, fora de Git/static/media pública. Validar SHA-256 e disponibilidade do storage de anexos necessário.
4. Fazer backup MySQL, configuração e storage, com restore isolado comprovado. Registrar ponto de recuperação e como preservar trabalho feito após ele.
5. Homologar em MySQL de ensaio, com cópia autorizada do contexto do servidor, versões/migrations iguais e credenciais próprias. Não apresentar teste SQLite como homologação MySQL ou de concorrência.

## Preview e Dry-run de ensaio

O comando existente pode analisar uma fonte somente em DEV. Os exemplos abaixo são **formas de comando**, com placeholders a substituir no host de ensaio; não configuram banco, não habilitam produção nem autorizam Apply. Relatórios são privados e podem conter dados pessoais.

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-dir <pasta-privada> --dump <dump-atual.sql> --source-timezone <fuso-confirmado> --preview --report <preview-privado.json>
.venv\Scripts\python.exe arion_geo\gerenciar.py importar_cancelamentos_legado --source-dir <pasta-privada> --dump <dump-atual.sql> --source-timezone <fuso-confirmado> --actor-id <id-autorizado> --dry-run --report <dry-run-privado.json>
```

Preview lê/valida a fonte sem admissão; Dry-run valida mapeamento contra o destino DEV permitido e requer ator com `import_cancellation_history` e escopo global. O [contrato do importador](../../05-funcionalidades/cancelamentos-migracao-arionbroker.md) descreve os argumentos reais e condições de Apply DEV. Relatório aprovado é vinculado ao snapshot/destino/mapeamento; não reaproveitar aprovação de outro arquivo ou banco.

Conferir PK/FK, órfãos, enums, datas incompletas, precisão Decimal, TicketID separado de número, CIs, responsáveis/autores, contagens e conflitos. Desconhecido permanece desconhecido; zero explícito não vira NULL. Não deduplicar por título/designação parecida, nem decidir arbitrariamente entre múltiplas pendências semelhantes.

## Liberação e Apply produtivo futuro

Antes de disponibilizar Apply, a implementação específica deverá exigir destino produtivo explicitamente autorizado, versão de mapeamento, ator, autorização de janela, relatório aprovado, hash da fonte e backup/restauração. Deve preservar contas/grupos/escopos/Qualidade e alterações locais já feitas. Não habilitar aplicação irrestrita por uma simples flag de ambiente.

A aplicação será em lotes transacionais rastreáveis, com origem + entidade + PK legada única, mapa origem→destino e checksum. Repetição não cria segundo cancelamento ou filho. Mudanças na fonte são conflito ou delta revisado; não sobrescrevem responsável Django, decisões recentes ou snapshot original. Ticket comprovadamente já admitido exige reconciliação, sem duplicação por nova origem.

Processos legados podem existir sem triagem/aprovação atual fictícia. Novos tickets OTRS continuam sujeitos à análise humana. Conclusão histórica sem data/autor mantém NULL e evidência da origem; não simula confirmação ISP, pagamento, encerramento OTRS ou status Cancelado na CMDB.

## Reconciliação pós-importação

| Conferência | Evidência exigida |
|---|---|
| Totais | Extraídos = novos + já conciliados + conflitos + inválidos, sem sobreposição nem descarte silencioso |
| Etapas | Totais por etapa/período comparados à extração atual, distinguindo pendências filhas de etapa PENDING |
| Filhos | Históricos, movimentos, observações, pendências, obrigações e referências conciliados por identidade |
| Financeiro | Multa + equipamento + outros com Decimal; subtotal parcial/NULL/zero preservados; pendências não somadas de novo |
| Pessoas | ID legado e evidência preservados; nome só quando comprovado; responsável atual ARION independente |
| Vínculos | TicketID/TN/ConfigItemID resolvidos ou explicitamente pendentes, sem criação paralela de clientes/projetos/ISPs |
| Integridade | Constraints/FKs, datas/fuso, amostras de detalhe/timeline/export, RBAC/escopo/financeiro e arquivos privados |
| Idempotência | Novo dry-run/replay não cria duplicatas nem altera decisões locais sem autorização |

Registrar diferença frente às contagens DEV em vez de forçar o MySQL a repetir números fixos. Exigir aceite da operação e TI antes de reabrir escritas/liberar o módulo com o histórico.

## Recuperação

Preservar relatório/lote e estado da falha. Rollback por lote só será seguro com dependências e mudanças posteriores avaliadas; não apagar em cascata processos já tratados. Preferir correção reconciliada quando reversão destruiria trabalho válido. Restore completo é crítico e precisa recompor banco, configuração/storage e dados posteriores ao backup. Não há rollback produtivo de lote implementado/liberado pelo importador atual.

O modo de [transferência completa](../migracao-completa-arion-mysql.md) exige candidato vazio e não mescla bancos. `rollback_banco_arion.py` não restaura MySQL. Procedimentos do servidor: [Implantação e Produção](../../02-arquitetura/implantacao-e-producao.md).
