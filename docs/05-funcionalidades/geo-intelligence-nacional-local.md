# GEO Inteligência — cobertura nacional e investigação local

## Operação e cobertura

Na Central Open-Meteo, selecionar Brasil, macrorregião IBGE ou UF e um mês. **Estimar volume** apresenta o cache reutilizado, requisições restantes antes de retries e orçamento disponível. **Autorizar e enfileirar** confirma esse recorte; a prévia assinada expira em 30 minutos e vincula usuário, UFs, período, fonte e método. O Mapa também exige prévia antes de enfileirar vários estados. Permissões, CSRF e Data Scope são revalidados no backend.

`WeatherCollectionJob` continua único por UF/mês. O Worker processa uma tarefa por vez sob os leases existentes e alterna oportunidades para tarefas locais. Cada resposta territorial validada fica em `progress`; interrupção, pausa e quota não descartam essas células. Pausar também interrompe a UF em andamento após a chamada atual; cancelar atua apenas sobre pendentes/pausados. Retomar preserva o progresso. Cache válido, como SP/outubro-2026, é reutilizado. Não há coleta na renderização, por frame, por ticket ou durante bootstrap.

Ver a evolução de [fontes, lotes, grade persistente e orçamento](../06-integracoes-e-dados/geo-multifonte.md), incluindo NASA diária e aquisição CDS preparada.

HTTP 429 impõe cooldown compartilhado entre tarefas e respeita `Retry-After`. Quota não esgota as três tentativas por falha. O orçamento diário inclui tentativas HTTP meteorológicas; ao esgotar, aguarda o próximo dia. Não há troca de fonte após quota nem aumento automático de limite; fallback em conexão/5xx depende de autorização e preserva modelos/fusos separados. O agendamento existente pode selecionar explicitamente as 27 UFs para o último mês completo; a configuração valida se o volume-base cabe no orçamento até a próxima atualização/expiração, não agenda todos os meses históricos nem habilita coleta nacional implicitamente.

Quantidade de UFs e área coberta são indicadores diferentes. A cobertura territorial é a soma das áreas da quadratura com dados, ponderada pela completude das variáveis/dias, dividida pela área aproximada da quadratura do recorte autorizado. Não é cobertura contínua de radar nem porcentagem de UFs. A interface informa **Cobertura parcial: N de 27 UFs**, células, completude, pendências, fonte, método, consumo e Worker. Indicadores parciais são do **recorte coberto**, não do Brasil completo.

A agregação diária regional/nacional pondera chuva e temperatura média pela área das UFs disponíveis. Não soma milímetros entre estados. Extremos térmicos e vento/rajada preservam a semântica de extremos amostrados. Ausência é `null`; zero é observação válida. Rankings estaduais comparam apenas caches vigentes, completos no mesmo intervalo, e indicam que as posições abrangem somente os recortes disponíveis.

## Localização e cache municipal

A inspeção do espelho atual encontrou cidade, UF e endereço em `CMDBLinkMirror`, sem campos de coordenadas confiáveis do link. `OTRSTicketConfigItem` fornece o vínculo com o ticket. O espelho de tickets possui abertura, resolução e mudanças, sem início real da indisponibilidade. Esses limites não são preenchidos por suposição.

Ao selecionar uma UF, as análises listam municípios CMDB e localidades de incidentes técnicos. **Coletar local** exige `run_integrations`, `view_map`, CSRF e cidade vinculada a links do recorte autorizado. O POST apenas enfileira. A tarefa normaliza cidade/UF por correspondência exata, única e sem distinção de acentos no catálogo oficial IBGE; nomes ambíguos ou sem correspondência permanecem sem cobertura municipal. A leitura territorial da UF continua disponível como referência, sem gerar coincidência municipal artificial.

O catálogo municipal por UF reutiliza `GeographyCatalog`. A malha IBGE fornece um ponto interior próximo ao centro geométrico amostrado. Ele é explicitamente uma **estimativa municipal**, não a coordenada real do link, da ruptura ou do trajeto físico da fibra. Nada é gravado nos espelhos OTRS/CMDB.

`WeatherLocalCache` compartilha fila e resultado por localidade normalizada, mês, fonte e modelo; diversos links/tickets da mesma cidade reutilizam a série. A coleta local consulta ERA5, aproximadamente 0,25°, com variáveis diárias e horárias de precipitação, temperatura, vento, rajada e direção. Inclui um dia de margem em cada borda do mês para janelas de incidentes; o resumo mensal exclui essas margens. O fuso local devolvido pelo fornecedor define os dias, e timestamps horários são validados e armazenados como epoch UTC. Não são disparadas milhares de consultas para o inventário.

**Investigar** consulta somente cache. Um TicketID real opcional permite abrir a janela de seis horas antes a duas horas depois da abertura, com revalidação do vínculo, classificação e escopo. O horário da abertura é identificado como aproximação. Horas ausentes permanecem ausentes; a janela não provoca chamada meteorológica adicional. As APIs locais internas ficam bloqueadas durante a apresentação; esta recebe somente agregados permitidos do cliente fixado.

Referências: [localidades IBGE](https://servicodados.ibge.gov.br/api/docs/localidades), [malhas municipais IBGE](https://servicodados.ibge.gov.br/api/docs/malhas?versao=3) e [API histórica Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api).

## Coincidência factual

Somente tickets distintos com categorias técnicas conhecidas participam da correlação. Motivos financeiros, sem classificação confiável e projeções estatísticas ficam fora. Categorias originais não são alteradas.

- Sem dados suficientes: falta vínculo municipal, cache ou cobertura da janela.
- Sem coincidência identificada: janela coberta sem atingir os limiares considerados.
- Coincidência temporal e geográfica: precipitação horária de pelo menos 1 mm na janela da localidade vinculada.
- Evidência contextual relevante: precipitação horária de pelo menos 10 mm ou rajada de pelo menos 60 km/h na janela. São critérios exploratórios, sem afirmar dano ou causa técnica.

O estado mais forte prevalece por TicketID quando há vários vínculos. O resumo local separa quatro estados (`status_counts`: **Sem dados suficientes**, **Sem coincidência identificada**, **Coincidência identificada** e **Causa confirmada**), além de denominadores explícitos por categoria (`by_category`: avaliáveis vs sem localidade/cache), rankings locais filtráveis (`local_rankings`: chuva acumulada, dias chuvosos, incidentes em dias de chuva, rompimentos, massivas e equipamento/energia), `uf_only_tickets`, `multi_city_tickets` e ressalva de rota (`route_caveat`). Denominadores separam elegíveis, município informado na CMDB, cobertura diária e cobertura horária. Rankings regionais de ocorrências chuvosas usam apenas esses tickets cobertos; não representam risco comparável de regiões sem cobertura. A comparação territorial por UF também exige chuva e incidentes na mesma UF/dia.

Não existe confirmação automática de causalidade. O espelho atual não fornece um campo estruturado de dano meteorológico comprovado; o sistema não extrai uma causa do título por palavras soltas. Evidência técnica precisa continuar sendo confirmada na fonte oficial.

## Mapa, ícones e filtros

O cabeçalho principal preserva logo e perfil, identifica GEO Inteligência e oferece navegação geral, **Análises** e **Apresentar** aos usuários autorizados, inclusive no mobile. A barra secundária reúne os seis modos, período, busca de UF e acesso aos filtros, com quebra de linha e sem rolagem horizontal. A faixa superior (`geo-top-subband`) reúne legenda dinâmica e chips de filtros ativos com remoção individual, sem obstruir a barra inferior ou o palco do Mapa.

Em desktops largos (> 1180 px), o painel esquerdo (**Panorama Territorial**: abas Regiões, Ranking e Concentração) e a superfície direita (**Análises**, Filtros ou Detalhes da UF) funcionam simultaneamente; o recolhimento de qualquer lado atualiza a área útil do canvas Mapbox. Em larguras ≤ 1180 px e mobile, esquerda e direita alternam automaticamente para evitar sobreposição. A barra de **Timeline** temporal possui botão próprio de recolhimento/expansão independente dos demais painéis, e o diagnóstico técnico de cota/worker permanece recolhido em `<details>`. Análises preserva os estados fechado/resumo/expandido sem reconsultar APIs ao alternar leitura.

A apresentação inicia com cliente, projeto pertinente e mês. O cliente permanece fixado no backend e no cabeçalho; os botões globais de cliente não são renderizados. Projetos, ISPs, UFs e catálogos respeitam cliente e Data Scope em cada endpoint. A legenda Base identifica apenas esse cliente; os demais modos usam projetos, escalas e categorias pertinentes. Trocar projeto ou ISP revalida a seleção territorial sem ampliar o cliente. Encerrar usa o fluxo nativo com auditoria e retorna ao Mapa interno.

Ícones Mapbox são opcionais: chuva, calor, frio e vento. No Brasil representam agregados estaduais em posições representativas; região/UF podem exibir localidades com cache municipal. Símbolos usam prevenção de sobreposição e agrupamento. O popover informa período, variáveis, incidentes do recorte (sem inventar raio de proximidade), fonte e precisão. Sem dado ou sem atingir o limiar, não há símbolo. A timeline reutiliza a série local.

`GEO_WEATHER_THRESHOLDS` permite configurar os limiares visuais: chuva ≥ 1 mm, chuva intensa amostrada ≥ 20 mm/dia, máxima ≥ 35 °C, rajada ≥ 40 km/h e mínimas regionais N=18, NE=16, CO=12, SE=10 e S=5 °C. Não são alertas oficiais nem anomalias climatológicas. Direção e camadas só utilizam observações válidas; opções sem variável disponível ficam desabilitadas.

Cliente restringe projetos canônicos; projeto restringe ISPs; região restringe UF. Combinações incompatíveis são recusadas no backend e removidas com orientação na interface. Filtros OTRS ficam suspensos no inventário Base/Projetos e preferências operacionais são preservadas ao mudar de modo. Último mês completo é o padrão; este mês, mês passado, últimos 90 dias e personalizado reutilizam os contratos existentes. Intervalos históricos de até 366 dias combinam séries mensais por data e mantêm TicketIDs distintos, sem transformar clima ausente em zero ou aceitar datas futuras.

Projetos utiliza o maior número de links CMDB por projeto canônico em cada UF, com desempate determinístico, participação no tooltip e legenda correspondente. Bradesco utiliza vermelho e Banco do Brasil amarelo; demais projetos possuem paleta estável. `GEO_PROJECT_COLORS` aceita overrides por nome canônico e cor hexadecimal. Um projeto selecionado utiliza intensidades da própria cor. A paleta de clientes permanece independente.

## Persistência e homologação

Migration aditiva **`integracoes.0018_weathercollectionjob_progress_and_more`**: progresso e estados de controle na fila existente, mais cache/fila local compartilhado. Não altera espelhos ou motivos oficiais. Aplicar via procedimento autorizado; rollback mantém tabela, progresso e caches aditivos. Não executar migrations reversas destrutivas.

Estado real em 13/11/2026: **27/27 UFs**, 373 células territoriais, 31 dias e 100% da área da quadratura/completude em outubro/2026. As dez pendências foram concluídas em 13/11/2026 às 09:15:03 (America/Sao_Paulo), com 19 tentativas HTTP para 130 localizações, sem elevar o orçamento existente de 250. Estimativa conservadora: 287,86 unidades do fornecedor; não é cobrança/saldo informado pela API. Os cinco checkpoints de PI e os hashes das 17 UFs anteriores foram preservados. O bloqueio anterior foi o orçamento interno antes do HTTP, sem evento persistido de 429. Worker GEO observado em vários ciclos com heartbeat ativo e encerrado graciosamente; estado final parado. SQLite DEV, não MySQL. São Paulo/SP, IBGE 3550308, conserva o cache municipal diário/horário real (31 dias e 792 horas com margens); nenhum outro município foi declarado coletado.

O Worker executou somente GEO e encerrou; o serviço contínuo não foi instalado. Persistência real em SQLite DEV. MySQL DEV `arion_geo_dev` não está configurado e continua pendente. Produção e OTRS remoto não foram usados. Fonte comercial/self-hosted real continua dependente de ambiente/credenciais, caso utilizada. Ver [candidata 1.4.1](../08-releases/v1.4.1-candidata.md).
