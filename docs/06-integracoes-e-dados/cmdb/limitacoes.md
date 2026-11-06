# 20 · Limitações e cobertura

## Cobertura do inventário documentado

| Artefato mensurado | Documentado | Escopo e limite |
|---|---:|---|
| Operações externas | 2 de 2 | ConfigItemSearch e ConfigItemGet; caminhos e payloads construídos pelo client. |
| Classes de sync | 2 de 2 | Links de internet e Operadoras. |
| Modelos principais | 2 de 2 | CMDBLinkMirror e CMDBOperatorMirror; tabelas documentadas. |
| Campos das whitelists | 65 de 65 | 24 de Operadoras e 41 de Links; não inclui metadados de sincronização. |
| Consultas internas catalogadas | 8 de 8 | Famílias representativas Q001–Q008, não cada queryset do projeto. |
| Transformações catalogadas | 8 de 8 | Pontos críticos T001–T008 do fluxo. |
| Áreas consumidoras | 8 de 8 | Base, Chamados, Dashboard, Cockpit, Rankings, Mapa, Qualidade, Relatórios. |
| Fluxos agendados de sync | 1 de 1 | FULL CMDB para ambas as classes, cadência 10 minutos. |
| Vínculo OTRS–CMDB | 1 fluxo | Join por ConfigItemID em relação local separada. |

O denominador é o escopo declarado em [Visão geral](cmdb.md), não uma prova de cobertura de todo o repositório ou de todos os atributos da fonte. A integração externa não foi exercitada nesta tarefa. Por isso, a cobertura de código está mensurada, mas o contrato atual da instância não está 100% confirmado.

## Seis validações externas pendentes

1. Envelope real vigente da GenericInterface para Search/Get e variações da configuração instalada.
2. Tipos, nulabilidade e obrigatoriedade atuais dos atributos dinâmicos de Links e Operadoras.
3. Semântica de paginação, limite máximo e exaustividade do Search sem parâmetro Limit.
4. Quotas/rate limits e capacidade de lote da ServiceUp para volumes atuais.
5. Estado TLS efetivo e cadeia de certificado do ambiente de produção; nenhuma configuração de produção foi consultada.
6. Cobertura semântica atual de aliases, estados e valores presentes, que depende de validação autorizada dos donos dos dados.

## Lacunas técnicas observadas no código

- Search não implementa paginação explícita; a execução assume retorno integral ao omitir Limit.
- O cliente não tem retry/backoff HTTP próprio. O worker agenda novas tentativas após falha.
- CIXMLData pode ser objeto ou XML, mas o schema dinâmico não é tipado nem versionado no repositório.
- O mirror guarda o estado atual e freshness, não histórico completo de cada valor da ServiceUp.
- Campo ISP pode não resolver se o valor não for ID/nome exato único; o texto bruto é preservado.
- A classificação de cliente/projeto é heurística versionada no código e pode requerer alias aprovado em novos padrões de Name.
- Geolocalização não produz coordenadas de circuito; o mapa agrega por texto de cidade/UF e geometria regional.
- Os oito grupos de consulta são catálogo operacional seletivo, não todas as consultas secundárias ou relatórios.

## O que precisa de validação futura

Uma validação externa autorizada deve comparar amostras sintéticas ou sanitizadas com o contrato vigente, medir a exaustividade do Search, confirmar limites e rever os alias/state labels com o responsável pelos dados. Essa atividade deve permanecer somente leitura e não faz parte deste documento implementado. Não sincronizar, importar, editar ConfigItems nem consultar a ServiceUp foi necessário para esta tarefa.

## Documentos e fontes

As páginas são Markdown do repositório e usam o renderer/navegação existentes do portal. O catálogo de código principal está em [Visão geral](cmdb.md). A referência OTRS correspondente está em [OTRS · Limitações](otrs-limitacoes.md). Nenhum dado de produção nem credencial é incluído.
