# Histórico climático persistente e incremental

O banco do ARION mantém cópias meteorológicas derivadas das fontes identificadas. Esse histórico é uma camada operacional/analítica local; não substitui o fornecedor meteorológico nem a fonte oficial OTRS/CMDB. A implementação usa o mesmo ORM em SQLite DEV e MySQL, sem banco paralelo ou dependência nova.

## Retenção e consolidação

`WeatherMonth`, `WeatherLocalCache`, `WeatherGridCache` e a fila `WeatherCollectionJob` foram preservados. Fonte, modelo, método `area-grid-v2` territorial, município/ponto IBGE, coordenadas solicitadas/células devolvidas, unidades e fuso continuam fazendo parte do contrato. O método municipal é identificado como `municipal-point-v1`. Os dados disponíveis, inclusive séries horárias, permanecem no banco.

Cada registro possui revisão, SHA256 do conteúdo, estimativa de bytes UTF-8, resultado da verificação e data de consolidação. A validade HTTP (`expires_at`) não define a retenção do histórico. O código não apaga dados nem solicita novamente um mês fechado completo apenas porque esse prazo venceu.

A janela padrão é **90 dias após o fim do mês para ERA5/Open-Meteo** e **120 dias para NASA POWER**. A Central permite configurar entre 90 e 730 dias. Esses prazos são uma política conservadora do ARION, não garantia de que o fornecedor nunca fará outra correção. ERA5 preliminar pode ser substituído por dados finais após dois a três meses; NASA também recomenda atenção à substituição de meteorologia recente.

Consolidação exige mês de calendário completo, variáveis suportadas íntegras, resumo compatível, unidades/datas válidas, checksum consistente e confirmação dos dados **posterior à janela**. Passar o tempo não transforma uma coleta antiga em confirmação nova. Na composição por trechos, a data de verificação mais antiga é preservada; consultar somente uma célula/dia novo não valida automaticamente valores antigos. Checkpoints de uma rodada anterior não são reutilizados como confirmação final.

Meses fechados completos em revisão são reutilizados até a data de revalidação. Depois da janela, o Worker autorizado confirma os trechos ainda não verificados e consolida automaticamente. Registros consolidados deixam de depender de TTL; uma revisão posterior precisa de ação manual identificada. Corrupção detectada em registro consolidado exige revisão explícita e não autoriza sobrescrita automática. A manutenção verifica checksums sem alterar valores ou horários de coleta.

## Coleta incremental e compartilhamento

Antes de chamar a API, a coleta procura dados compatíveis por fonte, modelo, coordenada, período e contrato temporal. Dados de fontes/métodos distintos não substituem silenciosamente um registro existente. NASA mantém as suas variáveis ausentes explícitas: completude do contrato suportado não inventa rajada/vento máximo ou série horária.

- Grades diárias são combinadas localmente, inclusive intervalos vencidos. Somente lacunas, valores ausentes e trechos que precisam de confirmação são solicitados; intervalos contíguos e coordenadas compatíveis usam lotes existentes.
- No mês atual, o horizonte respeita a disponibilidade histórica da fonte. Após o intervalo de atualização, a cauda provisória padrão de sete dias é revalidada; o prefixo permanece local. A cauda pode ser configurada entre 1 e 31 dias. Novos dias são detectados mesmo com TTL ainda válido.
- Séries horárias usam epoch UTC e mantêm o fuso dos dias locais, incluindo horários de verão históricos. Uma lacuna horária provoca consulta apenas dos dias afetados, a granularidade mínima usada nesse contrato. Variáveis diárias já completas não são pedidas junto com horas ausentes. As margens do mês são preservadas quando disponíveis.
- Municípios compartilham dados somente quando coordenadas, fonte, modelo, método e fuso são compatíveis. A série diária também reutiliza a grade compatível; dados estaduais não se passam por horas municipais. Coordenadas Open-Meteo diferentes não são consideradas equivalentes só por retornarem a mesma célula, pois podem ter ajuste de elevação diferente.
- Checkpoints locais guardam respostas validadas entre tentativas sem trocar a versão ativa. A publicação ocorre após validar o resultado; falha de rede ou HTTP 429 preserva histórico e progresso. Orçamentos, TLS, retry/backoff e `Retry-After` continuam vigentes.

Filtros de UF/região e períodos de **30, 60 e 90 dias** consultam o histórico local, sem coleta em GET ou por frame. Mapa e apresentação mantêm Data Scope no backend e continuam usando os dados armazenados quando o fornecedor está indisponível. Uma janela com meses ausentes mostra cobertura parcial, sem preencher séries com dados fictícios.

## Revisão manual e rastreabilidade

Na Central, selecionar período/UFs e **Estimar revisão histórica**. A prévia identifica a coleta de nova versão dos recortes e localidades já armazenados. Após informar justificativa de 12 a 240 caracteres, **Autorizar nova versão histórica** cria tarefas na fila existente. Não incluir credenciais na justificativa.

A ação exige gestão e execução de integrações, acesso ao Mapa, CSRF, prévia assinada e Data Scope. A autorização é revalidada no Worker; usuário desativado ou permissão revogada impede a chamada externa. Uma revisão não muda de fonte por fallback e não supera um cooldown vigente. Fonte/modelo/método e revisão de origem são fixados na solicitação.

`WeatherHistoryRevision` guarda snapshots identificados por tipo, registro e versão, com restrição de unicidade, digest, motivo, solicitante e identificador da operação. A publicação usa transação, bloqueio de linha e conferência da revisão esperada. A versão anterior permanece legível durante fila, processamento ou falha. A publicação gera evento no serviço central de auditoria; leituras e mudanças visuais não geram eventos.

Não há troca destrutiva de fonte ou método. Um novo contrato utiliza sua identidade própria. Mesmo quando os números não mudam, uma revisão autorizada mantém rastreabilidade da confirmação. Snapshots e dados ativos devem ser incluídos no backup do banco.

## Manutenção e monitoramento

```powershell
# Inventário, integridade e consolidação: sem rede e sem criar coletas.
.venv\Scripts\python.exe arion_geo\gerenciar.py geo_weather_history --limit 5000

# Enfileirar somente necessidades do recorte explicitamente autorizado.
.venv\Scripts\python.exe arion_geo\gerenciar.py geo_weather_history --period 2026-10 --ufs SP RJ --enqueue
```

`--enqueue` exige UFs e período explícitos. A rotina preserva jobs, pausas, cancelamentos, checkpoints e limites. No Worker, uma rodada limitada de manutenção ocorre por hora; coletas automáticas ficam restritas às UFs explicitamente agendadas e a períodos já armazenados. Não se infere uma coleta nacional na ativação ou ao abrir páginas. Inventário de outras grades pode atualizar metadados, sem consolidar fontes não selecionadas.

A Central mostra meses, UFs com mês íntegro, localidades, pendências, versões, estado completo/em revisão/consolidado e estimativa de conteúdo armazenado. Os bytes lógicos UTF-8 são estimativa de payloads/snapshots, **não tamanho físico MySQL**, índices, logs ou backups. Grades globais e o contador global de economia ficam restritos ao superusuário; demais métricas são agregadas após Data Scope.

O contador de HTTP evitados estima lotes de ações de coleta resolvidas localmente. Não conta cada filtro/frame como uma chamada supostamente economizada nem representa consumo faturado pelo fornecedor.

## Migrations e evidência DEV em 13/11/2026

Migrations aditivas geradas pelo Django: **`integracoes.0020_weathercollectionjob_refresh_request_and_more`** (metadados, checkpoints, revisões e snapshots) e **`0021_weatherlocalcache_weather_local_uf_period_idx`** (índice de UF/período/fonte/modelo municipal). Aplicadas no SQLite DEV. MySQL DEV `arion_geo_dev` não possui configuração disponível; execução real nesse banco continua pendente. Não foram usados produção nem OTRS para testar persistência climática.

Validação focada: 120 testes na bateria GEO completa e 41 testes de histórico/fontes/fila após a proteção final de provisoriedade (121 casos distintos), `check`, migrations e build de 35 bundles aprovados. Os testes de versões e de dados consolidados usam somente banco isolado.

O inventário real mantém **outubro/2026: 27/27 UFs, 373 células e um município com série diária/horária real**. Os dois outros registros municipais existentes não têm histórico completo e continuam pendentes. Hashes de todas as 27 UFs e dos três registros municipais confirmaram preservação de séries, resumos e horários de coleta, incluindo as 17 UFs originais. As dez UFs já haviam sido concluídas na entrega anterior; não foi reiniciado o lote.

Nenhum mês de outubro foi consolidado antecipadamente: a confirmação posterior à janela ainda é futura. A validação real consultou o mesmo mês duas vezes, alternou UFs/região e leu 30/60/90 dias com a chamada ao fornecedor bloqueada no processo: **zero chamadas externas e zero incremento no ledger HTTP**. Nas janelas reais de 60/90 dias, somente os 31 dias de outubro disponíveis foram usados; os demais meses não foram simulados. A manutenção real não enfileirou nem realizou nova coleta. Revisões manuais e meses antigos consolidados foram ensaiados com contratos controlados apenas no banco isolado de testes.

O Worker existente foi observado ativo no DEV, sem intervenção no seu processo. Ele deve carregar o código novo no reinício previsto; não se afirma que um processo iniciado antes desta alteração já execute a nova política. Evidências sanitizadas em `var/geo-history-real-result.json`, integridade em `var/geo-history-integrity.json` e Chromium em `var/geo-history/` (Central nos dois temas, 1440/1024/390, prévia sem coleta, Mapa e apresentação). Não publicar sessões, bancos ou snapshots operacionais no Git.

Rollback mantém tabelas, colunas e snapshots aditivos. Código antigo não respeita retenção/consolidação; antes de voltar, interromper a coleta climática no procedimento autorizado para evitar atualizações pelo TTL legado. Não reverter migrations destrutivamente nem remover versões históricas.

Referências: [ERA5 e substituição de dados preliminares](https://confluence.ecmwf.int/pages/viewpage.action?pageId=414588701), [meteorologia recente NASA POWER](https://power.larc.nasa.gov/docs/tutorials/) e [contratos multi-fonte existentes](geo-multifonte.md).
