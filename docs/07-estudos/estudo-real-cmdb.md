# Estudo real do CMDB ServiceUp

## Resumo executivo

Status: **Completo**. Links encontrados/processados: 3425/3425. Operadoras encontradas/processadas: 1327/1327. Duração: 70.7 s; chamadas/retries de client contabilizados: 108.

## Ambiente e método

Execução somente leitura via `CMDBClient` existente, com configuração e credenciais DPAPI atuais descriptografadas apenas em memória. Nenhum payload ou valor sensível foi persistido. A Base XLSX/Arion foi consultada somente para comparação (3418 registros); não foi alterada.

## Limitações da API

Não foi assumido offset/cursor. Paginação: não documentada/não encontrada. Resultado completo exige Search sem limite ou limites que não aparentem truncamento; lotes com falha deixam o estudo parcial. Definições e valores representam apenas as versões retornadas pela API durante esta execução.

## Comportamento do ConfigItemSearch

| Limite | IDs retornados | Duração ms | Erro |
|---:|---:|---:|---|
| 10 | 10 | 921 | — |
| 100 | 100 | 574 | — |
| 500 | 500 | 838 | — |
| 1000 | 1000 | 834 | — |
| 5000 | 3425 | 923 | — |
| sem limite | 3425 | 950 | — |
| Operadoras · 10 | 10 | 385 | — |
| Operadoras · 100 | 100 | 425 | — |
| Operadoras · 500 | 500 | 367 | — |
| Operadoras · 1000 | 1000 | 399 | — |
| Operadoras · 5000 | 1327 | 431 | — |
| Operadoras · sem limite | 1327 | 377 | — |


Search utilizou `Class` e `Limit`; não foi confirmada paginação.

## Comportamento do ConfigItemGet

Links: 3425/3425 itens processados em 69 lotes, com 0 falhas. Operadoras: 1327/1327 itens em 27 lotes, com 0 falhas. `ConfigItemGet` usou lote de 50 IDs.

## Links de internet

Encontrados: 3425; processados: 3425; falhas: 0.

### Campos encontrados e taxa de preenchimento

| Campo | Existe | Preenchido | Vazio | % | Tipos | Exemplo seguro |
|---|---:|---:|---:|---:|---|---|
| Class | 3425 | 3425 | 0 | 100.0% | string | — |
| ConfigItemID | 3425 | 3425 | 0 | 100.0% | string | — |
| CreateBy | 3425 | 3425 | 0 | 100.0% | number | — |
| CreateTime | 3425 | 3425 | 0 | 100.0% | string | — |
| DefinitionID | 3425 | 3425 | 0 | 100.0% | number | — |
| DeplState | 3425 | 3425 | 0 | 100.0% | string | Cancelado, Em Manutenção, Operacional |
| DeplStateType | 3425 | 3425 | 0 | 100.0% | string | productive |
| InciState | 3425 | 3425 | 0 | 100.0% | string | Incident, Operational |
| InciStateType | 3425 | 3425 | 0 | 100.0% | string | incident, operational |
| Name | 3425 | 3425 | 0 | 100.0% | string | — |
| Number | 3425 | 3425 | 0 | 100.0% | string | — |
| VersionID | 3425 | 3425 | 0 | 100.0% | string | — |
| CurDeplState | 3425 | 3423 | 2 | 99.9% | string | Cancelado, Em Manutenção, Operacional |
| CurDeplStateType | 3425 | 3423 | 2 | 99.9% | string | productive |
| CurInciState | 3425 | 3423 | 2 | 99.9% | string | Incident, Operational |
| CurInciStateType | 3425 | 3423 | 2 | 99.9% | string | incident, operational |
| LastVersionID | 3425 | 3423 | 2 | 99.9% | number | — |
| Circuito | 3422 | 3422 | 0 | 100.0% | string | — |
| Designacao | 3422 | 3422 | 0 | 100.0% | string | — |
| Estado | 3422 | 3422 | 0 | 100.0% | string | AM, PA, PE |
| ISP | 3422 | 3422 | 0 | 100.0% | string | — |
| Tipo | 3422 | 3422 | 0 | 100.0% | string | BANDA LARGA, Banda Larga, DEDICADO |
| Endereco | 3422 | 3420 | 2 | 99.9% | string | — |
| RangeIP | 3422 | 3418 | 4 | 99.9% | string | — |
| Cidade | 3422 | 3415 | 7 | 99.8% | string | — |
| Equipamento | 2367 | 2367 | 0 | 100.0% | string | — |
| MACAddress | 2367 | 2367 | 0 | 100.0% | string | — |
| Velocidade | 3395 | 1828 | 1567 | 53.8% | string | — |
| NumeroContrato | 3066 | 798 | 2268 | 26.0% | string | — |
| Identificacao | 3066 | 627 | 2439 | 20.5% | string | — |
| DesignacaoOperadora | 3066 | 613 | 2453 | 20.0% | string | — |
| CNPJ | 3066 | 582 | 2484 | 19.0% | string | — |
| HorarioFuncionamento | 3066 | 577 | 2489 | 18.8% | string | — |
| ObservacaoISP | 3066 | 569 | 2497 | 18.6% | string | — |
| UsuarioPPOE | 3066 | 63 | 3003 | 2.1% | string | — |
| SenhaPPOE | 3066 | 62 | 3004 | 2.0% | string | [PROTEGIDO] |
| Contato | 302 | 14 | 288 | 4.6% | string | — |
| CustomerID | 3409 | 12 | 3397 | 0.4% | string | — |
| Contato2 | 302 | 6 | 296 | 2.0% | string | — |
| Contato3 | 302 | 1 | 301 | 0.3% | string | — |
| CnpjOperadora | 3389 | 0 | 3389 | 0.0% | — | — |

### Campos por DefinitionID

- DefinitionID 10: 35 itens; campos: Cidade, Circuito, Class, ConfigItemID, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, Endereco, Estado, ISP, InciState, InciStateType, LastVersionID, Name, Number, RangeIP, Tipo, Velocidade, VersionID
- DefinitionID 15: 21 itens; campos: Cidade, Circuito, Class, CnpjOperadora, ConfigItemID, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, Endereco, Estado, ISP, InciState, InciStateType, LastVersionID, Name, Number, RangeIP, Tipo, Velocidade, VersionID
- DefinitionID 19: 302 itens; campos: Cidade, Circuito, Class, CnpjOperadora, ConfigItemID, Contato, Contato2, Contato3, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, Endereco, Estado, ISP, InciState, InciStateType, LastVersionID, Name, Number, RangeIP, Tipo, Velocidade, VersionID
- DefinitionID 22: 1 itens; campos: CNPJ, Cidade, Circuito, Class, CnpjOperadora, ConfigItemID, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, DesignacaoOperadora, Endereco, Estado, HorarioFuncionamento, ISP, Identificacao, InciState, InciStateType, LastVersionID, Name, Number, NumeroContrato, ObservacaoISP, RangeIP, SenhaPPOE, Tipo, UsuarioPPOE, Velocidade, VersionID
- DefinitionID 28: 698 itens; campos: CNPJ, Cidade, Circuito, Class, CnpjOperadora, ConfigItemID, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, DesignacaoOperadora, Endereco, Estado, HorarioFuncionamento, ISP, Identificacao, InciState, InciStateType, LastVersionID, Name, Number, NumeroContrato, ObservacaoISP, RangeIP, SenhaPPOE, Tipo, UsuarioPPOE, Velocidade, VersionID
- DefinitionID 33: 2368 itens; campos: CNPJ, Cidade, Circuito, Class, CnpjOperadora, ConfigItemID, CreateBy, CreateTime, CurDeplState, CurDeplStateType, CurInciState, CurInciStateType, CustomerID, DefinitionID, DeplState, DeplStateType, Designacao, DesignacaoOperadora, Endereco, Equipamento, Estado, HorarioFuncionamento, ISP, Identificacao, InciState, InciStateType, LastVersionID, MACAddress, Name, Number, NumeroContrato, ObservacaoISP, RangeIP, SenhaPPOE, Tipo, UsuarioPPOE, Velocidade, VersionID

### Estados



- CurDeplState: Operacional (3174), Cancelado (248), Em Manutenção (1)
- CurDeplStateType: productive (3423)
- CurInciState: Operational (3305), Incident (118)
- CurInciStateType: operational (3305), incident (118)
- DeplState: Operacional (3176), Cancelado (248), Em Manutenção (1)
- DeplStateType: productive (3425)
- InciState: Operational (3307), Incident (118)
- InciStateType: operational (3307), incident (118)

### Versionamento

VersionID distintos: 3425; LastVersionID distintos: 3423. Iguais: 3423; diferentes: 0; ausente em um dos campos: 2.

## Operadoras

Encontradas: 1327; processadas: 1327; falhas: 0.

### Campos encontrados e taxa de preenchimento

| Campo | Existe | Preenchido | Vazio | % | Tipos | Exemplo seguro |
|---|---:|---:|---:|---:|---|---|
| Class | 1327 | 1327 | 0 | 100.0% | string | — |
| ConfigItemID | 1327 | 1327 | 0 | 100.0% | string | — |
| CreateBy | 1327 | 1327 | 0 | 100.0% | number | — |
| CreateTime | 1327 | 1327 | 0 | 100.0% | string | — |
| CurDeplState | 1327 | 1327 | 0 | 100.0% | string | Operacional |
| CurDeplStateType | 1327 | 1327 | 0 | 100.0% | string | productive |
| CurInciState | 1327 | 1327 | 0 | 100.0% | string | Incident, Operational |
| CurInciStateType | 1327 | 1327 | 0 | 100.0% | string | incident, operational |
| DefinitionID | 1327 | 1327 | 0 | 100.0% | number | — |
| DeplState | 1327 | 1327 | 0 | 100.0% | string | Operacional |
| DeplStateType | 1327 | 1327 | 0 | 100.0% | string | productive |
| EmailAbertura | 1327 | 1327 | 0 | 100.0% | string | — |
| HorarioOperadora | 1327 | 1327 | 0 | 100.0% | string | — |
| InciState | 1327 | 1327 | 0 | 100.0% | string | Incident, Operational |
| InciStateType | 1327 | 1327 | 0 | 100.0% | string | incident, operational |
| LastVersionID | 1327 | 1327 | 0 | 100.0% | number | — |
| Name | 1327 | 1327 | 0 | 100.0% | string | — |
| Number | 1327 | 1327 | 0 | 100.0% | string | — |
| TelefoneOperadora | 1327 | 1327 | 0 | 100.0% | string | — |
| VersionID | 1327 | 1327 | 0 | 100.0% | string | — |
| CnpjOperadora | 1327 | 1181 | 146 | 89.0% | string | — |
| OperadoraWhatsApp | 1327 | 1120 | 207 | 84.4% | string | — |
| 1EscalonamentoOperadora | 1327 | 104 | 1223 | 7.8% | string | — |
| PortalOperadora | 1327 | 46 | 1281 | 3.5% | string | — |
| EmailAcompanhamento | 1327 | 36 | 1291 | 2.7% | string | — |
| Observacao | 1100 | 33 | 1067 | 3.0% | string | — |
| 2EscalonamentoOperadora | 1327 | 32 | 1295 | 2.4% | string | — |
| 3EscalonamentoOperadora | 1327 | 19 | 1308 | 1.4% | string | — |
| 4EscalonamentoOperadora | 1327 | 15 | 1312 | 1.1% | string | — |
| UsuarioPortalOperadora | 1327 | 9 | 1318 | 0.7% | string | — |
| SenhaPortalOperadora | 1327 | 8 | 1319 | 0.6% | string | [PROTEGIDO] |
| 5EscalonamentoOperadora | 1327 | 6 | 1321 | 0.5% | string | — |
| CategoriaOperadora | 1327 | 4 | 1323 | 0.3% | string | — |


### Campos sensiveis

Os campos sensiveis aparecem somente por presenca e quantidade preenchida, com conteudo `[PROTEGIDO]` e classificacao `PROTEGIDO - NAO PERSISTIR`. Exemplos nao sao divulgados para UsuarioPPOE, contatos, CNPJ ou MAC.

## Relação Link → Operadora

ISP resolvido por ConfigItemID: 3306; por Number: 0; por Name: 9; por outro campo: 0; não resolvido: 107; ambíguo: 0; ISP vazio: 3.

Conclusão observada para ISP: **mixed**. A resolução usa correspondência exata de strings sem conversão numérica, preservando zeros.

| Link ConfigItemID | Link Number | Link Name | ISP | Operadora ConfigItemID | Operadora Number | Operadora Name | Método |
|---|---|---|---|---|---|---|---|
| 4633 | 36131000003 | BLADEX REPRESENTACAO LIMITADA | 6804 | 6804 | 36132000029 | CLARO | ConfigItemID |
| 4635 | 36131000005 | DUFRY - AERO - BELÉM - BLM/IP/10682 | 6804 | 6804 | 36132000029 | CLARO | ConfigItemID |
| 4637 | 36131000007 | MODERN - AEROP. DE MANAUS - AM | 6804 | 6804 | 36132000029 | CLARO | ConfigItemID |
| 4638 | 36131000008 | UFINET - BD50000067605 - GOL LOG - AEROPORTO DE BELEM | 6804 | 6804 | 36132000029 | CLARO | ConfigItemID |
| 4639 | 36131000009 | WISH MATRIZ - SPO/IP/71769 - CLARO | 6804 | 6804 | 36132000029 | CLARO | ConfigItemID |


## ConfigItemID vs Number
ConfigItemID presente/unico: 3425/3425; Number presente/unico: 3425/3425; valores iguais entre si: 0.

`ConfigItemID` foi tratado como identificador técnico da API e `Number` comparado literalmente com o campo XLSX/Arion `ConfigItem#` (`CMDBLink.config_item_id`). Correspondências exatas: 3418; somente CMDB: 7; somente Base: 0; duplicados Number: 0; duplicados na Base: 0. Resultado: **Parcialmente**.

## Comparação com Base atual

3418 Numbers coincidem exatamente com `ConfigItem#` em 3418 linhas da Base consultada. Não houve normalização numérica. Comparações por campo: {"Cidade": {"compared": 3415, "different": 7, "equal": 3408}, "Circuito": {"compared": 3415, "different": 4, "equal": 3411}, "CurDeplState": {"compared": 3418, "different": 4, "equal": 3414}, "CurInciState": {"compared": 3418, "different": 65, "equal": 3353}, "Designacao": {"compared": 3415, "different": 1, "equal": 3414}, "DesignacaoOperadora": {"compared": 3059, "different": 1, "equal": 3058}, "Estado": {"compared": 3415, "equal": 3415}, "ISP": {"compared": 3415, "different": 3299, "equal": 116}, "Name": {"compared": 3418, "equal": 3418}, "Number": {"compared": 3418, "equal": 3418}, "RangeIP": {"compared": 3415, "different": 31, "equal": 3384}, "Tipo": {"compared": 3415, "equal": 3415}, "Velocidade": {"compared": 3388, "different": 2, "equal": 3386}}.

## Designação

Campos avaliados: Designacao, DesignacaoOperadora, Circuito, Identificacao e Name. Igualdades com `designation_arion`: Designacao 3414/3415; DesignacaoOperadora 3058/3059; Circuito 3411/3415; Name 3418/3418. Campo com maior igualdade observada: **Name**. Tratar como forte evidencia de equivalencia de valor; validar a semantica antes de adota-lo como regra oficial.

## Projeto / Cliente

Campos explícitos com nome Projeto/Project: não encontrados. `CustomerID` foi inventariado como possível campo candidato, sem evidência suficiente para tratá-lo como Projeto. Resultado: **fonte oficial de Projeto não confirmada pela API estudada**.

## Possíveis associações com Ticket

Resultado direto: **Nao encontrado**. A busca considerou nomes contendo Ticket, Link, Relation, Associated e estruturas equivalentes.

Chaves/estruturas com indícios de Ticket/associação encontrados: nenhum campo ou estrutura correspondente foi exposto em Search/Get.

## Qualidade de dados

- ConfigItemID duplicados: 0.
- Number duplicados no CMDB/Base: 0/0.
- Name vazio: 0; ISP vazio/nao resolvido: 3/107.
- Preenchimentos parciais e valores atipicos estao detalhados no inventario e nao sao classificados automaticamente como erro.

## Divergências

- Status do estudo: Completo para o inventário recuperado.
- Number duplicado no CMDB/Base: 0/0.
- ISP sem operadora: 107; ISP ambíguo: 0.
- Falhas de Get: Links 0; Operadoras 0.

## Matriz de mapeamento e recomendacoes para o modelo futuro

| Campo CMDB | Campo Arion atual | Decisão futura |
|---|---|---|
| ConfigItemID | config_item_id | CONFIRMAR como chave técnica, comparar com base |
| Number | ConfigItem# / config_item_id | Candidato; evidência quantitativa acima |
| Name | name | Candidato |
| Designacao / DesignacaoOperadora / Circuito / Identificacao | designation_arion / operator_designation / circuit | Investigar comparações acima |
| ISP | Provider/Operadora | Candidato; usar resolução medida |
| Cidade / Estado / Tipo / Velocidade / RangeIP | city / uf_raw / link_type / speed_raw / range_ip | Candidatos |
| CurDeplState / CurInciState | deployment_state / incident_state | Candidatos |
| Senhas, tokens, secrets, sessões | — | PROTEGIDO — NÃO PERSISTIR |

## Pendências

- Search não documenta offset/cursor; validar truncamento junto ao administrador do GenericInterface se o resultado atingir o teto observado.
- Campos ausentes ou com valor `Operational` em Designacao são reportados sem classificação automática de erro.
- Nenhuma alteração, sync ou escrita foi executada no CMDB ou Base.

## Próxima fase: OTRS / NOC - ATACADO

Investigar MySQL read-only: **Queue → Tickets → associação oficial ao ConfigItem**, começando pela fila `NOC - ATACADO`. Levar `ConfigItemID`, `Number`/`ConfigItem#`, `Name`, `Designacao`, `DesignacaoOperadora`, `Circuito` e identificadores de operadora descobertos. Não usar `ticket.title LIKE` como relação definitiva.

## Guia para investigacao OTRS

Procurar as estruturas oficiais de associacao pelos identificadores `ConfigItemID`, `Number`/`ConfigItem#`, `Designacao` e `Name`, partindo de `Queue` -> tickets da fila `NOC - ATACADO` -> vinculo ConfigItem. O titulo pode apoiar a investigacao, mas nao confirma a relacao.




Verificação local: Base antes=3418; depois=3418; quantidade inalterada=sim.
