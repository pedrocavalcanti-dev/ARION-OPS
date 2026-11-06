# OTRS

## Fonte oficial e limite de escrita

OTRS é a fonte oficial dos tickets, estados, relações com ConfigItems e Dynamic Fields. O ARION OPS mantém um espelho local para consulta e análise; Chamados, Dashboard, Cockpit, Mapa e Rankings não escrevem no OTRS e não consultam o servidor remoto durante uma request web.

A integração conecta ao MySQL do OTRS com uma conta dedicada de leitura. O client `OTRSReadOnlyClient` inicia a sessão MySQL em modo somente leitura e limita as consultas de inspeção a `SELECT` e metadados. A conta remota também deve receber grants somente de leitura, limitados ao schema necessário.

## Fila e sincronização

O worker sincroniza o conjunto configurado para o Cockpit: `NOC - ATACADO`, `NOC - CANCELAMENTOS NGO`, `NOC - RETIRA EQUIPAMENTO` e `NOC – PENDENTE PAGAMENTO` (travessão no nome real). FAST busca alterações recentes a cada 2 minutos; FULL reconcilia todas as filas a cada 30 minutos e quando a cobertura muda. `IntegrationLease` evita execução concorrente. Falha ou timeout preserva o último snapshot local e não deve ser interpretado como fila vazia. O comando `sincronizar_otrs --apply` aplica a leitura no mirror local; a conta e o client remotos continuam somente leitura.

`OTRS_NOC_ATACADO_QUEUE_URL` é um deep link para `AgentKPISearch;Profile=251_2`, usado para abrir a fila na interface web. Ele não filtra a sincronização: `OTRSSyncService` resolve os nomes canônicos de fila e consulta `ticket.queue_id`. A cobertura dos quatro conjuntos é persistida no registro FULL e exigida antes do FAST.

O pipeline analítico do Cockpit é:

```text
OTRS (somente leitura)
→ sincronizador FAST/FULL
→ mirror local
→ normalização de estados e campos
→ classificação operacional por fila/estado
→ analytics local com Data Scope
→ Cockpit
```

O Cockpit não abre conexão OTRS ao carregar a tela. FAST mantém o mirror incremental; FULL reconcilia a cobertura configurada e serve como carga/reconstrução quando necessário. Falhas preservam o último snapshot válido.

## Perfis operacionais de cancelamento

Os perfis de referência são definições da tabela `kpi`, coluna JSON `search`; não são `search_profile` e não são usados como identidade de ticket no runtime:

- `681_58` / KPI 681: fila `NOC - CANCELAMENTOS NGO`, estado `em atendimento`.
- `725_58` / KPI 725: a mesma fila, estados `agendado`, `pendente aprovação`, `pendente cliente endereço não localizado`, `pendente cliente local fechado`, `Pendente`, `pendente operadora` e `pendente outros`.
- `680_58` / KPI 680: fila `NOC - RETIRA EQUIPAMENTO`, os estados operacionais `agendado`, `merged`, `aguardando aprovação do gestor`, `aguardando aprovação do solicitante`, `em atendimento`, `new`, `pendente aprovação`, `pendente cliente endereço não localizado`, `pendente cliente local fechado`, `Pendente`, `pendente operadora`, `pendente outros` e `Projeto Especial`.
- `697_58` / KPI 697: fila `NOC – PENDENTE PAGAMENTO`, com os mesmos 13 estados operacionais.

As definições foram localizadas em `kpi.search`, não em `search_profile`. Os quatro perfis não têm filtros ativos de serviço, tipo, responsável ou Dynamic Field. Na consulta somente leitura de 29/10/2026 encontraram 28, 2, 4 e 0 tickets, sem duplicidade entre resultados. O filtro de Pagamento executou com sucesso; no mesmo snapshot, essa fila tinha 221 `closed successful` e 1 `cancelado`, sem ticket nos estados ativos do perfil. Essas contagens são a fotografia datada da inspeção, não valores permanentes. O nome oficial da fila de pagamento usa travessão (`NOC – PENDENTE PAGAMENTO`); o código conserva a grafia antiga com hífen apenas como alias para registros já espelhados, e novas resoluções usam o nome exato da origem.

Os tickets locais preservam a identidade externa do OTRS. `ticket.id` é a chave técnica externa e `ticket.tn` é o número apresentado ao usuário; o ARION não os trata como a mesma coluna. Relações Ticket–ConfigItem são espelhadas separadamente para que um ticket associado a vários itens continue navegável. Saída da fila não significa, por si só, que o ticket foi fechado.

O estado técnico recebido do OTRS é mantido no mirror e convertido pelo catálogo de estados usado pelo produto. Estado desconhecido não é inferido como aberto ou fechado. O campo dinâmico `CodigoFechamento` é preservado em `closing_code_raw`; `FalhaMassiva`, `Falha Massiva` e `Defeito Massivo` são aliases conhecidos da mesma categoria exibida. A canonicalização não apaga o valor original.

## Inspeção segura do schema

A inspeção administrativa de schema usa metadados de `information_schema`, valida database e tabela descobertos e limita a paginação de amostras. Consultas fornecidas ao estudo são restritas a um único `SELECT`; comandos de escrita, efeitos colaterais e leitura de conteúdo sensível são recusados. A amostra não permite explorar tabelas de artigos ou usuários de clientes, e colunas relacionadas a senha, token, sessão, autenticação, artigo, corpo ou nota interna são mascaradas.

O painel não deve expor senha, ciphertext DPAPI, `DATABASE_URL`, artigos, anexos ou corpo de mensagens. A credencial de OTRS é protegida localmente; DPAPI depende da conta Windows que a cadastrou. Depois de restaurar o banco em outra máquina, recadastre a credencial sob a mesma identidade de serviço usada pelo Worker.

## Operação

Configure host, schema, usuário read-only e segredo na Central de Integrações. Testes de conexão e exploração de schema são ações administrativas auditadas. Acompanhe a última execução FAST/FULL, cursor, lease, quantidade de tickets e estado do mirror em conjunto; uma tentativa malsucedida recente não substitui a última sincronização bem-sucedida.

O procedimento de operação e recuperação está em [Worker e Sincronização](..\02-arquitetura\worker-e-sincronizacao.md). Os campos e regras de identidade estão em [Dicionário de Dados](..\02-arquitetura\dicionario-de-dados.md), e a relação entre OTRS e os demais mirrors está em [Fontes de Dados](..\02-arquitetura\fontes-de-dados.md).

Zero em uma métrica significa que a sincronização FULL requerida foi aplicada com sucesso e nenhum ticket corresponde ao recorte. Sem FULL válido com a cobertura esperada, a origem é “não sincronizada”; o Cockpit informa essa condição e não apresenta a ausência de dados como zero operacional.


## Histórico de filas e etapas

O OTRS mantém eventos `Move` e `StateUpdate` em `ticket_history`. Eles mostram mudanças reais de fila e estado, mas ainda não são persistidos no mirror como uma sequência por ticket. Por isso, o Cockpit não calcula duração histórica por etapa e não substitui a data de criação por uma data de entrada em fila que não esteja materializada no espelho. A lista atual usa o estado e a fila do snapshot; resoluções e fechamentos ficam fora do backlog `OPEN`/`PENDING`.

## Estado bruto e categoria operacional

`external_state_name` preserva o nome bruto recebido do OTRS para rastreabilidade. `business_status` é a categoria derivada usada pelas consultas operacionais: estados OTRS Resolvido e Encerrado são exibidos e contados como `RESOLVED`, incluindo compatibilidade com linhas locais antigas ainda em `CLOSED` antes da migration `0014_normalize_closed_operational_status`. O espelho e os relatórios não alteram `resolved_at`, `terminal_state_at`, `canceled_at` nem o nome bruto da origem. Não se fabricam datas de resolução nem se converte a data posterior de fechamento em resolução.

Para análises de término operacional, `resolved_at` (Dynamic Field `ticketResolvido.value_date`) é a data principal. Se estiver ausente, o ARION pode usar `terminal_state_at`, derivado do `StateUpdate` compatível com o estado terminal atual. Os dois campos continuam disponíveis separadamente para reconciliação e diagnóstico. Se ambos estiverem ausentes, métricas por data terminal e duração final não inventam uma data atual. `CANCELED` e `canceled_at` mantêm a política própria de cancelamento.

O backlog operacional atual inclui somente `OPEN` e `PENDING`. `SCHEDULED`, `RESOLVED`, `CANCELED`, `UNKNOWN` e improcedentes ficam fora; a classificação ativa de filas pode continuar exibindo Agendado separadamente sem incluí-lo no estoque de backlog.
