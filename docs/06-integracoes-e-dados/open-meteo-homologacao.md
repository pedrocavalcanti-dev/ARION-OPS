# Open-Meteo — configuração e homologação GEO

## Estado validado em 12/11/2026

**Rodada nacional atual:** **27/27 UFs**, 373 células territoriais, 31 dias e 100% da área da quadratura/completude em outubro/2026. As dez pendências foram concluídas em 13/11/2026 às 09:15:03 (America/Sao_Paulo), com 19 tentativas HTTP para 130 localizações, sem elevar o orçamento existente de 250. Estimativa conservadora: 287,86 unidades do fornecedor; não é cobrança/saldo informado pela API. Os cinco checkpoints de PI e os hashes das 17 UFs anteriores foram preservados. O bloqueio anterior foi o orçamento interno antes do HTTP, sem evento persistido de 429. Worker GEO observado em vários ciclos com heartbeat ativo e encerrado graciosamente; estado final parado. SQLite DEV, não MySQL. São Paulo/SP, IBGE 3550308, conserva o cache municipal diário/horário real (31 dias e 792 horas com margens); nenhum outro município foi declarado coletado. Ver [fontes, consumo e evidências atuais](geo-multifonte.md). A tabela abaixo registra o ensaio inicial de SP, anterior à expansão.

O fluxo **Central → teste real → solicitação de uma UF → Worker → cache → GEO Intelligence → Mapa** foi executado com sucesso no **SQLite DEV disponível**. Não foi usado MySQL de produção, banco OTRS ou dado meteorológico fictício.

| Evidência SP / outubro de 2026 | Resultado |
|---|---|
| Fonte | Pública · `https://archive-api.open-meteo.com/v1/archive` |
| Chave comercial | Não utilizada; não exigida para a fonte pública |
| Teste pela Central | Sucesso, contrato de 31 dias em uma célula |
| Fila | Somente SP / 2026-10; um job |
| Worker | `integration_worker --geo-only --once`, sucesso na primeira tentativa |
| Cache persistido | `open_meteo_public` / `era5` / `area-grid-v2` |
| Cobertura | 13 células territoriais; 31 dias; completude 100% |
| Chuva mensal estimada | 178,46 mm |
| Temperatura média territorial/mensal | Aproximadamente 22,01 °C |
| Vento / rajada máximos amostrados | 24,5 / 67,7 km/h |
| Requisições HTTP registradas | 14: uma de diagnóstico e 13 da coleta |
| Persistência | 12/11/2026, 16:20, America/Sao_Paulo |
| Retorno ao Mapa | `climate.available=true`, `collection.status=completed`; camadas, indicadores e reprodução conferidos |

Após a rodada única, o Worker encerrou e liberou seu lease: **está parado**, com cache real disponível. Não foi instalado um serviço contínuo por esta tarefa. Esses valores são evidência datada da estimativa territorial, não medição única da capital nem valores fixos do produto.

**MySQL DEV continua pendente.** O diagnóstico encontrou banco ativo SQLite, sem bootstrap de banco, candidato MySQL ou `DATABASE_URL` configurados. O trecho do fluxo que exige persistência e execução em `arion_geo_dev` precisa ser repetido nesse ambiente antes da implantação. O sucesso em SQLite não comprova MySQL.

## Disponibilização automática

`bootstrap_geo_integrations` é conectado a `post_migrate` e reutilizado pelos serviços GEO quando falta o cadastro. Cria IBGE/Open-Meteo habilitados, com fonte pública e valores seguros de timeout/cache/orçamento. **Não substitui configurações existentes, chaves ou desabilitações explícitas.** Não consulta fornecedores, não cria jobs e não inicia o Worker.

O agendamento nasce desligado, com lista de UFs vazia. Não há coleta nacional implícita. A funcionalidade é disponibilizada em DEV/produção sem condicionar fonte pública a `ARION_ENV`, chave ou confirmação administrativa de licença. Usar o conector continua sujeito às [condições da Open-Meteo](https://open-meteo.com/en/terms); o cadastro público não constitui licença comercial.

## Fonte e configurações

Com `view_integrations`, abrir Central de Configurações → Integrações → GEO Intelligence → Open-Meteo. A rota é `/configuracoes/administracao/integracoes/geo/open_meteo/`. A página reúne Visão geral, Estado operacional, Configurações, Coletas e sincronização e Histórico de operações. Alterações exigem `manage_integrations` e CSRF.

| Fonte selecionável | Endpoint / credencial |
|---|---|
| Pública | `https://archive-api.open-meteo.com/v1/archive`; sem chave. |
| Comercial | `https://customer-archive-api.open-meteo.com/v1/archive`; chave real com acesso ao histórico. Nenhum checkbox de licença é exigido ou apresentado como prova de contratação. |
| Self-hosted | URL HTTPS da instância própria, compatível com as variáveis/unidades da API histórica Open-Meteo. Chave opcional, vinculada ao destino. |

O seletor preenche o endpoint padrão público/comercial. A configuração normal não requer editar `.env` ou código. Uma chave comercial existente é preservada e nunca enviada ao endpoint público ou a outro destino após a troca de fonte. Chaves permanecem protegidas por DPAPI e não são renderizadas no frontend.

Para self-hosted em rede privada, declarar os hosts deliberadamente confiáveis em `GEO_WEATHER_ALLOWED_HOSTS`, separados por vírgula. Sem essa configuração, somente endereços globais são aceitos. A resolução é fixada durante a conexão; TLS/SNI/certificado continuam validados, inclusive para hosts internos. Não são aceitos HTTP, redirects, credenciais embutidas, query ou fragmento na URL. Instalar uma cadeia de confiança válida para o serviço próprio; não desabilitar TLS.

Agendamento é opcional em qualquer fonte: habilitar, escolher explicitamente as UFs autorizadas e definir intervalo. Configurações antigas de atualização sem lista de UFs não geram coleta nacional; precisam selecionar os recortes. Limites avançados mantêm timeout 2–30 s, até duas tentativas adicionais, validade do cache e orçamento diário de requisições HTTP, incluindo retries.

## Testar e coletar uma UF

1. Conferir a fonte e usar **Testar conectividade** com `run_integrations`. O teste valida uma célula do último mês completo, não a persiste como estimativa estadual e respeita um minuto entre testes. Repetir durante esse intervalo não altera a saúde do último teste válido.
2. Em **Coletas e sincronização**, escolher abrangência **UF**, **SP / outubro de 2026**, usar **Estimar volume** e **Autorizar e enfileirar o recorte**. Exige `run_integrations`, `view_map`, CSRF e UF presente no Data Scope. Não selecionar Brasil nem agendar outras UFs para esse ensaio.
3. Alternativamente, o botão de coleta do Mapa reaproveita o recorte autorizado e a mesma fila.
4. Conferir o estado do Worker e a fila na Central. Solicitar uma coleta com Worker parado apenas a deixa pendente; não executa chamadas na requisição da página.

Diagnóstico do supervisor existente:

```powershell
.venv\Scripts\python.exe deploy\windows\run_process.py --status worker
```

Para reproduzir **somente uma rodada GEO no DEV**, sem acionar OTRS/CMDB, backlog ou Cancelamentos:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py integration_worker --geo-only --once
```

Não iniciar outro Worker se já existir lease ativo. A execução contínua usa o supervisor já existente sob a identidade Windows correta; esta tarefa não fez deploy nem alterou os serviços de produção.

## Confirmar persistência e exibição

Confirmar job concluído, cache da fonte selecionada, método `area-grid-v2`, modelo ERA5, coleta e completude coerentes. Leitura técnica no banco DEV correto:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py shell -c "from aplicativos.integracoes.models import WeatherCollectionJob, WeatherMonth; print(list(WeatherCollectionJob.objects.filter(uf='SP',period='2026-10-01').values('uf','period','status','attempts','updated_at'))); print(list(WeatherMonth.objects.filter(uf='SP',period='2026-10-01',method='area-grid-v2').values('uf','period','source','model','method','completeness','collected_at','expires_at')))"
```

No Mapa, selecionar Clima/SP/2026-10 e alternar chuva, temperatura, vento/rajada. Expandir a série mensal e reproduzir os dias. As séries locais alimentam cores e indicadores sem novas consultas meteorológicas por frame. Zero observado continua distinto de ausência; falta de clima não interrompe a análise OTRS/CMDB. Coincidência permanece factual, sem causalidade automática ou uso de projeções.

HTTP 429 interrompe retries imediatos e conserva `Retry-After` no próximo processamento. Novas solicitações não antecipam o job pendente. Autenticação/quotas não provocam troca de fonte. Fallback em transporte/5xx requer autorização explícita e mantém caches/modelos/fusos separados. Erros são sanitizados; o cache anterior é preservado. O cache grava fonte, endpoint sem segredo, atribuição e condições de utilização. Caches `open_meteo_evaluation` anteriores continuam legíveis como fonte pública; fontes comerciais e instâncias próprias ficam separadas.

## Aceite

- [x] Bootstrap idempotente, sem rede ou jobs automáticos; configuração existente preservada.
- [x] Pública sem chave em DEV/produção nos testes de contrato; comercial e self-hosted configuráveis.
- [x] Ensaio inicial SP e expansão real para 27/27 UFs em outubro/2026, respeitando quota.
- [x] São Paulo municipal: diário/horário real, sem alteração do espelho ou causa automática.
- [x] Dez UFs concluídas após renovação interna, em 19 HTTP, sem elevar orçamento e preservando hashes/checkpoints.
- [x] Worker GEO executado, sucesso na primeira tentativa e cache real persistido no SQLite DEV.
- [x] Indicadores, camadas e timeline no Mapa com os dados reais.
- [x] Interface em temas claro/escuro e desktop/notebook/mobile, sem overflow horizontal.
- [ ] Repetir persistência/Worker/cache no MySQL DEV `arion_geo_dev`.
- [ ] Homologar credencial/DPAPI comercial ou instância self-hosted real, se essas fontes forem utilizadas.
- [ ] Configurar e confirmar execução contínua do Worker no ambiente de destino.

Evidências atuais em `var/geo-multi/` e diagnósticos `var/geo-multi-*.json`; evidências anteriores em `var/geo-national/`. Migrations aditivas `integracoes.0018` e `0019`, aplicadas no SQLite DEV; homologação MySQL pendente. Evidências iniciais em `var/geo-simple/`: Central, páginas Open-Meteo/IBGE em 1440/1024/390 pixels, chuva/temperatura reais de SP no Mapa. Diagnóstico de coleta em `var/geo-simple-real-result.json`, fora do Git. Nenhuma credencial, cookie ou banco deve ser publicado.

Ver [candidata 1.4.1](../08-releases/v1.4.1-candidata.md) e [implantação Windows](../08-releases/geo-intelligence-implantacao-windows.md). Rollback para código anterior à simplificação pode indisponibilizar as novas fontes; preservar os caches e restaurar somente configuração prévia compatível e autorizada, sem converter fonte pública em comercial nem reverter migrations destrutivamente.
