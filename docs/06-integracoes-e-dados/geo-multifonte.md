# GEO — fontes, consumo e cache por grade

Estado em **13/11/2026**, no SQLite DEV. Nenhuma implantação em produção, mudança de tag ou configuração MySQL foi realizada.

## Diagnóstico e cobertura real

O bloqueio anterior ocorreu em `_count_request`, **antes da chamada HTTP**, ao atingir o orçamento interno `daily_budget=250`. O diagnóstico encontrou um evento desse orçamento e nenhum evento persistido de HTTP 429. Isso comprova a recusa local observada; não representa acesso ao saldo externo do fornecedor.

Depois da renovação diária, o Worker retomou os dez jobs existentes, incluindo cinco checkpoints de PI, sem reenfileirar as 17 UFs completas. A rodada terminou em **13/11/2026 às 09:15:03 (America/Sao_Paulo)**: **27/27 UFs**, 373 células territoriais, 31 dias e **100% da área da quadratura/completude contratada**, em outubro/2026. Não significa observação contínua de todo o território. Os 27 jobs estão concluídos, sem pendências ou falhas.

A conclusão consumiu **19 tentativas HTTP para 130 localizações**, contra 130 chamadas individuais previstas: redução de aproximadamente 85,4% nas conexões, antes de retries. A estimativa conservadora de consumo do fornecedor foi **287,86 unidades**, não 19 unidades. Não houve HTTP 429, retry ou elevação do orçamento existente de 250. Hashes de `samples`, `summary` e `collected_at` confirmaram as **17 UFs anteriores intactas**. O cache municipal de São Paulo/SP, IBGE 3550308, também permaneceu intacto: 31 dias no resumo e 792 horas incluindo margens. Nenhum outro município foi declarado coletado.

## Contratos e política de seleção

| Fonte | Contrato disponível | Homologação real neste DEV |
|---|---|---|
| Open-Meteo pública | ERA5 diário territorial e diário/horário municipal; sem chave | 27 UFs e São Paulo municipal em outubro/2026 |
| Open-Meteo comercial | Mesmo contrato com credencial vinculada ao endpoint | Contratos controlados; credencial real pendente |
| Self-hosted Open-Meteo | Endpoint HTTPS seguro, certificado e destino validados | Contratos controlados; instância real pendente |
| NASA POWER | Temperatura média/mínima/máxima e precipitação diária UTC | Uma célula real de outubro/2026, 31 dias, sem simular cobertura de UF |
| Copernicus CDS ERA5 | Manifesto e aquisição limitada por área/período via comando | Manifesto validado; credencial indisponível, conta/aceite não verificados, download e conversão/importação NetCDF não homologados |

Os adaptadores online usam a fila, leases e cache territorial existentes. A preferência e a fonte secundária são configuradas na Central. **Fallback exige autorização explícita e falha de transporte/5xx**. Quota interna, HTTP 429, autenticação, resposta inválida, unidades incompatíveis e configuração não provocam fallback. Cada tentativa conserva o cache da sua fonte e os checkpoints das outras variantes.

O backend não agrega nem compara em ranking séries com modelos/fusos incompatíveis, inclusive entre meses. NASA diária UTC não é comparada factual ou graficamente a incidentes diários em America/Sao_Paulo. A fonte/modelo estão nos registros; procedência, referência temporal, completude e limitações são exibidas. Nenhum dado ausente é convertido em zero.

NASA retorna neste ensaio `sources=[MERRA2, GEOSIT, POWER]`, resolução meteorológica 0,5° × 0,625°, unidades `C` e `mm/day`, referência explicitamente UTC. A identidade `power_meteorology` preserva esses metadados; dados recentes são tratados como potencialmente provisórios, sem declarar ERA5 ou MERRA2 definitivo. O adaptador não substitui vento médio por máximo/rajada e declara vento máximo, rajada, direção e horário indisponíveis. `supported_completeness` distingue completude das quatro variáveis suportadas da completude do contrato GEO de sete variáveis. A grade de diagnóstico foi persistida separadamente; a fonte preferencial operacional permanece Open-Meteo pública.

## Consumo, lotes e reutilização

`WeatherGridCache` identifica fonte, modelo, coordenada solicitada/célula NASA canônica, período e contrato diário/fuso. A mesma série não recebe peso territorial de uma UF no cache compartilhado. As áreas entram apenas na composição `area-grid-v2` existente.

- Lotes Open-Meteo possuem até oito coordenadas, com validação do tamanho, ordem quando identificada, localização, datas e unidades de toda a resposta antes da persistência. A amostragem territorial não foi reduzida.
- Pedidos idênticos são deduplicados. NASA utiliza a grade meteorológica nativa para evitar pedidos repetidos de uma mesma célula. Open-Meteo mantém coordenadas exatas porque seleção terrestre/elevação pode alterar valores mesmo com a mesma célula devolvida. Somente respostas diárias idênticas são combinadas territorialmente.
- Caches territoriais e checkpoints anteriores alimentam a grade sem nova coleta ou alteração das evidências originais. Em células fundidas pelo código anterior, somente a primeira coordenada tem resposta recuperável; as demais não recebem valores presumidos no cache por coordenada. Intervalos já cobertos são recortados localmente; um prefixo válido é reutilizado e somente o sufixo ausente é solicitado.
- A coleta municipal pode compartilhar o contrato diário quando coordenadas, fonte/modelo e fuso coincidem. Ela mantém sua série horária própria: um cache diário estadual nunca substitui horas municipais.
- Diagnóstico de conectividade ignora o cache para não apresentar leitura local como disponibilidade ao vivo. Central e Mapa em GET continuam sem coleta.

`WeatherRequestLog` conta cada tentativa HTTP, localizações, estimativa de unidades e resultado seguro, sem URL ou segredo. Retries consomem orçamento. A estimativa Open-Meteo é conservadora: `localizações × max(1,dias/14) × max(1,variáveis/10)`. É uma proteção local, **não faturamento ou saldo informado pela API**. Os valores efetivamente cobrados podem diferir. A Central distingue telemetria nova, contador HTTP legado e limites externos conhecidos.

Novos cadastros possuem orçamento interno de 1.000 HTTP/dia, lote de oito e orçamento estimado de 5.000 unidades/24h. Cadastros existentes são preservados — neste DEV continuam 250 HTTP/dia. Os valores são configuráveis. Na fonte pública, o guard adicional permanece limitado a 600 unidades estimadas/minuto, 5.000/h e 10.000/24h; não implica autorização comercial. Comercial/self-hosted dependem do contrato próprio. NASA opera sequencialmente; o fornecedor não informa saldo de quota nesse contrato. HTTP 429 conserva `Retry-After`, sem repetição imediata ou rotação de fonte/chave/IP.

O orçamento HTTP renova à meia-noite local; janelas estimadas são móveis. Cooldown e `next_attempt_at` são persistidos. O Worker ativo retoma automaticamente no vencimento, sem novo POST. **Retomar** na Central preserva progresso e respeita o cooldown, inclusive externo; não remove uma restrição vigente.

## Worker e persistência

O ensaio executou `integration_worker --geo-only --interval 2 --max-cycles 16`, sem `--once`, exclusivamente no DEV. O heartbeat foi observado ativo com idade de cinco segundos durante o processamento. A fila progrediu em ciclos e o processo encerrou graciosamente, liberando o lease. Estado final: **Parado**, cache disponível. Isso não instala nem homologa um serviço contínuo no servidor de destino.

O diagnóstico considera heartbeat com até 60 segundos, token e lease vigente; distingue parado de heartbeat vencido. Para serviço contínuo, usar o procedimento Windows existente e o escopo autorizado no destino. `--max-cycles` é opcional; zero mantém o laço contínuo.

Migration aditiva **`integracoes.0019_weatherrequestlog_weathergridcache`**, após `0018`, aplicada no SQLite DEV. Não houve configuração válida para `arion_geo_dev`; migration, fila e persistência MySQL continuam pendentes. Rollback mantém tabelas/caches aditivos. Código anterior ignora a grade e não reconhece NASA; restaurar apenas configuração compatível, sem reverter migrations destrutivamente.

## CDS preparado, sem dependência obrigatória nova

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py geo_cds_plan --start 2026-10-01 --end 2026-10-31 --area -23 -47 -24 -46 --output var\geo-cds-manifest.json
```

O manifesto limita a aquisição a 32 dias e 100 graus quadrados, divide mudanças de mês e explicita dataset, variáveis, UTC e condições. `--download` usa `cdsapi` **somente se instalado no ambiente de processamento** e configurado com credencial local/aceite do dataset; erros não exibem credenciais. Registra SHA256 dos artefatos, sem gravar cache operacional automaticamente.

Não existe `.cdsapirc` neste usuário. O runtime Django não ganhou dependências. `cdsapi` deve ser avaliado em ambiente separado; conversão NetCDF pode exigir `xarray` e um backend NetCDF com wheels compatíveis com Python 3.12/Windows. Não se introduziu GDAL/ecCodes nem instalação implícita no Windows Server.

A próxima etapa de conversão/importação deve validar coordenadas, unidades e calendário, transformar K em °C, precipitação acumulada em m para mm com a janela correta da hora precedente e horas adjacentes, manter vento médio/máximo distintos e gravar método/fuso/proveniência separados. **Essa conversão ainda não está implementada/homologada; CDS não aparece como fonte online selecionável.**

## Evidências e referências

Evidências locais, fora do Git: `var/geo-multi/` (painel nos cinco tamanhos e dois temas, legendas de clientes/projetos, perfil/PWA, Central e apresentação Claro); `var/geo-multi-integrity.json`, `geo-multi-before.json`, `geo-multi-real-result.json`, `geo-multi-summary.json` e `geo-nasa-adapter-real.json`. Não publicar cookies, credenciais, bancos ou snapshots. Camadas e imagens usam caches reais; contratos controlados dos testes ficam no banco de testes isolado.

Referências primárias: [histórico Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api), [múltiplas localizações e peso de chamadas](https://openmeteo.substack.com/p/weather-data-for-multiple-locations), [limites e modalidades Open-Meteo](https://open-meteo.com/en/pricing), [NASA POWER diária](https://power.larc.nasa.gov/docs/services/api/temporal/daily/), [resolução e recomendações NASA](https://power.larc.nasa.gov/docs/tutorials/), [atribuição NASA](https://power.larc.nasa.gov/docs/referencing/) e [CDS API e credenciais](https://cds.climate.copernicus.eu/how-to-api).
