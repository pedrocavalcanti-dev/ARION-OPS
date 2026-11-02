# Estudo do Índice ARION V1 × V2 · outubro/2026

## Recorte e método

Leitura somente do mirror local para **01/10/2026–25/10/2026**, com usuário superuser técnico para abranger todo o Data Scope disponível. O `RankingService.build` consultou **3.438 links**, **535 chamados nacionais distintos** e **1.303 provedores** em **4 consultas**, em **1,993 s**. No recorte havia 267 provedores com chamados e 1.036 sem chamados nem backlog.

V1 e V2 foram calculados sobre os mesmos agregados e o mesmo universo filtrado. V1 usa a metodologia ordinal histórica. V2 usa o posto mais próximo do P95: para este snapshot, **P95 de chamados = 2** e **P95 de links afetados = 1**. Ambos os contadores são limitados ao P95 antes da pontuação do Impacto no NOC. P95 é interno ao modelo; não aparece por linha na tela.

## Top 20 da V1 e posição na V2

As posições abaixo refletem a lista V1 histórica completa e o ranking V2 de risco. “—” significa que o provedor não pertence à visão de risco V2, que exclui zero ocorrências.

| Provedor | V1 (posição) | V2 (posição e índice) | O que mudou |
|---|---:|---:|---|
| ON LINE TELECOM | 90,1 (1) | 87,5 (17) | Mantém alto risco proporcional; outros provedores chegam a 90,0 com aging integral e sem backlog. |
| FIBRAXNET | 89,7 (2) | 86,6 (18) | Base e reincidência seguem máximas; tem 2 de 3 chamados acima de 24h. |
| NWI | 88,3 (3) | 76,6 (38) | 1 de 2 links afetado e 2 de 3 chamados acima de 24h reduzem base/aging frente aos líderes. |
| GNET | 88,3 (4) | 71,0 (53) | Volume chega ao máximo do componente de impacto, mas 6/10 links afetados e 3/6 reincidentes moderam o total. |
| GIGANET TELECOM | 87,7 (5) | 72,7 (52) | Apenas 1/5 links afetado; reincidência e 7/8 acima de 24h continuam pontuando. |
| TASS NETWORKS | 87,2 (6) | 90,0 (15) | 1/1 link afetado, reincidente e ambos os chamados acima de 24h; não há backlog. |
| Digital Telecom | 86,9 (7) | 75,0 (43) | Um dos dois links foi afetado; metade dos chamados ultrapassou 24h e há backlog. |
| TRI TELECOM | 86,9 (8) | 75,0 (46) | Mesma proporção de links afetados e aging de Digital Telecom; desempate por nome. |
| RV CONNECT | 86,3 (9) | 90,0 (1) | 1/1 link, quatro chamados, reincidência e 4/4 acima de 24h. |
| Periquito Net | 86,1 (10) | 90,0 (2) | 1/1 link, três chamados, reincidência e 3/3 acima de 24h. |
| Maxnet Internet e TV | 86,0 (11) | 75,0 (40) | 1/1 link afetado e reincidente, mas apenas 1/4 acima de 24h. |
| CLARO | 86,0 (12) | 47,1 (205) | Escala maior (129 links/16 chamados), com 8,5% da base afetada, 36,4% reincidente e 37,5% acima de 24h. |
| TERA NETWORK | 85,9 (13) | 80,0 (22) | Dois links afetados em uma base de dois e todos os cinco chamados acima de 24h. |
| RAPIX | 85,7 (14) | 80,0 (24) | 2/2 links afetados, 1/2 reincidente e todos os quatro chamados acima de 24h. |
| MV Telecom | 85,5 (15) | 80,0 (30) | 1/1 link afetado e reincidente; aging em 1/2 chamados. |
| AC Telecom | 85,5 (16) | 90,0 (3) | 1/1 link afetado e reincidente, com 2/2 chamados acima de 24h. |
| AGILE TELECOM | 85,5 (17) | 90,0 (4) | 1/1 link afetado e reincidente, com 2/2 chamados acima de 24h. |
| BZ CONECTA | 85,5 (18) | 90,0 (5) | 1/1 link afetado e reincidente, com 2/2 chamados acima de 24h. |
| CARLUSAT CONNECT | 85,5 (19) | 90,0 (6) | 1/1 link afetado e reincidente, com 2/2 chamados acima de 24h. |
| CLICK NET | 85,5 (20) | 90,0 (7) | 1/1 link afetado e reincidente, com 2/2 chamados acima de 24h. |

## Top 20 da V2

| Pos. V2 | Provedor | Índice V2 | Pos. V1 |
|---:|---|---:|---:|
| 1 | RV CONNECT | 90,0 | 9 |
| 2 | Periquito Net | 90,0 | 10 |
| 3 | AC Telecom | 90,0 | 16 |
| 4 | AGILE TELECOM | 90,0 | 17 |
| 5 | BZ CONECTA | 90,0 | 18 |
| 6 | CARLUSAT CONNECT | 90,0 | 19 |
| 7 | CLICK NET | 90,0 | 20 |
| 8 | Cybertech Telecom | 90,0 | 21 |
| 9 | ERB | 90,0 | 22 |
| 10 | FNT Telecom | 90,0 | 23 |
| 11 | Fronteira Net | 90,0 | 24 |
| 12 | GHNet Telecom | 90,0 | 25 |
| 13 | Inforsat Tecnologia | 90,0 | 26 |
| 14 | MICRORCIN | 90,0 | 27 |
| 15 | TASS NETWORKS | 90,0 | 6 |
| 16 | TELECOCAL | 90,0 | 28 |
| 17 | ON LINE TELECOM | 87,5 | 1 |
| 18 | FIBRAXNET | 86,6 | 2 |
| 19 | ST Digital | 85,0 | 31 |
| 20 | VOCALNET | 80,0 | 33 |

Os primeiros lugares V2 concentram links com 100% da base afetada e alta recorrência/aging. A ordem entre linhas de 90,0 segue os desempates documentados. A V2 desce provedores com maior escala quando os índices de base, recorrência e aging são menores; volume absoluto ainda pode alcançar os 30 pontos completos.

## Maior risco · Top 10 V2

| Pos. | Provedor | Links | Chamados | Chamados/link | Afetados | Base | Reincidentes | >24h | Backlog | Impacto NOC | Índice V2 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | RV CONNECT | 1 | 4 | 4,00 | 1 | 100,0% | 1 | 4 | 0 | 30,0/30 | 90,0 |
| 2 | Periquito Net | 1 | 3 | 3,00 | 1 | 100,0% | 1 | 3 | 0 | 30,0/30 | 90,0 |
| 3 | AC Telecom | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 4 | AGILE TELECOM | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 5 | BZ CONECTA | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 6 | CARLUSAT CONNECT | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 7 | CLICK NET | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 8 | Cybertech Telecom | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 9 | ERB | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |
| 10 | FNT Telecom | 1 | 2 | 2,00 | 1 | 100,0% | 1 | 2 | 0 | 30,0/30 | 90,0 |

## Melhor desempenho observado · Top 10

Somente provedores com pelo menos um chamado. O pódio usa a amostra mínima de cinco links ou três chamados; provedores menores seguem na tabela.

| Pos. | Provedor | Links | Chamados | Chamados/link | Afetados | Base | Reincidentes | >24h | Backlog | Impacto NOC | Índice V2 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | LINKTEL | 5 | 1 | 0,20 | 1 | 20,0% | 0 | 0 | 0 | 21,0/30 | 25,0 |
| 2 | SPEED NET | 5 | 1 | 0,20 | 1 | 20,0% | 0 | 0 | 0 | 21,0/30 | 25,0 |
| 3 | Vale do Ivaí Telecom | 5 | 1 | 0,20 | 1 | 20,0% | 0 | 0 | 0 | 21,0/30 | 25,0 |
| 4 | MINAS TELECOM | 4 | 1 | 0,25 | 1 | 25,0% | 0 | 0 | 0 | 21,0/30 | 26,0 |
| 5 | REDE M2B | 4 | 1 | 0,25 | 1 | 25,0% | 0 | 0 | 0 | 21,0/30 | 26,0 |
| 6 | TELEMIDIA TELECOM | 4 | 1 | 0,25 | 1 | 25,0% | 0 | 0 | 0 | 21,0/30 | 26,0 |
| 7 | GTec Net | 3 | 1 | 0,33 | 1 | 33,3% | 0 | 0 | 0 | 21,0/30 | 27,7 |
| 8 | JJ Informática | 3 | 1 | 0,33 | 1 | 33,3% | 0 | 0 | 0 | 21,0/30 | 27,7 |
| 9 | NetFácil Isp Telecom | 3 | 1 | 0,33 | 1 | 33,3% | 0 | 0 | 0 | 21,0/30 | 27,7 |
| 10 | WINNET | 3 | 1 | 0,33 | 1 | 33,3% | 0 | 0 | 0 | 21,0/30 | 27,7 |

## Sem ocorrências · Top 10 por links CMDB

| Pos. | Provedor | Links | Clientes | Projetos | UFs |
|---:|---|---:|---|---|---|
| 1 | VIVO | 16 | Claro, Vivo | Banco do Brasil, Correios, Diversos | MA, PE, PI, PR, RJ, RS, SC, SP |
| 2 | BR DIGITAL | 11 | — | — | CE, GO, MA, MG, PR, RS |
| 3 | LINK NET TELECOM | 10 | Claro, Vivo | Bradesco, Correios, Fust | BA, MA, MT, PA, PE, RO |
| 4 | NVX | 10 | Claro, Vivo | Banco do Brasil, ENERGISA | MG, MT, PB, SP |
| 5 | AONET | 8 | Claro, Vivo | Banco do Brasil, Bradesco, Correios, SERILON | SP |
| 6 | CTE TELECOM | 8 | Claro | Banco do Brasil | PA |
| 7 | Rapeedo Internet | 8 | Claro | Correios | MG |
| 8 | CLOUD - EMBRATEL | 7 | — | — | AM, MG, PA, RJ, SP |
| 9 | Conecta | 7 | Claro, Vivo | Bradesco, Correios, Fust | MG |
| 10 | GIGALINK | 7 | Claro | Banco do Brasil, Caixa Econômica Federal, Correios | RJ |

## Casos pedidos

**ON LINE TELECOM** tem 1 link, 4 chamados, 1 link afetado, reincidência, 3 chamados acima de 24h e 1 backlog. Pontuação: Impacto no NOC 30,0/30; Base afetada 20,0/20; Reincidência 20,0/20; Aging 15,0/20; Backlog 2,5/10; **V2 87,5**. Era posição 1 na V1 e fica em 17 na V2: mantém risco crítico, mas há provedores com proporção igual e aging integral, sem backlog, que somam 90,0.

**FIERCE TELECOM** tem 267 links, 28 chamados, 24 links afetados (9,0%), 4 reincidentes, 6 chamados acima de 24h e backlog 1. A composição é **30,0 + 1,8 + 3,3 + 4,3 + 0,4 = 39,8/100**, nível Baixo. O Impacto no NOC satura em 30/30; sua baixa taxa da base e menor reincidência/aging reduzem o total. A posição muda de 39 na V1 para 250 entre 267 provedores de risco V2. Essa diferença mostra o equilíbrio intencional: volume absoluto fica visível, enquanto a priorização final considera também proporção e persistência.

**GNET** tem 10 links, 10 chamados, 6 afetados, 3 reincidentes, 9 acima de 24h e backlog 1: `30 + 12 + 10 + 18 + 1 = 71`, nível Alto. Cai da posição 4 V1 para 53 V2 porque não tem a proporção máxima dos provedores de um único link.

**VIVO** (16 links) e **BR DIGITAL** (11 links) não tiveram chamados nem backlog. Ambas recebem **Índice 0 · Sem ocorrências**, ficam fora de Melhor desempenho e Maior risco e aparecem em Sem ocorrências. A V1 atribuía 43,3 às duas por efeito dos percentis empatados. Para o caso de teste de grande base saudável (267 links, 2 chamados, 2 afetados, sem reincidência, aging ou backlog), o V2 permanece abaixo de 20, enquanto uma base pequena com 100% afetada, recorrência e aging fica em nível alto.

## Verificações

- `RankingService.build`: 4 consultas para 3.438 links e 1.303 provedores; 1,993 s no snapshot local.
- Testes focados de Rankings: 20 aprovados em SQLite em memória. A criação do banco de teste MySQL foi negada pelas permissões locais; o mesmo conjunto passou no backend de desenvolvimento suportado.
- `manage.py check`: sem problemas.
- `makemigrations --check`: nenhuma migration pendente.
- O estudo e a interface não alteram a origem OTRS/CMDB, não persistem score e não criam migrations.
