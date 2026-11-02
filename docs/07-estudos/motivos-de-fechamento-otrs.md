# Estudo de motivos de fechamento do OTRS

Levantamento read-only do mirror local da fila `NOC - ATACADO`, preservando `closing_code_raw`. Meses agrupados por `source_created_at` em `America/Sao_Paulo`. A tabela informa total, primeira/ultima ocorrencia e contagem mensal de nov/2025 a out/2026.

## Origem e catalogo

- Ha um campo ativo de motivo em `dynamic_field`: ID 101, `CodigoFechamento`, label do motivo, tipo `Dropdown`, objeto `Ticket`, valido. Criado em 2024-05-20 e alterado em 2026-08-07. O catalogo atual tem 76 opcoes em `dynamic_field.config.PossibleValues`; valores antigos e novas chaves estao em `dynamic_field_value.value_text` sob o mesmo `field_id=101`.
- Nao foi encontrado novo Dynamic Field de motivo nem tabela `dynamic_field_config`; as opcoes ficam no `config` do proprio Dynamic Field.
- ID 102 (`DetalheCodFechamento`) e texto livre e nao e fonte do codigo. IDs 121 e 132 sao campos de senha; seus valores nao foram consultados. O inventario usa exclusivamente o campo 101.
- Causa: o catalogo do mesmo Dynamic Field mudou em lugar; valores antigos permaneceram no historico enquanto chaves tecnicas novas passaram a ser gravadas. A transicao visivel ocorre em agosto/2026. O mirror nao reescreve o historico.

## Cobertura

- Tickets presentes na fila: **11,973**; raws distintos: **54**; categorias canonicas: **44**; raws consolidados alem do primeiro por categoria: **10**.
- O raw permanece intacto; valores sem regra explicita continuam separados.
- Comparativo desta revisão: **54 raws** antes e depois; **48 categorias canônicas** antes e **44 depois**; **10 raws excedentes** consolidados em grupos canônicos (antes eram 6). Raw permanece intacto.

## Inventario raw completo

| Valor raw (exato) | Canonical key | Display | Total | Primeira ocorrencia | Ultima ocorrencia | 2025-11 | 2025-12 | 2026-01 | 2026-02 | 2026-03 | 2026-04 | 2026-05 | 2026-06 | 2026-07 | 2026-08 | 2026-09 | 2026-10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AltaTempSalaTecnica | alta_temperatura_sala_tecnica | Alta Temperatura da Sala Técnica | 1 | 2026-08-11 12:10 | 2026-08-11 12:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| AtaqueCibernetico | ataque_cibernetico | Ataque Cibernético | 1 | 2026-08-25 11:30 | 2026-08-25 11:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| AttDeConfiguracao | atualizacao_configuracao | Atualização de Configuração | 29 | 2026-07-31 16:50 | 2026-10-23 10:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 5 | 13 | 10 |
| Atualização de configuração | atualizacao_configuracao | Atualização de Configuração | 864 | 2024-04-26 10:47 | 2026-07-31 10:35 | 44 | 25 | 24 | 17 | 25 | 24 | 66 | 14 | 17 | 0 | 0 | 0 |
| CaboDanificado | cabo_danificado | Cabo Danificado | 10 | 2026-08-07 08:50 | 2026-10-04 21:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 4 | 1 |
| CaboDesconectado | cabo_desconectado | Cabo Desconectado | 16 | 2026-08-12 19:50 | 2026-10-22 09:56 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 7 | 7 |
| Cancelado | chamado_cancelado | Chamado Cancelado | 441 | 2024-04-18 14:28 | 2026-08-03 15:20 | 41 | 30 | 32 | 28 | 21 | 35 | 13 | 17 | 27 | 2 | 0 | 0 |
| CancelamentoServico | cancelamento_servico | Cancelamento de Serviço | 5 | 2026-08-05 13:20 | 2026-10-09 12:45 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 2 | 2 |
| ChamadoCancelado | chamado_cancelado | Chamado Cancelado | 146 | 2026-08-05 09:10 | 2026-10-23 14:40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 55 | 70 |
| Conexão Incorreta - Cabeamento/porta | conexao_incorreta_cabo_interface | Conexão Incorreta - Cabo/Interface | 101 | 2024-08-15 14:12 | 2026-07-29 10:06 | 4 | 4 | 7 | 5 | 2 | 12 | 7 | 8 | 10 | 0 | 0 | 0 |
| ConexaoIncorreta | conexao_incorreta_cabo_interface | Conexão Incorreta - Cabo/Interface | 2 | 2026-08-25 15:00 | 2026-09-01 09:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 |
| ConfiguracaoRede | configuracao_rede | Configuração de Rede | 9 | 2026-08-04 09:10 | 2026-09-11 18:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 2 | 0 |
| ConfiguracaoVlan | configuracao_vlan | Configuração de VLAN | 3 | 2026-08-18 08:20 | 2026-09-14 09:40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 1 | 0 |
| Defeito de infra | Defeito de infra | Defeito de infra | 260 | 2024-11-18 13:50 | 2026-07-31 12:50 | 6 | 15 | 4 | 6 | 12 | 10 | 33 | 14 | 14 | 0 | 0 | 0 |
| Defeito de Infra - Bradesco | Defeito de Infra - Bradesco | Defeito de Infra - Bradesco | 5 | 2024-05-21 11:20 | 2024-08-16 20:50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Defeito de Infra - Vivo | Defeito de Infra - Vivo | Defeito de Infra - Vivo | 5 | 2024-06-21 13:30 | 2024-08-16 11:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Defeito Massivo | falha_massiva | Falha Massiva | 493 | 2024-05-20 13:30 | 2026-08-03 11:00 | 4 | 5 | 8 | 20 | 21 | 31 | 55 | 47 | 63 | 5 | 0 | 0 |
| DefeitoPontoRede | defeito_ponto_rede | Defeito no Ponto de Rede | 4 | 2026-08-19 08:30 | 2026-09-02 18:40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1 | 0 |
| Duplicidade | Duplicidade | Duplicidade | 320 | 2024-05-08 07:28 | 2026-10-22 15:50 | 4 | 2 | 3 | 13 | 10 | 8 | 33 | 14 | 20 | 14 | 20 | 15 |
| Equipamento Substituido | equipamento_substituido | Equipamento Substituído | 187 | 2024-05-17 14:50 | 2026-07-29 21:00 | 7 | 12 | 9 | 9 | 5 | 6 | 4 | 8 | 14 | 0 | 0 | 0 |
| Equipamento travado/reinicilizado | equipamento_travado | Equipamento Travado | 1266 | 2024-05-23 16:50 | 2026-08-03 12:50 | 52 | 58 | 77 | 61 | 73 | 139 | 82 | 55 | 73 | 6 | 0 | 0 |
| EquipamentoDesligado | equipamento_desligado | Equipamento Desligado | 12 | 2026-09-01 18:50 | 2026-10-23 07:00 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 3 |
| EquipamentoSubstituido | equipamento_substituido | Equipamento Substituído | 30 | 2026-08-01 04:10 | 2026-10-21 09:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 6 | 11 |
| EquipamentoTravado | equipamento_travado | Equipamento Travado | 232 | 2026-08-04 15:40 | 2026-10-23 16:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 83 | 81 | 68 |
| ErroConfigIP | erro_configuracao_ip | Erro de Configuração de IP | 2 | 2026-08-13 11:00 | 2026-08-19 12:15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| ErroMonitoramento | erro_monitoramento | Erro de Monitoramento | 3 | 2026-09-02 16:10 | 2026-09-08 23:41 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| Falha elétrica | falha_eletrica | Falha Elétrica | 129 | 2024-10-03 23:46 | 2026-07-23 14:40 | 5 | 6 | 6 | 6 | 6 | 19 | 7 | 4 | 15 | 0 | 0 | 0 |
| FalhaEletrica | falha_eletrica | Falha Elétrica | 53 | 2026-08-09 10:22 | 2026-10-23 08:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 19 | 29 |
| FalhaInfraAeroporto | falha_infra_aeroporto | Falha na Infraestrutura do Aeroporto | 1 | 2026-09-03 09:20 | 2026-09-03 09:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| FalhaInfraCliente | falha_infra_cliente | Falha na Infraestrutura do Cliente | 37 | 2026-07-31 10:06 | 2026-10-23 13:40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 7 | 18 | 11 |
| FalhaLogica | falha_logica_operadora | Falha Lógica na Operadora | 5 | 2026-08-24 10:36 | 2026-10-20 08:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 3 |
| FalhaMassiva | falha_massiva | Falha Massiva | 203 | 2026-08-04 10:40 | 2026-10-23 15:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 85 | 77 | 41 |
| FalhaMonitoramento | falha_monitoramento | Falha no Monitoramento | 1 | 2026-08-13 10:50 | 2026-08-13 10:50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| FalhaOperadora | falha_operadora | Falha na Operadora | 2 | 2026-08-28 19:50 | 2026-09-03 07:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 |
| FalhaPPPOE | falha_pppoe | Falha no PPPOE | 8 | 2026-08-17 16:20 | 2026-10-16 13:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 5 | 2 |
| Falta Energia no modem/router | Falta Energia no modem/router | Falta Energia no modem/router | 4 | 2024-05-21 17:30 | 2024-09-04 10:57 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| FonteQueimada | fonte_queimada | Fonte Queimada | 4 | 2026-08-28 18:10 | 2026-09-30 09:50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 3 | 0 |
| Migração | Migração | Migração | 69 | 2024-10-24 07:40 | 2026-06-17 12:10 | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| Modem Desconectado | Modem Desconectado | Modem Desconectado | 38 | 2024-05-24 15:20 | 2026-07-25 10:00 | 2 | 0 | 3 | 0 | 2 | 2 | 7 | 0 | 1 | 0 | 0 | 0 |
| OscilacaoRede | oscilacao_rede | Oscilação na Rede | 10 | 2026-08-13 15:10 | 2026-10-22 18:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 8 |
| Outros | outros_especificar | Outros (Especificar) | 72 | 2026-07-29 15:30 | 2026-10-22 18:20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 38 | 15 | 18 |
| Pendência Financeira | pendencia_financeira | Pendência Financeira | 448 | 2024-06-25 09:10 | 2026-08-03 07:16 | 14 | 35 | 25 | 25 | 8 | 13 | 27 | 28 | 11 | 1 | 0 | 0 |
| PendenciaFinanceira | pendencia_financeira | Pendência Financeira | 122 | 2026-08-05 13:40 | 2026-10-23 16:10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 46 | 55 |
| PreventivaFalha | preventiva_falha | Preventiva com Falha | 1 | 2026-08-14 00:58 | 2026-08-14 00:58 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| Rompimento/manut Fibra | rompimento_fibra | Rompimento de Fibra | 650 | 2024-05-20 12:20 | 2026-08-03 15:20 | 38 | 40 | 47 | 53 | 56 | 34 | 36 | 40 | 70 | 4 | 0 | 0 |
| RompimentoFibra | rompimento_fibra | Rompimento de Fibra | 232 | 2026-07-28 15:20 | 2026-10-23 09:13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 81 | 76 | 74 |
| Sem acesso por 120 minutos (informativo) | Sem acesso por 120 minutos (informativo) | Sem acesso por 120 minutos (informativo) | 23 | 2024-11-13 13:55 | 2026-06-05 09:50 | 1 | 7 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| Sem Intervenção Técnica | sem_intervencao_tecnica | Sem Intervenção Técnica | 2363 | 2024-05-22 08:20 | 2026-08-03 21:50 | 95 | 128 | 129 | 129 | 156 | 124 | 97 | 105 | 80 | 3 | 0 | 0 |
| SemIntervencaoTecnica | sem_intervencao_tecnica | Sem Intervenção Técnica | 391 | 2026-08-03 12:30 | 2026-10-23 10:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 79 | 154 | 158 |
| SinalFraco | sinal_fraco | Sinal Fraco | 3 | 2026-08-27 12:20 | 2026-10-09 11:00 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 1 |
| TunnelMTU | tunnel_mtu | Tunnel / MTU | 47 | 2026-08-06 12:30 | 2026-10-21 16:50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 | 20 | 13 |
| Vandalismo | vandalismo | Vandalismo | 1 | 2026-10-09 09:50 | 2026-10-09 09:50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| Vlan | vlan | Troca de VLAN | 1 | 2026-10-15 13:30 | 2026-10-15 13:30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| zOutros | outros_legado | Outros (catálogo legado) | 945 | 2024-05-07 13:40 | 2026-08-03 11:40 | 9 | 27 | 55 | 60 | 84 | 42 | 49 | 62 | 71 | 6 | 0 | 0 |

## Aliases efetivamente observados e consolidados

| Raws observados | Canonical key | Label canônico |
|---|---|---|
| `Defeito Massivo`, `FalhaMassiva` | `falha_massiva` | Falha Massiva |
| `Pendência Financeira`, `PendenciaFinanceira` | `pendencia_financeira` | Pendência Financeira |
| `Equipamento Substituido`, `EquipamentoSubstituido` | `equipamento_substituido` | Equipamento Substituído |
| `Falha elétrica`, `FalhaEletrica` | `falha_eletrica` | Falha Elétrica |
| `Sem Intervenção Técnica`, `SemIntervencaoTecnica` | `sem_intervencao_tecnica` | Sem Intervenção Técnica |
| `Atualização de configuração`, `AttDeConfiguracao` | `atualizacao_configuracao` | Atualização de Configuração |
| `Equipamento travado/reinicilizado`, `EquipamentoTravado` | `equipamento_travado` | Equipamento Travado |
| `Rompimento/manut Fibra`, `RompimentoFibra` | `rompimento_fibra` | Rompimento de Fibra |
| `Cancelado`, `ChamadoCancelado` | `chamado_cancelado` | Chamado Cancelado |
| `Conexão Incorreta - Cabeamento/porta`, `ConexaoIncorreta` | `conexao_incorreta_cabo_interface` | Conexão Incorreta - Cabo/Interface |

Os raws de rótulo humano do catálogo atual (por exemplo `Falha Massiva`, `Equipamento Travado` e `Rompimento de Fibra`) também são aceitos; alguns não aparecem literalmente no snapshot porque o OTRS passou a gravar chaves técnicas.

## Auditoria do catálogo canônico

| Canonical key | Display label | Raws observados | Total | Primeira ocorrência | Última ocorrência |
|---|---|---|---:|---|---|
| `alta_temperatura_sala_tecnica` | Alta Temperatura da Sala Técnica | AltaTempSalaTecnica (1) | 1 | 2026-08-11 12:10 | 2026-08-11 12:10 |
| `ataque_cibernetico` | Ataque Cibernético | AtaqueCibernetico (1) | 1 | 2026-08-25 11:30 | 2026-08-25 11:30 |
| `atualizacao_configuracao` | Atualização de Configuração | AttDeConfiguracao (29)<br>Atualização de configuração (864) | 893 | 2024-04-26 10:47 | 2026-10-23 10:10 |
| `cabo_danificado` | Cabo Danificado | CaboDanificado (10) | 10 | 2026-08-07 08:50 | 2026-10-04 21:10 |
| `cabo_desconectado` | Cabo Desconectado | CaboDesconectado (16) | 16 | 2026-08-12 19:50 | 2026-10-22 09:56 |
| `cancelamento_servico` | Cancelamento de Serviço | CancelamentoServico (5) | 5 | 2026-08-05 13:20 | 2026-10-09 12:45 |
| `chamado_cancelado` | Chamado Cancelado | Cancelado (441)<br>ChamadoCancelado (146) | 587 | 2024-04-18 14:28 | 2026-10-23 14:40 |
| `conexao_incorreta_cabo_interface` | Conexão Incorreta - Cabo/Interface | ConexaoIncorreta (2)<br>Conexão Incorreta - Cabeamento/porta (101) | 103 | 2024-08-15 14:12 | 2026-09-01 09:10 |
| `configuracao_rede` | Configuração de Rede | ConfiguracaoRede (9) | 9 | 2026-08-04 09:10 | 2026-09-11 18:30 |
| `configuracao_vlan` | Configuração de VLAN | ConfiguracaoVlan (3) | 3 | 2026-08-18 08:20 | 2026-09-14 09:40 |
| `Defeito de infra` | Defeito de infra | Defeito de infra (260) | 260 | 2024-11-18 13:50 | 2026-07-31 12:50 |
| `Defeito de Infra - Bradesco` | Defeito de Infra - Bradesco | Defeito de Infra - Bradesco (5) | 5 | 2024-05-21 11:20 | 2024-08-16 20:50 |
| `Defeito de Infra - Vivo` | Defeito de Infra - Vivo | Defeito de Infra - Vivo (5) | 5 | 2024-06-21 13:30 | 2024-08-16 11:30 |
| `defeito_ponto_rede` | Defeito no Ponto de Rede | DefeitoPontoRede (4) | 4 | 2026-08-19 08:30 | 2026-09-02 18:40 |
| `Duplicidade` | Duplicidade | Duplicidade (320) | 320 | 2024-05-08 07:28 | 2026-10-22 15:50 |
| `equipamento_desligado` | Equipamento Desligado | EquipamentoDesligado (12) | 12 | 2026-09-01 18:50 | 2026-10-23 07:00 |
| `equipamento_substituido` | Equipamento Substituído | Equipamento Substituido (187)<br>EquipamentoSubstituido (30) | 217 | 2024-05-17 14:50 | 2026-10-21 09:10 |
| `equipamento_travado` | Equipamento Travado | Equipamento travado/reinicilizado (1266)<br>EquipamentoTravado (232) | 1498 | 2024-05-23 16:50 | 2026-10-23 16:10 |
| `erro_configuracao_ip` | Erro de Configuração de IP | ErroConfigIP (2) | 2 | 2026-08-13 11:00 | 2026-08-19 12:15 |
| `erro_monitoramento` | Erro de Monitoramento | ErroMonitoramento (3) | 3 | 2026-09-02 16:10 | 2026-09-08 23:41 |
| `falha_eletrica` | Falha Elétrica | Falha elétrica (129)<br>FalhaEletrica (53) | 182 | 2024-10-03 23:46 | 2026-10-23 08:20 |
| `falha_logica_operadora` | Falha Lógica na Operadora | FalhaLogica (5) | 5 | 2026-08-24 10:36 | 2026-10-20 08:20 |
| `falha_massiva` | Falha Massiva | Defeito Massivo (493)<br>FalhaMassiva (203) | 696 | 2024-05-20 13:30 | 2026-10-23 15:20 |
| `falha_infra_aeroporto` | Falha na Infraestrutura do Aeroporto | FalhaInfraAeroporto (1) | 1 | 2026-09-03 09:20 | 2026-09-03 09:20 |
| `falha_infra_cliente` | Falha na Infraestrutura do Cliente | FalhaInfraCliente (37) | 37 | 2026-07-31 10:06 | 2026-10-23 13:40 |
| `falha_operadora` | Falha na Operadora | FalhaOperadora (2) | 2 | 2026-08-28 19:50 | 2026-09-03 07:30 |
| `falha_monitoramento` | Falha no Monitoramento | FalhaMonitoramento (1) | 1 | 2026-08-13 10:50 | 2026-08-13 10:50 |
| `falha_pppoe` | Falha no PPPOE | FalhaPPPOE (8) | 8 | 2026-08-17 16:20 | 2026-10-16 13:30 |
| `Falta Energia no modem/router` | Falta Energia no modem/router | Falta Energia no modem/router (4) | 4 | 2024-05-21 17:30 | 2024-09-04 10:57 |
| `fonte_queimada` | Fonte Queimada | FonteQueimada (4) | 4 | 2026-08-28 18:10 | 2026-09-30 09:50 |
| `Migração` | Migração | Migração (69) | 69 | 2024-10-24 07:40 | 2026-06-17 12:10 |
| `Modem Desconectado` | Modem Desconectado | Modem Desconectado (38) | 38 | 2024-05-24 15:20 | 2026-07-25 10:00 |
| `oscilacao_rede` | Oscilação na Rede | OscilacaoRede (10) | 10 | 2026-08-13 15:10 | 2026-10-22 18:20 |
| `outros_legado` | Outros (catálogo legado) | zOutros (945) | 945 | 2024-05-07 13:40 | 2026-08-03 11:40 |
| `outros_especificar` | Outros (Especificar) | Outros (72) | 72 | 2026-07-29 15:30 | 2026-10-22 18:20 |
| `pendencia_financeira` | Pendência Financeira | PendenciaFinanceira (122)<br>Pendência Financeira (448) | 570 | 2024-06-25 09:10 | 2026-10-23 16:10 |
| `preventiva_falha` | Preventiva com Falha | PreventivaFalha (1) | 1 | 2026-08-14 00:58 | 2026-08-14 00:58 |
| `rompimento_fibra` | Rompimento de Fibra | Rompimento/manut Fibra (650)<br>RompimentoFibra (232) | 882 | 2024-05-20 12:20 | 2026-10-23 09:13 |
| `Sem acesso por 120 minutos (informativo)` | Sem acesso por 120 minutos (informativo) | Sem acesso por 120 minutos (informativo) (23) | 23 | 2024-11-13 13:55 | 2026-06-05 09:50 |
| `sem_intervencao_tecnica` | Sem Intervenção Técnica | Sem Intervenção Técnica (2363)<br>SemIntervencaoTecnica (391) | 2754 | 2024-05-22 08:20 | 2026-10-23 10:30 |
| `sinal_fraco` | Sinal Fraco | SinalFraco (3) | 3 | 2026-08-27 12:20 | 2026-10-09 11:00 |
| `vlan` | Troca de VLAN | Vlan (1) | 1 | 2026-10-15 13:30 | 2026-10-15 13:30 |
| `tunnel_mtu` | Tunnel / MTU | TunnelMTU (47) | 47 | 2026-08-06 12:30 | 2026-10-21 16:50 |
| `vandalismo` | Vandalismo | Vandalismo (1) | 1 | 2026-10-09 09:50 | 2026-10-09 09:50 |

No snapshot desta revisão: **11,973** tickets presentes na fila, **10,310** com motivo, **54** raws distintos com motivo, **44** categorias canônicas e **10** raws excedentes consolidados. Labels canônicos duplicados: **0**. Valores sem regra confirmada permanecem com raw próprio.

## Séries canônicas de transição

Contagem por mês de criação no mirror, com aliases somados na mesma categoria (Nov/2025–Out/2026).

**Falha Massiva** (`falha_massiva`)

| Mês | Tickets |
|---|---:|
| 2025-11 | 4 |
| 2025-12 | 5 |
| 2026-01 | 8 |
| 2026-02 | 20 |
| 2026-03 | 21 |
| 2026-04 | 31 |
| 2026-05 | 55 |
| 2026-06 | 47 |
| 2026-07 | 63 |
| 2026-08 | 90 |
| 2026-09 | 77 |
| 2026-10 | 41 |

**Equipamento Travado** (`equipamento_travado`)

| Mês | Tickets |
|---|---:|
| 2025-11 | 52 |
| 2025-12 | 58 |
| 2026-01 | 77 |
| 2026-02 | 61 |
| 2026-03 | 73 |
| 2026-04 | 139 |
| 2026-05 | 82 |
| 2026-06 | 55 |
| 2026-07 | 73 |
| 2026-08 | 89 |
| 2026-09 | 81 |
| 2026-10 | 68 |

**Rompimento de Fibra** (`rompimento_fibra`)

| Mês | Tickets |
|---|---:|
| 2025-11 | 38 |
| 2025-12 | 40 |
| 2026-01 | 47 |
| 2026-02 | 53 |
| 2026-03 | 56 |
| 2026-04 | 34 |
| 2026-05 | 36 |
| 2026-06 | 40 |
| 2026-07 | 71 |
| 2026-08 | 85 |
| 2026-09 | 76 |
| 2026-10 | 74 |

**Chamado Cancelado** (`chamado_cancelado`)

| Mês | Tickets |
|---|---:|
| 2025-11 | 41 |
| 2025-12 | 30 |
| 2026-01 | 32 |
| 2026-02 | 28 |
| 2026-03 | 21 |
| 2026-04 | 35 |
| 2026-05 | 13 |
| 2026-06 | 17 |
| 2026-07 | 27 |
| 2026-08 | 23 |
| 2026-09 | 55 |
| 2026-10 | 70 |

**Conexão Incorreta - Cabo/Interface** (`conexao_incorreta_cabo_interface`)

| Mês | Tickets |
|---|---:|
| 2025-11 | 4 |
| 2025-12 | 4 |
| 2026-01 | 7 |
| 2026-02 | 5 |
| 2026-03 | 2 |
| 2026-04 | 12 |
| 2026-05 | 7 |
| 2026-06 | 8 |
| 2026-07 | 10 |
| 2026-08 | 1 |
| 2026-09 | 1 |
| 2026-10 | 0 |

## Candidatos revisados que permanecem separados

- `zOutros` vs `Outros` (`Outros (Especificar)` no catálogo atual): o novo raw `Outros` começa em 29/07/2026 enquanto `zOutros` ainda ocorre até 03/08/2026. A sobreposição observada e a falta do catálogo histórico impedem concluir que `z` era somente ordenação; permanecem separados.
- `Defeito de infra` vs `FalhaInfraCliente`: o primeiro é amplo e o segundo atribui explicitamente a causa à infraestrutura do cliente; a semântica não é equivalente.
- `Defeito de Infra - Bradesco` e `Defeito de Infra - Vivo`: valores históricos específicos, sem prova de equivalência com categoria nova.
- `Falha Lógica` não foi observada como raw; `FalhaLogica` atual significa `Falha Lógica na Operadora`. Não se cria alias por semelhança textual.
- `CancelamentoServico` (`Cancelamento de Serviço`) permanece separado de `Cancelado`/`ChamadoCancelado`, pois representa cancelamento do serviço, não encerramento de chamado cancelado.

## Alias canônico confirmado

- `Equipamento travado/reinicilizado` + `EquipamentoTravado` (`Equipamento Travado`) -> `equipamento_travado`.
- `Rompimento/manut Fibra` + `RompimentoFibra` (`Rompimento de Fibra`) -> `rompimento_fibra`.
- `Cancelado` + `ChamadoCancelado` (`Chamado Cancelado`) -> `chamado_cancelado`.
- `Conexão Incorreta - Cabeamento/porta` + `ConexaoIncorreta` (`Conexão Incorreta - Cabo/Interface`) -> `conexao_incorreta_cabo_interface`.

## Semantica aplicada

- Regra confirmada: `FalhaMassiva`, `Falha Massiva`, `Defeito Massivo` -> `falha_massiva` / label canonico `Falha Massiva`.
- Tambem sao consolidados aliases de chaves tecnicas e labels identicos, e pares historicos claros: pendencia financeira, equipamento substituido, falha eletrica, sem intervencao tecnica e atualizacao de configuracao. Labels das chaves novas usam o Dynamic Field 101.
- Filtros consultam conjuntos de raws em SQL. Distribuicoes canonicas agrupam por `CASE/WHEN` em SQL. O raw original segue disponivel para auditoria.
