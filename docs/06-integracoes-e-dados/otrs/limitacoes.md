# Limitações e cobertura do levantamento OTRS

## Cobertura identificada no repositório

| Elemento | Identificado | Documentado | Critério e situação |
|---|---:|---:|---|
| Tabelas OTRS de domínio | 21 | 21 | Confirmado pelo código em sync, estudo e consultas de Qualidade |
| Pares tabela/coluna | 80 | 80 | Confirmados em projeções, filtros, joins e leitura de estudo; não representa toda a estrutura do OTRS |
| Tipos do snapshot local | 79 | 79 | Confirmados no artefato local sanitizado de 23/10/2026; schema atual pendente |
| Famílias de consulta | 48 | 48 | Catálogo por template/família; a quantidade de execuções varia por lote, fila e dimensão |
| Apps consumidores | 8 | 8 | Identificados no código como leitores do mirror ou relações locais |
| Padrões de relacionamento | 9 | 9 | Chaves e associações observadas em SQL e transformação local; cardinalidades são conceituais quando o código não impõe unicidade na origem |
| Diagramas de fluxo | 5 | 5 | Diagramas Mermaid simples aceitos pelo renderizador do portal |
| Páginas OTRS | 18 | 18 | Visão geral e 17 páginas técnicas registradas no portal |

## Classificação das evidências

- Confirmado pelo código: tabelas, projeções, filtros, queries, transformações e destinos locais que aparecem no repositório.
- Confirmado por snapshot local: tipos e metadados registrados no estudo sanitizado datado de 23/10/2026. O snapshot não foi atualizado durante esta tarefa.
- Inferido: significado funcional e cardinalidade deduzidos da utilização das colunas quando não existe constraint ou documentação de origem disponível.
- Pendente: configuração e estado atuais do MySQL/OTRS, grants efetivos, índices existentes no servidor, volume real, latência/custo das queries e timezone efetivo da fonte.

## Limites conhecidos

1. O mirror cobre as filas configuradas para operações e os fluxos explícitos de Qualidade; não é réplica integral do OTRS.
2. A sincronização FULL consulta todo o conjunto definido para as filas e não usa paginação SQL. Batches de relação e valores Dynamic Field reduzem o tamanho de partes do processamento, mas não alteram esse limite da projeção principal.
3. FAST usa change_time com janela de sobreposição e IDs locais conhecidos. Ele não detecta por si só qualquer remoção histórica fora do escopo observado.
4. O cursor e o estado de sincronização são locais. Uma falha após atualização local pode fazer a leitura ser repetida; o processamento utiliza chaves e upserts idempotentes.
5. A sobreposição de 40 IDs no checkpoint de artigos ajuda a cobrir atrasos próximos ao cursor, mas não garante detecção de artigo que apareça tardiamente com ID inferior à janela.
6. A reconciliação manual usa LIKE sobre article_data_mime.a_body, limitado a um TN e até 20 resultados, e valida o QID exato depois. O custo não foi medido e pode depender de varredura de corpos.
7. a_body é dado sensível e o tipo SQL não aparece no snapshot sanitizado; o conteúdo só é exposto transitoriamente ao parser de QID.
8. A configuração TLS atual não foi validada contra o servidor. No código, quando não há CA explícita, o contexto não habilita a verificação de hostname; isso deve ser tratado como observação técnica para revisão operacional, não como confirmação de identidade remota.
9. SET SESSION TRANSACTION READ ONLY e os guardrails do cliente não comprovam os grants efetivos da conta MySQL.
10. Não há evidência encontrada de cálculo de conformidade de SLA. O código usa identificadores, nomes e campos de escalação onde aplicável; não se deve inferir percentual de SLA cumprido.
11. A fonte não foi consultada nesta tarefa. Nenhum ticket, artigo, dado pessoal, credencial ou servidor de produção foi acessado.

## Itens a validar em uma revisão autorizada da origem

- Atualizar o snapshot de schema, chaves, índices e nulabilidade, sem exportar dados de ticket.
- Confirmar grants somente leitura e compatibilidade da sessão read-only.
- Validar cadeia de certificado, nome DNS e comportamento de verificação TLS usado em produção.
- Medir plano/custo das consultas FULL e da reconciliação por TN, com parâmetros sintéticos ou controles aprovados.
- Confirmar timezone efetivo da fonte e comparar com o timezone Django configurado.
- Confirmar limites de retenção, volume e política de atrasos para artigos e alterações de tickets.

Estas validações exigem autorização e acesso controlado à fonte. Não são pré-requisito para a documentação baseada no código, mas limitam afirmações sobre o ambiente atual.

Veja [segurança](otrs-seguranca.md), [sincronização](otrs-sincronizacao.md), [consultas SQL](otrs-consultas-sql.md) e [diagnóstico](otrs-diagnostico.md).
