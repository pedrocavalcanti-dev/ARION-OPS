# Dashboard Gerencial de Cancelamentos

Disponível em `/cancelamentos/dashboard/`, pelo botão **Dashboard** da lista operacional. **Voltar aos Cancelamentos** retorna à lista. A página é exclusiva do domínio de Cancelamentos e não modifica o Dashboard geral nem o Cockpit.

## Indicadores e referência

Seis indicadores: total de processos oficiais, Concluídos, em andamento, valor total informado, multas contratuais e equipamentos. Pedido, Solicitação e Pendências compõem o andamento; cada processo conta uma vez. Entrada OTRS não é processo oficial e não integra os totais.

O período usa `CancellationProcess.created_at`, preservado na importação. Não utiliza abertura do ticket ou conclusão como substitutos. Os meses são agrupados em Python com datetimes aware e `timezone.localtime`, usando o fuso da aplicação. O dashboard não depende de `TruncMonth`, `CONVERT_TZ` ou das tabelas de timezone do MySQL. Datas ausentes ou que não podem ser convertidas não recebem mês presumido.

Atalhos: Este mês, Últimos 3 meses, Últimos 6 meses, Este ano e Personalizado. Meses são calendários, terminando na data atual. O padrão é o ano corrente quando os registros mais recentes estão nele; se o último registro pertence a outro ano, oferece esse ano com dados. A consulta personalizada admite até dez anos. Filtros inválidos retornam erros e nenhum dado agregado.

Cliente/projeto e ISP usam opções obtidas apenas do universo autorizado. Projeto reaproveita `canonical_project_name`, `BusinessIdentityRules` e `display_project_name`; quando o projeto falta, usa o campo de cliente final como referência explícita de agrupamento. Provedor prioriza o nome relacionado à CMDB e reutiliza `normalize_provider` para caixa/espaçamento, sem fundir empresas por semelhança. Esses agrupamentos não alteram cadastros nem concedem acesso.

## Gráficos

- **Cancelamentos por mês:** colunas por mês de cadastro, com quantidade no tooltip.
- **Valores informados por mês:** multas, equipamentos e outros empilhados, com valores exatos em reais e subtotal parcial quando necessário. Não há data financeira homologada para esse indicador; utiliza cadastro do processo, indicado na própria página.
- **Distribuição por etapa:** rosca para as quatro etapas atuais, sem duplicação por pendências.
- **ISPs com mais cancelamentos:** oito provedores principais, por quantidade; o tooltip financeiro mostra o total informado apenas para usuários autorizados.

A tabela inferior reúne até oito clientes/projetos, com quantidade, Concluídos e total informado autorizado. Os gráficos usam Apache ECharts local, já utilizado em Qualidade; os dois módulos compartilham os mesmos helpers de paleta, tooltip e tema em `frontend_src/js/shared/chart_theme.js`. Não há CDN ou dependência nova.

Não existe exportação específica de gráficos ou agregados nesta versão. **Exportar** na lista operacional continua produzindo CSV sob seus próprios filtros e permissions. O período da lista usa abertura do ticket; o dashboard usa cadastro do processo, portanto os recortes precisam ser alinhados conscientemente antes de comparar totais.

## Valores e segurança

O serviço central `services/financial_totals.py` (`money_columns` e helpers de categoria) é compartilhado por lista, detalhe, resumo para o Financeiro, exportação CSV e Dashboard, usando `Decimal`. Para cada obrigação (`FinancialObligation`), utiliza o valor cobrado quando existe ou o previsto como alternativa. Soma também pendências monetárias não descartadas (`CancellationPending`) que ainda não possuam vínculo confiável com obrigação: vínculo explícito via `FinancialObligation.source_pending` ou proveniência legada comprovada do ArionBroker (`legacy_field` + `LegacyCancellationReference`, incluindo `valor_outros` cobrindo `OVERDUE` e `OTHER`). Nunca deduplica apenas por igualdade numérica; sem vínculo formal, obrigação e pendência permanecem contabilizadas com sinalização de possível duplicidade para revisão.

`NULL` continua desconhecido; zero informado continua zero. Pendências sem obrigação vinculada não preenchem artificialmente Multa/Equipamentos/Outros com `R$ 0,00`: categorias sem obrigação continuam ausentes e o total permanece assinalado como parcial. Valores informados não comprovam pagamento; previsto, em aberto, pago e resolvido/descartado permanecem distintos, e a resolução de uma pendência nunca marca pagamento realizado. O navegador apenas desenha gráficos e exibe labels calculados pelo servidor; não calcula totais financeiros.

`view_cancellations` protege a rota e o serviço. `view_cancellation_finance` controla todos os dados monetários. Sem essa permission, os três KPIs financeiros mostram acesso restrito e valores não são serializados no HTML, JSON dos gráficos, tabela ou tooltips. `CancellationScope` filtra antes das opções, agregações e rankings; registros sem vínculo confiável ou mistos mantêm as restrições existentes. Superuser conserva seu acesso global.

As consultas carregam apenas as colunas necessárias das obrigações e pendências no escopo filtrado; a consolidação mensal ocorre em Python no fuso da aplicação após RBAC/Data Scope e filtros, sem `TruncMonth`/`CONVERT_TZ`, sem criação de tabelas extras e sem disparo de integrações. Os testes cobrem datas importadas, ausência de timezone MySQL, filtros, aliases, NULL/zero, pendências monetárias sem vínculo, deduplicação por `source_pending`/proveniência legada, permissions e Data Scope. A migration aditiva `cancelamentos.0007_financialobligation_source_pending` acrescenta o vínculo explícito opcional sem alterar dados históricos existentes.
