# GEO Intelligence regional e mensal

O Mapa 2D existente permanece a superfície de exploração. CMDB e OTRS continuam como fontes oficiais; catálogo geográfico e reanálise meteorológica são contexto analítico. Não há escrita nos bancos remotos.

## Recortes e indicadores

- Brasil e as cinco macrorregiões oficiais do IBGE. Sudeste: ES, MG, RJ e SP.
- Região e UF filtram os querysets antes das contagens e agregações. UF incompatível com a região retorna HTTP 400.
- Cliente, projeto canônico, ISP, motivo, categoria e permissões/escopos existentes são reutilizados.
- Mês padrão: último mês completo. O mês atual é parcial. Links representam o snapshot CMDB atual, mesmo ao consultar incidentes de meses passados; não são inventários históricos retroativos.
- Incidentes são TicketIDs distintos, criados no período, com status OPEN/PENDING/SCHEDULED/RESOLVED (CLOSED legado é RESOLVED) e responsabilidade PROCEDENTE/INDETERMINADO. Resolvidos válidos permanecem no mês.
- Cancelados, improcedentes, UNKNOWN e motivos explícitos de cancelamento, sem atuação ou sem intervenção técnica são excluídos. `IncidentRules` concentra a seleção usada pelo Mapa, detalhes, indicadores de Suporte, Dashboard e Rankings/exportações. A consulta interna de histórico preserva os registros originais. O Cockpit abre com inclusão de cancelados/improcedentes desligada; suas opções explícitas de consulta histórica e a métrica específica de cancelados continuam disponíveis e são identificadas como histórico ampliado, fora do universo de incidentes válidos. O backlog continua com sua regra OPEN/PENDING.
- As categorias são derivadas do catálogo de motivos confirmado, sem regravar `closing_code_raw`. Valores sem mapeamento confiável ficam sem classificação. Motivos financeiros válidos permanecem na contagem interna.
- Um ticket com vários links conta uma vez no total; pode aparecer em mais de uma UF vinculada. Não some contagens estaduais para obter o total nacional. Tickets sem localização não são atribuídos artificialmente a uma região.

Camadas: análise existente, cliente/projeto, incidentes válidos, categoria dominante, chuva, rajada e dias de coincidência temporal. Cinza indica ausência de dados; zero meteorológico válido permanece distinto de ausência. O gráfico identifica chuva em mm no eixo esquerdo e incidentes no eixo direito. Não há radar, anomalia climatológica ou atribuição automática de causa.

## IBGE

Central → Integrações → GEO Intelligence → IBGE — Regiões e Estados.

O backend consulta `/api/v1/localidades/regioes` e `/api/v1/localidades/estados`, valida os cinco IDs regionais e as 27 associações UF/ID/região e só então substitui `GeographyCatalog`. O Mapa usa catálogo persistente ou catálogo offline versionado. Falhas preservam o último catálogo completo.

O teste é leitura explícita. A sincronização manual enfileira o pedido em `IntegrationSyncState`; o `integration_worker` executa a coleta sob lease. Atualização automática exige a integração habilitada e respeita o intervalo configurado (mínimo 24 horas), com backoff após erro.

Referência: [API de localidades do IBGE](https://servicodados.ibge.gov.br/api/docs/localidades).

## Open-Meteo e licenciamento

Central → Integrações → GEO Intelligence → Open-Meteo — Clima e Histórico.

A integração inicia sem coleta habilitada. Avaliação requer autorização administrativa de uso não comercial/homologação e ambiente fora de produção. Não executa atualização periódica. A política se aplica tanto ao teste quanto ao worker; trocar o ambiente para produção bloqueia jobs gratuitos anteriormente enfileirados.

Uso corporativo regular exige modalidade comercial, chave protegida e confirmação de licença compatível com histórico, Professional ou superior conforme a [matriz do fornecedor](https://open-meteo.com/en/pricing). Confirmar um checkbox registra configuração, não comprova contratação, acesso ao plano ou sucesso da API.

Destinos fixos:

- Avaliação: `https://archive-api.open-meteo.com/v1/archive`.
- Comercial: `https://customer-archive-api.open-meteo.com/v1/archive`.

TLS é validado. Redirects e destinos arbitrários são recusados. Timeout entre 2 e 30 segundos, no máximo duas novas tentativas com backoff, limite diário configurável incluindo tentativas e resposta/descompactação limitadas a 4 MB. Credencial fica em `secret_ciphertext` pelo protetor DPAPI existente. O frontend recebe apenas o estado de configuração. Falhas não armazenam URL, resposta externa ou exceção bruta.

O teste valida uma célula e o contrato diário no último mês completo; essa célula não é persistida como estimativa estadual. Coletas estaduais são solicitadas pelo recorte autorizado do Mapa e executadas no worker, uma UF/mês por vez sob lease global. Repetir o pedido reutiliza job/cache. Uma requisição de abertura do Mapa nunca inicia chamadas meteorológicas externas.

O worker reavalia a licença ao executar cada job, recupera jobs interrompidos e limita a três tentativas por job com backoff. Erros ficam no job e no histórico de saúde. Cache anterior permanece legível e é sinalizado quando vencido. Atualização automática comercial, quando habilitada, agenda as UFs para o último mês completo; não varre todo o histórico.

Referência de parâmetros, unidades e prefixo comercial: [Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api).

## Método climático `area-grid-v1`

1. Usa a geometria local já adotada pelo Mapa (`static/geo/brasil_estados.geojson`), sem novo download por request.
2. Divide o envelope da UF em 4 × 4 estratos. Uma quadratura 8 × 8 por estrato verifica interior do polígono, incluindo buracos e multipolígonos. O ponto representativo é um ponto interior próximo ao centro ponderado do estrato.
3. Obtém até 16 pontos por UF; são múltiplos pontos territoriais, não apenas a capital. A aproximação de área considera longitude, latitude e cosseno da latitude em uma esfera. A amostragem é uma estimativa grosseira, não integração exata da geometria nem cobertura completa da grade ERA5.
4. Solicita explicitamente modelo ERA5, unidades mm e km/h e dias em America/Sao_Paulo. Células retornadas equivalentes são deduplicadas pelos centros devolvidos, acumulando pesos territoriais. Coordenadas solicitadas e células efetivamente retornadas ficam registradas.
5. Calcula chuva diária por média espacial ponderada. A chuva mensal soma as médias diárias disponíveis. Dia chuvoso significa média diária ≥ 1 mm; o percentual usa os dias com observação, acompanhado da completude. Vento/rajada são máximos amostrados, não médias estaduais.
6. Para vários estados, a chuva diária é ponderada pelas áreas aproximadas amostradas das UFs disponíveis. Milímetros estaduais nunca são somados entre si. UFs sem dados ficam explícitas; uma agregação parcial não representa cobertura nacional completa.
7. Ausência permanece `null`, nunca zero silencioso. São registrados completude por variável/ponto/dia, cobertura diária de precipitação, pontos, área aproximada, fonte, modelo, instante de coleta, período e versão do método. O histórico recente pode ter atraso de cinco dias; cache do mês corrente tem validade máxima de 24 horas.

`WeatherMonth` guarda séries diárias e unidades por célula em JSON e resumo estadual. A chave única inclui UF, mês, fonte/modalidade, modelo e método; existem índices de período/UF e expiração. `WeatherCollectionJob` deduplica UF/mês e indexa o próximo processamento. Todas as alterações são aditivas.

Para ampliar variáveis, estenda o registro `VARIABLES` com nome/unidade oficial e seu agregador, mantendo as três variáveis e chaves iniciais. Para refinar a quadratura ou a resolução/modelo, use uma nova versão de método; não reutilize silenciosamente um cache antigo como resultado do novo método.

Limites: a geometria simplificada e a baixa densidade de amostragem restringem a precisão, sobretudo em estados extensos e áreas litorâneas. Não há geocodificação de links ou análise meteorológica precisa de um circuito. O cruzamento atual é por UF e mês/dia e deve ser apresentado como tal. Cobertura operacional depende do histórico efetivamente disponível no mirror OTRS; não são fabricados tickets ausentes.

## Apresentação por cliente

A ação Apresentar exige `configuracoes.view_map` e a nova permissão `configuracoes.present_geo`. Administradores podem concedê-la pela Central de Grupos; grupos existentes não recebem concessão indiscriminada.

O início valida cliente/projeto contra os links autorizados. Sessão autenticada de até duas horas fixa cliente, projeto e mês. Região e UF podem variar dentro desse recorte. Cada API revalida usuário, permissão e Data Scope; parâmetros não ampliam o recorte. `PresentationIsolationMiddleware` impede páginas/APIs internas na mesma sessão até o encerramento. Outras sessões não abrem a apresentação pelo endereço. O encerramento é POST com CSRF e exige acesso à visão interna.

Endpoints dedicados retornam uma lista explícita de agregados autorizados. Não retornam títulos, contas, valores, tickets individuais ou motivos financeiros originais. Tickets reservados válidos continuam no total. Não há exportação interna disponibilizada nessa superfície. A tela tem identificação do cliente, links por região, mapa, filtros regionais, clima, série diária, tela cheia e saída explícita.

`project_distribution(total, weights)` recebe somente a quantidade agregada elegível. O método dos maiores restos distribui o total exatamente, com desempate pela ordem dos pesos. Exemplo: 72 com pesos 40/30/30 resulta em 29/22/21. A projeção permanece separada em `distribution_projected`, identificada como **distribuição projetada**, e nunca é gravada como motivo OTRS. Séries técnicas usadas na coincidência climática excluem esses registros reservados; não incorporam valores projetados.

Configuração opcional:

- `GEO_PRESENTATION_WEIGHTS`: JSON com categorias técnicas e pesos não negativos; padrão `{"Massiva":40,"Rompimento":30,"Equipamento":30}`.
- `GEO_PRESENTATION_WEIGHT_MODE`: `configured` (padrão) ou `technical`, que usa proporções técnicas do próprio recorte quando disponíveis.

## Endpoints

| Rota | Método | Proteção / finalidade |
|---|---|---|
| `/mapa/intelligence/` | GET | view_map + Data Scope; resumo mensal e cache climático |
| `/mapa/clima/coletar/` | POST | view_map + run_integrations + CSRF; enfileira apenas UFs do recorte autorizado |
| `/mapa/apresentar/` | GET / POST | view_map + present_geo; seleção validada e criação da sessão |
| `/mapa/apresentacao/<chave>/` | GET | Página executiva vinculada à sessão |
| `…/dados/`, `…/detalhes/`, `…/intelligence/` | GET | Mesmo escopo fixo; agregados sanitizados |
| `…/encerrar/` | POST | view_map + sessão correspondente + CSRF |
| `/configuracoes/integracoes/geo/ibge/` | GET | view_integrations; catálogo, configuração e saúde |
| `/configuracoes/integracoes/geo/open_meteo/` | GET | view_integrations; licença/configuração, consumo e saúde |
| `…/salvar/` | POST | manage_integrations + CSRF |
| `…/testar/` | POST | run_integrations + CSRF; lease e intervalo mínimo entre testes |
| `…/sincronizar/` (IBGE) | POST | run_integrations + CSRF; pedido ao worker |

Os endpoints existentes de agregados e detalhes do Mapa também aceitam `region` e `category`. Filtros mensais usam `period=AAAA-MM`.

## Cancelamentos

O campo Ticket e o título abrem a URL produzida por `otrs_ticket_url` somente quando `source_ticket`/entrada fornecem um TicketID real. Nunca convertem o número exibido em TicketID por suposição. Sem vínculo confiável, exibem indisponibilidade. Links usam outra aba com `noopener noreferrer`.

Conclusão excepcional requer justificativa e `override_cancellation_completion`; a evidência adicional deixou de integrar essa operação. Ator, instante, processo e justificativa continuam no evento/auditoria e no registro de conclusão. Evidências históricas e outros fluxos permanecem intactos.

## Validação e implantação

Ver [roteiro desta entrega](../08-releases/geo-intelligence-implantacao-windows.md). O ambiente disponível é SQLite DEV; não foram encontrados DATABASE_URL/credenciais MySQL DEV, bootstrap local ou utilitários/perfil mysql_config_editor. A validação em `arion_geo_dev` continua pendente antes de implantação em produção. Não usar `arion_geo` nem o banco externo OTRS para testes DEV.
