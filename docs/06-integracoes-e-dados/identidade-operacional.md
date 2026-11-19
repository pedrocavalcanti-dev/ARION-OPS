# Identidade Operacional de Links e Tickets (CMDB + OTRS)

Documentação técnica das regras determinísticas de identificação operacional de **operadora contratante**, **cliente final**, **projeto canônico**, **infraestrutura própria ARION/POP** e da **Central de Revisão de Registros Não Identificados** no ARION OPS.

## 1. Separação conceitual obrigatória

O sistema preserva a separação estrita entre cinco conceitos distintos:

- **Operadora contratante (`contracting_client`):** contratante do circuito perante a ARION (`Claro`, `Vivo`, `Algar`, `ARION` para infraestrutura própria ou `Outras`).
- **Cliente final (`final_client`):** organização atendida pelo circuito (`Bradesco`, `Banco CSF`, `Banco do Brasil`, `Correios`, `Arcos Dourados`, `Santander`, etc.).
- **Projeto canônico (`business_project`):** carteira ou projeto canônico para filtros e análises (`Bradesco`, `Carrefour`, `Correios`, `Banco do Brasil`, `Fust`, `Escolas Conectadas`, `Diversos`, etc.).
- **ISP físico (`operator` / `isp_raw`):** provedor de entrega física registrado na CMDB (ex.: `FIERCE TELECOM`, `GamaNet Telecom`), **nunca** confundido com a operadora contratante.
- **Link / CI (`source_config_item_id`, `designation`, `business_id`):** identidade técnica do circuito no espelho CMDB.

## 2. Arquitetura e imutabilidade das fontes oficiais

- OTRS e CMDB continuam sendo as fontes oficiais somente leitura.
- Nenhum processo de classificação altera `CMDBLinkMirror.name`, `designation`, `isp_raw`, `operator`, nem `OTRSTicket.title`, datas, status, responsáveis ou dados financeiros.
- A camada dedicada **`OperationalLinkIdentity`** (1-para-1 com `CMDBLinkMirror`) e o histórico **`OperationalIdentityRevision`** armazenam:
  - classificação operacional efetiva;
  - tipo de problema (`NONE`, `MISSING_OPERATOR`, `MISSING_CLIENT_PROJECT`, `MISSING_BOTH`, `CONFLICT`, `OTHER_VERIFY`);
  - status de revisão (`PENDING`, `AUTO_RESOLVED`, `CONFIRMED`, `NEEDS_REVIEW`, `CONFIRMED_UNKNOWN`);
  - sugestões automáticas, nível de confiança, origem da classificação e evidências resumidas;
  - marcação de infraestrutura interna ARION/POP (`is_internal_arion`, `internal_detail`);
  - flag de override manual (`is_manual_override`), revisor, data e justificativa.
- O resultado efetivo é projetado nos campos derivados de `CMDBLinkMirror` (`contracting_client`, `contracting_client_raw`, `business_project`, `business_account`, `business_id`, `business_identity_confidence`, `business_identity_source`), garantindo consistência imediata em **Dashboard**, **Cockpit**, **Chamados**, **Base CMDB**, **GEO Inteligência**, **Rankings**, **Qualidade** e **Data Scope**.

## 3. Ordem determinística de prioridade

Implementada em `arion_geo/aplicativos/integracoes/services/operational_identity.py`:

1. **Classificação manual confirmada (`is_manual_override=True`):** nunca é sobrescrita automaticamente por sincronizações CMDB ou OTRS. Caso novas evidências contradigam a classificação confirmada, o sistema preserva a decisão manual e sinaliza `has_conflict=True` com justificativa para nova revisão.
2. **Classificação explícita na CMDB (`LinkBusinessIdentityService.parse`):**
   - Operadoras explícitas e razões sociais normalizadas (`CLARO`, `CLARO S.A.`, `TELMEX`, `TELMEX DO BRASIL S/A`, `CLARO / TELMEX`, `VIVO`, `TELEFONICA BRASIL S.A.`, `TELEFONICA DO BRASIL`, `ALGAR`, `ALGAR TELECOM`).
   - Prefixos regionais `<OPERADORA>.<UF>.` (`CLARO.SC.BLIVR...`, `VIVO.PE.BLIVR...`, `ALGAR.SC.BLIVR...`), usados apenas quando não há token explícito anterior e nunca promovidos a projeto.
   - Aliases corporativos específicos (ex.: `BANCO CSF` → cliente final `Banco CSF`, projeto canônico `Carrefour`), sem forçar operadora sem evidência independente.
   - Regras exclusivas de projeto (`CORREIOS`, `BANCO DO BRASIL` / `PROJETO BB`, `CAIXA ECONOMICA`, `CARREFOUR`, `FUST` [não `FUST UBS`], `L2L`, `ARCOS DOURADOS`, `ESCOLAS CONECTADAS`). Projetos compartilhados (`BRADESCO`, `FUST UBS`, `SANTANDER`, `DIVERSOS`) nunca inferem operadora apenas pelo nome do projeto.
   - Detecção conservadora de infraestrutura interna ARION/POP (`POP ARION`, `ARION POP`, `BACKBONE ARION`, `NOC ARION`, `INFRAESTRUTURA ARION`), excluindo referências administrativas (`CNPJ ARION`, `GESTORES COMERCIAL`).
3. **Relação direta oficial OTRS (`OTRSTicketConfigItem`):** analisa títulos e metadados (`customer_id` não-relay) de tickets vinculados oficialmente ao link. Com consenso sem conflito, resolve operadora e/ou projeto ausente. `"PROJETO DIVERSOS"` / `"DIVERSOS"` é tratado como genérico e **nunca** substitui um cliente/projeto específico da CMDB (como `Banco CSF` / `Carrefour`).
4. **Consenso histórico por designação única (`business_id`):** quando o circuito ainda está pendente e possui designação única na CMDB, analisa tickets distintos no espelho local que mencionem o mesmo circuito:
   - **3 ou mais tickets distintos** sem evidências conflitantes → promove automaticamente (`OTRS_HISTORY_CONSENSUS`).
   - **1 ou 2 tickets distintos** sem conflito → não promove automaticamente; preenche apenas `suggested_*` para confirmação humana.
   - **Evidências divergentes** → nunca chuta; registra `has_conflict=True` e detalha a divergência em `conflict_reason`.
5. **Regra anti-autocorroboração:** o motor avalia sempre os campos brutos originais (`link.name`, `link.designation`, `ticket.title`, `ticket.customer_id`), nunca reutilizando uma inferência anterior como prova de si mesma.

## 4. Infraestrutura própria ARION / POP

Links confirmados como infraestrutura própria (ex.: `POP ARION - AEROP. ALTA FLORESTA (AFL) - MT` e `CNF - POP ARION - MG.BLIVR000501`):

- Recebem `is_internal_arion=True`, `contracting_client="ARION"`, `business_project="POP ARION"` (ou `"Infraestrutura ARION"`) e `final_client=""` (sem inventar cliente externo).
- No Dashboard, GEO Inteligência e filtros de operadora agrupam em **Outras** (não em Claro, Vivo, Algar nem em "Cliente não identificado"), preservando a identificação `ARION` / `POP ARION` nos detalhes.
- Ficam fora da fila de links desconhecidos na Central de Revisão, com aba dedicada para conferência.

## 5. Títulos compactos no drawer de Chamados

O drawer de Chamados exibe um cabeçalho operacional compacto gerado por `build_compact_ticket_title`:

- Exemplo Vivo/Bradesco: `Vivo · Bradesco · BL50000080775`
- Exemplo Claro/Banco CSF: `Claro · Banco CSF (Carrefour) · BL150000094228`
- Quando a identidade não está confirmada, exibe identificação neutra com os dados conhecidos (designação e/ou número do chamado).
- O título integral original do OTRS permanece acessível no bloco expansível **“Título original OTRS”** logo abaixo do título compacto.

## 6. Reprocessamento seguro e sincronização

- Comando idempotente (100% local, sem consultas ao OTRS remoto):
  - Prévia (dry-run): `.venv\Scripts\python.exe arion_geo\gerenciar.py classificar_identidade_cmdb`
  - Persistência: `.venv\Scripts\python.exe arion_geo\gerenciar.py classificar_identidade_cmdb --apply`
- `CMDBSyncService.run(apply=True)` e `OTRSSyncService.run(apply=True)` executam reconciliação segura após o espelhamento, preservando todas as classificações manuais confirmadas (`is_manual_override=True`) e sinalizando conflitos se a fonte divergir.

## 7. Central de Revisão de Registros Não Identificados

Acessível em **Base → Não identificados** (`/base/nao-identificados/`):

- **Separação de filas e indicadores:** links analisados, sem operadora, sem cliente/projeto, com conflito, sugestões confiáveis, corrigidos manualmente, links pendentes e tickets sem vínculo CMDB (separados em abas distintas, sem duplicar links com múltiplos chamados).
- **Investigação individual em drawer com navegação Anterior / Próximo:** exibe CMDB original, identidade atual, sugestão automática, conflitos, títulos completos dos tickets OTRS relacionados (com link para OTRS e Base CMDB) e histórico de auditoria.
- **Correção manual supervisionada:** confirmar sugestão, editar operadora/cliente final/projeto, marcar como infraestrutura própria ARION/POP, confirmar desconhecido, encaminhar para revisão posterior ou reabrir para classificação automática.
- **Segurança:** protegida por `configuracoes.view_base`, `configuracoes.manage_operational_identity` (POST de correção), `configuracoes.export_base` (CSV) e `DataScopeService` no backend, com trilha de auditoria em `OperationalIdentityRevision` e `AuditEvent`.

## 8. Exemplos reais homologados em DEV

- **`BL50000080775` (CI `6058`):** CMDB `BRADESCO - BL50000080775` + ticket OTRS `TELEFONICA BRASIL S.A.` → classificado como **Vivo / Bradesco**, preservando ISP físico `FIERCE TELECOM`.
- **`BL150000094228` (CI `6391`):** CMDB `BANCO CSF - BL150000094228` + tickets OTRS `CLARO S.A. / PROJETO DIVERSOS` → classificado como **Claro / Banco CSF (Carrefour)**, preservando ISP físico `GamaNet Telecom` sem generalizar `PROJETO DIVERSOS`.
- **`BL300000093987` e padrão `BRAD - TELMEX` (`BL10000078874`):** cliente **Bradesco**, operadora **Claro** preservando referência bruta `TELMEX`.
- **`CNF - POP ARION - MG.BLIVR000501` (CI `10173`) e `POP ARION - AEROP. ALTA FLORESTA (AFL) - MT` (CI `6251`):** classificados como infraestrutura interna **ARION / POP ARION**, fora da fila de desconhecidos.
- **`FUST UBS - CLARO.SC.BLIVR001101` / `FUST UBS - VIVO.PE.BLIVR000252`:** operadora extraída corretamente do prefixo regional (`Claro` / `Vivo`) com projeto `Fust`.
