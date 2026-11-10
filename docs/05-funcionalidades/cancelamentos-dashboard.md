# Dashboard Gerencial de Cancelamentos

Disponível em `/cancelamentos/dashboard/`, pelo botão **Dashboard** da lista operacional. **Voltar aos Cancelamentos** retorna à lista. A página é exclusiva do domínio de Cancelamentos e não modifica o Dashboard geral nem o Cockpit.

## Indicadores e referência

Seis indicadores: total de processos oficiais, Concluídos, em andamento, valor total informado, multas contratuais e equipamentos. Pedido, Solicitação e Pendências compõem o andamento; cada processo conta uma vez. Entrada OTRS não é processo oficial e não integra os totais.

O período usa `CancellationProcess.created_at`, preservado na importação. Não utiliza abertura do ticket ou conclusão como substitutos. Os meses usam o fuso da aplicação. No MySQL, as tabelas de fusos precisam estar disponíveis para as funções temporais Django, como nas demais consultas temporais do projeto.

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

O ledger `FinancialObligation` é agregado no backend com `Decimal`, usando valor cobrado quando existe e previsto como alternativa, exatamente como tabela/detalhe/exportação. As expressões SQL e o agrupamento financeiro ficam em `financial_totals.py`. Multa + Equipamentos + Outros formam o total informado. Não há join nem soma de `CancellationPending`, evitando repetir a mesma cobrança.

NULL continua desconhecido; zero informado continua zero. Um total com categorias/valores ausentes é subtotal conhecido parcial. Zero não substitui ausência. Valores informados não comprovam pagamento; confirmação, baixas e conciliação permanecem independentes. O navegador apenas desenha coordenadas dos gráficos e exibe labels monetários calculados pelo servidor; não calcula os totais financeiros.

`view_cancellations` protege a rota e o serviço. `view_cancellation_finance` controla todos os dados monetários. Sem essa permission, os três KPIs financeiros mostram acesso restrito e valores não são serializados no HTML, JSON dos gráficos, tabela ou tooltips. `CancellationScope` filtra antes das opções, agregações e rankings; registros sem vínculo confiável ou mistos mantêm as restrições existentes. Superuser conserva seu acesso global.

As consultas agrupam dimensões, mês e etapa no banco, sem carregar processos completos no navegador, criar tabelas de agregação ou disparar integrações. Os testes cobrem datas importadas, limite de mês no fuso local, filtros, aliases, NULL/zero, repetição de cobrança em pendência, permissions e Data Scope. Não há migration nova para este dashboard.
