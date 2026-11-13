# GEO Intelligence nacional, regional e local

O Mapa 2D existente permanece a superfície de exploração, em layout focado na viewport com filtros flutuantes, seis modos (Base, Projetos, Chamados, Reincidência, Motivos e Clima), indicadores compactos, análises expansíveis e drawer por UF. Região e UF são enquadradas pela geometria; exploração manual é preservada no resize. CMDB e OTRS continuam como fontes oficiais; catálogo geográfico e reanálise meteorológica são contexto analítico. Não há escrita nos bancos remotos.

Análises inicia recolhido por padrão no drawer lateral direito (acionável pelo botão superior direito **Análises**), expande em drawer de leitura ampla no desktop (painel inferior no mobile) com rolagem principal única e seções recolhíveis, e convive simultaneamente com o Panorama Territorial à esquerda em desktops largos (> 1180 px). A faixa compacta superior (`geo-top-subband`) reúne a legenda dinâmica do modo e os chips dos filtros ativos com remoção individual. Ver [cobertura nacional, investigação municipal, ícones e filtros](geo-intelligence-nacional-local.md). Os filtros usam drawer único com seções recolhíveis e aplicação em cascata (Cliente → Projeto → Provedor → Região/UF → Status/Motivos); Aplicar atualiza o recorte e fecha o drawer, Limpar reutiliza a limpeza atual. As 27 siglas têm prioridade sobre valores meteorológicos desde o enquadramento nacional; estados pequenos usam linhas-guia. A área útil do mapa desconta os painéis abertos em ambos os lados sem substituir a câmera explorada manualmente.

## Recortes e indicadores

- Brasil e as cinco macrorregiões oficiais do IBGE. Sudeste: ES, MG, RJ e SP.
- Região e UF filtram os querysets antes das contagens e agregações. UF incompatível com a região retorna HTTP 400.
- Cliente, projeto canônico, ISP, motivo, categoria e permissões/escopos existentes são reutilizados.
- Mês padrão: último mês completo. O mês atual é parcial. Links representam o snapshot CMDB atual, mesmo ao consultar incidentes de meses passados; não são inventários históricos retroativos.
- Incidentes são TicketIDs distintos, criados no período, com status OPEN/PENDING/SCHEDULED/RESOLVED (CLOSED legado é RESOLVED) e responsabilidade PROCEDENTE/INDETERMINADO. Resolvidos válidos permanecem no mês.
- Elegibilidade de incidentes por operadora contratante (sem confundir com ISP): Cancelados, improcedentes e `UNKNOWN` são excluídos globalmente. Na **Vivo** e demais operadoras padrão, chamados de cancelamento, sem atuação ou **Sem intervenção técnica** também ficam fora dos indicadores válidos. Na **Claro**, chamados classificados como **Sem intervenção** são preservados como elegíveis quando possuem procedência válida, sem afetar a política da Vivo. `IncidentRules.valid_for_operator` e `IncidentRules.is_valid_ticket` concentram essa seleção no Mapa, detalhes por UF, GEO Intelligence, clima local e Apresentação, preservando `IncidentRules.valid` para Painel e Rankings. A consulta interna de histórico preserva os registros originais no banco. O Cockpit preserva sua política histórica própria. O backlog continua com sua regra `OPEN`/`PENDING`.
- As categorias usam paleta visual distinta por causa com códigos compactos (`RMP`, `ENG`, `MSV`, `EQP`, `CFG`, `RST`, `FIN`, `TEC`, `S/C`), comparação com período anterior (`period_comparison`) e distribuição por macrorregião (`region_category_counts`), derivadas do catálogo de motivos confirmado sem regravar `closing_code_raw`. Motivos financeiros válidos entram na contagem interna e permanecem bloqueados na Apresentação.
- Um ticket com vários links conta uma vez no total; pode aparecer em mais de uma UF vinculada. Não some contagens estaduais para obter o total nacional. Tickets sem localização não são atribuídos artificialmente a uma região.

Camadas: análise existente, cliente/projeto, incidentes válidos, categoria dominante, chuva, temperatura média/mínima/máxima, vento, rajada, direção aproximada e dias de coincidência temporal. A timeline independente das análises aparece em Chamados, Motivos e Clima, com reprodução/pausa/velocidade/reinício e acesso à série detalhada. Base, Projetos e Reincidência ocultam e pausam a reprodução. Cores, valores meteorológicos e incidentes diários reutilizam as séries locais, sem consulta ou reenquadramento por dia. Transições e marcadores são representações territoriais discretas, não observações de circuito. Cinza indica ausência de dados; zero observado permanece distinto de ausência. O gráfico acompanha chuva, temperatura ou vento, com variável/unidade no eixo esquerdo e incidentes no direito. Na comparação combinada, a série factual inclui apenas classificações técnicas conhecidas; registros sem classificação permanecem no total e fora da coincidência. Não há radar, anomalia climatológica ou atribuição automática de causa.

## IBGE

Central → Integrações → GEO Intelligence → IBGE — Regiões e Estados.

O backend consulta `/api/v1/localidades/regioes` e `/api/v1/localidades/estados`, valida os cinco IDs regionais e as 27 associações UF/ID/região e só então substitui `GeographyCatalog`. O Mapa usa catálogo persistente ou catálogo offline versionado. Falhas preservam o último catálogo completo.

O teste é leitura explícita. A sincronização manual enfileira o pedido em `IntegrationSyncState`; o `integration_worker` executa a coleta sob lease. Atualização automática exige a integração habilitada e respeita o intervalo configurado (mínimo 24 horas), com backoff após erro.

Referência: [API de localidades do IBGE](https://servicodados.ibge.gov.br/api/docs/localidades).

## Open-Meteo — fontes e operação

Central → Integrações → GEO Intelligence → Open-Meteo — Clima e Histórico. IBGE e Open-Meteo usam os cards existentes da Central, com saúde, Worker, cache, consumo e datas reais. As páginas possuem Visão geral, Estado operacional, Configurações, Coletas e sincronização e Histórico de operações, com suporte a temas e mobile.

O bootstrap idempotente executa após `migrate` e é reutilizado pelos serviços GEO quando falta cadastro. Novos registros ficam habilitados, com fonte pública, agendamento desligado e nenhuma UF agendada. Registros existentes, credenciais e desabilitações explícitas são preservados. Habilitar a funcionalidade não dispara coleta externa, não concede licença comercial e não depende de `ARION_ENV`.

- Pública: `https://archive-api.open-meteo.com/v1/archive`, sem chave.
- Comercial: `https://customer-archive-api.open-meteo.com/v1/archive`, com chave real protegida pelo DPAPI existente.
- Self-hosted: endpoint HTTPS configurável e compatível com o contrato diário Open-Meteo. Chave opcional fica vinculada ao destino; uma chave comercial não acompanha uma troca arbitrária de host.

Os checkboxes de modalidade/licença anteriores foram removidos. Fonte, atribuição, endpoint sem segredo e condições de utilização acompanham o cache. A fonte pública permanece sujeita às [condições do fornecedor](https://open-meteo.com/en/terms); disponibilizar o conector não declara licença comercial. Instâncias próprias preservam as condições do software e dos dados de origem.

TLS continua validado, redirects são recusados e URLs não aceitam credenciais, query ou fragmento. Self-hosted resolve e fixa os endereços na conexão, preservando SNI/certificado e recusando redes privadas por padrão. Destinos internos deliberados podem ser declarados em `GEO_WEATHER_ALLOWED_HOSTS` (hosts separados por vírgula); isso não desativa TLS. Endpoints público/comercial são validados contra os destinos correspondentes.

Mantidos timeout de 2–30 s, até duas tentativas adicionais para falhas transitórias, orçamento diário incluindo tentativas e limite de 4 MB por resposta/descompactação. HTTP 429 interrompe repetições imediatas, respeita `Retry-After` na fila e não troca silenciosamente para outra fonte. Falhas não armazenam segredo, URL com chave, corpo externo ou exceção bruta.

Testar conectividade valida uma célula do último mês completo e não a grava como estimativa estadual. A Central permite Brasil, macrorregião ou UF/mês com prévia assinada de volume, controles de pausa/retomada/cancelamento, `run_integrations`, `view_map`, CSRF e Data Scope; o Mapa mantém seu recorte autorizado existente. O Worker processa as mesmas filas/caches territoriais `area-grid-v2`, uma UF por vez sob lease. GETs do Mapa não consultam a API meteorológica.

Agendamento é opcional em qualquer fonte e exige seleção explícita de UFs. Somente esses recortes são atualizados para o último mês completo; não há coleta nacional implícita. Jobs antigos configurados para atualização mas sem UFs ficam sem agendamento até selecionar os recortes. Para ensaio isolado: `integration_worker --geo-only --once` executa somente GEO e encerra, sem sincronizar outros módulos. O Worker normal mantém sua operação existente.

Caches públicos legados `open_meteo_evaluation` continuam legíveis em DEV/produção. Cache comercial permanece separado; cada endpoint self-hosted possui identidade própria. O Worker recupera jobs interrompidos e aplica backoff, preservando o cache anterior. Ver [procedimento e coleta real SP/2026-10](../06-integracoes-e-dados/open-meteo-homologacao.md).

## Método climático `area-grid-v2`

1. Usa a geometria local já adotada pelo Mapa (`static/geo/brasil_estados.geojson`), sem novo download por request.
2. Divide o envelope da UF em 4 × 4 estratos. Uma quadratura 8 × 8 por estrato verifica interior do polígono, incluindo buracos e multipolígonos. O ponto representativo é um ponto interior próximo ao centro ponderado do estrato.
3. Obtém até 16 pontos por UF; são múltiplos pontos territoriais, não apenas a capital. A aproximação de área considera longitude, latitude e cosseno da latitude em uma esfera. A amostragem é uma estimativa grosseira, não integração exata da geometria nem cobertura completa da grade ERA5.
4. Solicita explicitamente modelo ERA5, unidades mm e km/h e dias em America/Sao_Paulo. Células retornadas equivalentes são deduplicadas pelos centros devolvidos, acumulando pesos territoriais. Coordenadas solicitadas e células efetivamente retornadas ficam registradas.
5. Calcula chuva diária por média espacial ponderada. Temperatura média diária (°C) é ponderada por área e a média mensal usa os dias observados; mínima/máxima são extremos amostrados. Direção dominante diária (graus) usa média circular ponderada por área, ausente quando os vetores se anulam; o resumo mensal aplica média circular aos dias. Não representa fluxo de vento preciso de um circuito. A chuva mensal soma as médias diárias disponíveis. Dia chuvoso significa média diária ≥ 1 mm; o percentual usa os dias com observação, acompanhado da completude. Vento/rajada são máximos amostrados, não médias estaduais.
6. Para vários estados, a chuva diária é ponderada pelas áreas aproximadas amostradas das UFs disponíveis. Milímetros estaduais nunca são somados entre si. UFs sem dados ficam explícitas; uma agregação parcial não representa cobertura nacional completa.
7. Ausência permanece `null`, nunca zero silencioso. São registrados completude por variável/ponto/dia, cobertura diária de precipitação, pontos, área aproximada, fonte, modelo, instante de coleta, período e versão do método. O histórico recente pode ter atraso de cinco dias; cache do mês corrente tem validade máxima de 24 horas.

`WeatherMonth` guarda séries diárias e unidades por célula em JSON e resumo estadual. A chave única inclui UF, mês, fonte/modalidade, modelo e método; existem índices de período/UF e expiração. `WeatherCollectionJob` deduplica UF/mês e indexa o próximo processamento. Todas as alterações são aditivas.

O registro `VARIABLES` preserva as três variáveis iniciais e acrescenta `temperature_2m_mean/min/max` (°C) e `wind_direction_10m_dominant` (°). O contrato é validado com unidades e permite temperaturas negativas. `v2` mantém a quadratura e identifica o contrato ampliado; caches `area-grid-v1` permanecem legíveis e sinalizados como vencidos, com novas variáveis ausentes. Novas coletas gravam `v2`. Para ampliar variáveis, estenda esse registro e seu agregador. Para refinar a quadratura ou a resolução/modelo, use uma nova versão de método; não reutilize silenciosamente um cache antigo como resultado do novo método.

Limites: a geometria simplificada e a baixa densidade de amostragem restringem a precisão, sobretudo em estados extensos e áreas litorâneas. Não há geocodificação de links ou análise meteorológica precisa de um circuito. O cruzamento atual é por UF e mês/dia e deve ser apresentado como tal. Cobertura operacional depende do histórico efetivamente disponível no mirror OTRS; não são fabricados tickets ausentes.

## Apresentação por cliente

A ação Apresentar exige `configuracoes.view_map` e a nova permissão `configuracoes.present_geo`. Administradores podem concedê-la pela Central de Grupos; grupos existentes não recebem concessão indiscriminada.

O início valida cliente/projeto contra os links autorizados. Sessão autenticada de até duas horas fixa o cliente. Projeto autorizado desse cliente, mês, modo, região, UF, ISP, motivo técnico e categoria podem variar, com validação de cada parâmetro no backend. Seleção de outro cliente, projeto/ISP fora do escopo e classificações reservadas são recusados. Cada API revalida usuário, permissão e Data Scope; parâmetros não ampliam o recorte. `PresentationIsolationMiddleware` impede páginas/APIs internas na mesma sessão até o encerramento. Outras sessões não abrem a apresentação pelo endereço. O encerramento é POST com CSRF e exige acesso à visão interna. Os seis modos e as abas agregadas de projetos, ISPs, motivos e reincidência reutilizam os componentes internos; não há aba de tickets individuais.

Endpoints dedicados retornam uma lista explícita de agregados autorizados. Não retornam títulos, contas, valores, tickets individuais ou motivos financeiros originais. Tickets reservados válidos continuam no total. Não há exportação interna disponibilizada nessa superfície. A tela tem identificação do cliente, links por região, mapa, filtros regionais, clima, série diária, tela cheia e saída explícita.

`project_distribution(total, weights)` recebe somente a quantidade agregada elegível. O método dos maiores restos distribui o total exatamente, com desempate pela ordem dos pesos. Exemplo: 72 com pesos 40/30/30 resulta em 29/22/21. A projeção permanece separada em `distribution_projected`, exibida como barra de **cenário estimado**, com segmentos/tooltip por categoria e sem segunda tabela extensa, e nunca é gravada como motivo OTRS. Séries técnicas usadas na coincidência climática excluem esses registros reservados; não incorporam valores projetados.

Configuração opcional:

- `GEO_PRESENTATION_WEIGHTS`: JSON com categorias técnicas e pesos não negativos; padrão `{"Massiva":40,"Rompimento":30,"Equipamento":30}`.
- `GEO_PRESENTATION_WEIGHT_MODE`: `configured` (padrão) ou `technical`, que usa proporções técnicas do próprio recorte quando disponíveis.

## Endpoints

| Rota | Método | Proteção / finalidade |
|---|---|---|
| `/mapa/intelligence/` | GET | view_map + Data Scope; resumo mensal e cache climático |
| `/mapa/clima/coletar/` | POST | view_map + run_integrations + CSRF; enfileira apenas UFs do recorte autorizado |
| `/mapa/clima/local/` | GET / POST | view_map + escopo; lê cache municipal/janela de TicketID; coleta também exige run_integrations + CSRF |
| `/mapa/apresentar/` | GET / POST | view_map + present_geo; seleção validada e criação da sessão |
| `/mapa/apresentacao/<chave>/` | GET | Página executiva vinculada à sessão |
| `…/dados/`, `…/detalhes/`, `…/intelligence/` | GET | Mesmo escopo fixo; agregados sanitizados |
| `…/encerrar/` | POST | view_map + sessão correspondente + CSRF |
| `/configuracoes/administracao/integracoes/geo/ibge/` | GET | view_integrations; catálogo, configuração e saúde |
| `/configuracoes/administracao/integracoes/geo/open_meteo/` | GET | view_integrations; fonte, configuração, consumo e saúde |
| `…/salvar/` | POST | manage_integrations + CSRF |
| `…/testar/` | POST | run_integrations + CSRF; lease e intervalo mínimo entre testes |
| `…/sincronizar/` | POST | run_integrations + CSRF; IBGE ou prévia/controle/coleta de Brasil, região ou UF/mês, com view_map + Data Scope |

Os endpoints existentes de agregados e detalhes do Mapa também aceitam `region` e `category`. Filtros mensais usam `period=AAAA-MM`.

O Mapa focado envia `cohort=created`: inclusive no modo Motivos, classificações pertencem aos incidentes criados no mês escolhido, seguindo o mesmo recorte da comparação climática. A consulta legada de Motivos sem esse parâmetro mantém a semântica anterior por data de resolução. As APIs de apresentação fixam a coorte mensal de criação no backend.

Temperatura média e agregação diária foram conferidas no [registro oficial de variáveis diárias](https://github.com/open-meteo/open-meteo/blob/main/Sources/App/Controllers/VariableDaily.swift); unidades, extremos e direção constam da [documentação histórica](https://open-meteo.com/en/docs/historical-weather-api#daily-parameter-definition). A contratação/acessibilidade real do plano permanece etapa própria de homologação.

## Diagnóstico e reconciliação

A coleta usa estados `disabled`, `incomplete`, `incompatible`, `unauthorized`, `awaiting_collection`, `queued`, `running`, `completed`, `failed`, `unavailable` e `invalid`. Cadastro ausente recebe bootstrap público; HTTP 409 / `disabled` se aplica à desabilitação explícita. A leitura mensal separa configuração, Worker, possibilidade de coleta e disponibilidade de cache. A ação Configurar integração é restrita aos administradores apropriados; permissão e CSRF continuam obrigatórios na coleta. Fila/processamento são acompanhados por leitura local a cada 15 segundos. Cache disponível mantém o Mapa útil mesmo com Worker parado ou fornecedor indisponível. O antigo HTTP 400 por cadastro ausente é apenas diagnóstico histórico, não o comportamento atual.

O resumo interno inclui `reconciliation`: incidentes distintos, classificados/não classificados, com/sem UF, links afetados/reincidentes distintos e ocorrências estaduais. Tickets de links sem UF confiável permanecem no total e nas séries, sem receber localização inventada. Esse diagnóstico não é enviado à apresentação como campo interno.

## Cancelamentos

Números de ticket nas listagens, Entrada e detalhe abrem a URL produzida por `otrs_ticket_url` somente quando `source_ticket`/entrada fornecem um TicketID real. Nunca convertem o número exibido em TicketID por suposição. Sem vínculo confiável, exibem indisponibilidade. Links usam outra aba com `noopener noreferrer` e não acionam o detalhe local simultaneamente. A ação Visualizar permanece separada. Modais operacionais ignoram backdrop/Escape; fechar por X/Cancelar solicita confirmação quando há alterações. Erros mantêm modal e valores; sucesso preserva o fluxo existente de salvamento/auditoria.

Conclusão excepcional requer justificativa e `override_cancellation_completion`; a evidência adicional deixou de integrar essa operação. Ator, instante, processo e justificativa continuam no evento/auditoria e no registro de conclusão. Evidências históricas e outros fluxos permanecem intactos.

## Validação e implantação

Candidata: [1.4.1](../08-releases/v1.4.1-candidata.md). Ver [roteiro desta entrega](../08-releases/geo-intelligence-implantacao-windows.md). O ambiente disponível é SQLite DEV; não foram encontrados DATABASE_URL/credenciais MySQL DEV, bootstrap local ou utilitários/perfil mysql_config_editor. A validação em `arion_geo_dev` continua pendente antes de implantação em produção. Não usar `arion_geo` nem o banco externo OTRS para testes DEV.
