# Roadmap de evolução

Revisado em 19/11/2026. A versão **1.4.1** está registrada no catálogo interno `docs/08-releases/releases.json`, e o conjunto de entregas abaixo compõe a [release candidata `v1.4.2`](../08-releases/v1.4.2-candidata.md). Este documento distingue o que está **IMPLEMENTADO** no código do que permanece **PLANEJADO**; publicação não equivale a implantação.

## IMPLEMENTADO — Entregas consolidadas até 1.4.0

- **Identidade visual ARION OPS · NOC ATACADO:** consolidada nos templates, cabeçalho, login institucional, exportações e documentação, com crédito institucional em Sobre o ARION OPS e `NOTICE.md`.
- **PWA e atualizações:** aviso de nova versão persistente em páginas autenticadas, indicador discreto no cabeçalho e ativação controlada pelo usuário.
- **Cockpit:** contextos Suporte, Cancelamentos e Geral, indicadores operacionais, séries reconciliadas e exportações CSV/PDF/XLSX respeitando Data Scope.
- **Rankings:** exposição por links ativos, incidência proporcional (`/100 ativos`) e leituras de risco/desempenho pelo Índice ARION.
- **Base inicial Claro Multas:** fluxo existente preservado no código; fica fora da navegação padrão da V1 apresentada.
- **Qualidade Operacional (Fases 1–4):** Dashboard gerencial com ECharts local, carteira derivada de Links Prioritários (Ativos, Estabilizados e Todos), fila e detalhe completo de Ordens de Qualidade (Resumo, Ocorrências, Tratativas, Monitoramento RREIP de 30 dias e Histórico), diretório de contatos/escalonamentos e histórico auditado.
- **Sincronização OTRS FAST/FULL:** cobertura das quatro filas operacionais do Cockpit com controle de lease e sinalização de cobertura aplicada.
- **Cancelamentos:** triagem humana, fluxo oficial, histórico, valores informados e Dashboard Gerencial com filtros e controle financeiro. Importação e reconciliação histórica executadas somente no DEV.
- **Qualidade/Brevo/OTRS:** solicitação de TQ por SMTP/API autorizada, envio idempotente e correlação exata por QID; monitoramento e decisão humana final da Qualidade.
- **Acessos:** ciclo de convite, ativação e redefinição de senha, controles administrativos, RBAC, Data Scope e auditoria.
- **Backlog e aging:** fechamento mensal histórico do Suporte e aging da fila atual do Dashboard alinhado à página Chamados.
- **Identidade Operacional e Central de Revisão de Não Identificados (em DEV; deploy produção pendente):** motor central determinístico e auditável (`OperationalLinkIdentity` / `OperationalIdentityRevision`, migrations `integracoes.0022` e `configuracoes.0013`) combinando CMDB, vínculos diretos OTRS, consenso histórico (3+ tickets distintos sem conflito), aliases corporativos (`Banco CSF` / `Carrefour` sem generalizar `Projeto Diversos`) e reconhecimento conservador de infraestrutura própria `ARION/POP` agrupada em Outras; títulos compactos no drawer de Chamados preservando título integral OTRS expansível; e aba **Não identificados** na Base (`/base/nao-identificados/`) com fila separada por tipo de problema, investigação individual com navegação Anterior/Próximo, correção manual supervisionada protegida contra sobrescrita em syncs, RBAC (`manage_operational_identity`), Data Scope e exportação CSV. Ver [Identidade Operacional](../06-integracoes-e-dados/identidade-operacional.md).
- **Reconciliação de Indicadores — GEO Inteligência, Dashboard × Cockpit e Backlog (em DEV; deploy produção pendente):** paridade total de incidentes válidos, links afetados e reincidências entre GEO Normal e Modo Apresentação (com agrupamento seguro de motivos financeiros e não técnicos em **Outros motivos**, sem projeção simulada); unificação da métrica canônica de Tickets criados / Novos no período entre Dashboard e Cockpit; alinhamento de Backlog atual (`Atualizado em ...`, >24h e >72h) × Backlog histórico reconstruído (`Observado em ...`, picos por intervalo, endpoint `/api/analytics/backlog-at/` e coleta horária persistente `OTRSBacklogCheckpoint`, migration `integracoes.0023`). Ver [Métricas Operacionais](../02-arquitetura/metricas-operacionais.md).
- **Qualidade Operacional, Reincidências R1–R5+, Contatos e Análise Unificada da Planta CMDB (candidata `v1.4.2`; deploy produção pendente):** visualização completa de **Links estabilizados** (`/qualidade/estabilizados/`) com guarda de incidente ativo e separação estatística × RREIP; cálculo automático e auditável de níveis **R1–R5+** nas Ordens de Qualidade preservando níveis manuais e indicando `A confirmar` quando histórico insuficiente; correção de RBAC (`manage_quality_contacts` no catálogo central para `Administrador do Sistema` e `Gestor`, migration `qualidade.0018`) e melhorias operacionais em **Contatos** (categoria, WhatsApp, horário de atendimento, cópia rápida, migration `qualidade.0017`); e experiência unificada **Planta CMDB × Chamados** no Dashboard (card **Base CMDB** + botão **Analisar planta**) e no Cockpit (botão **Planta × Chamados** com sincronização dos filtros ativos), compartilhando o modal em 3 abas (**Visão geral**, **Evolução histórica**, **Comparação por período**), o endpoint `/api/analytics/plant-history/` e snapshots mensais idempotentes (`CMDBPlantMonthlySnapshot`/`CMDBPlantMonthlySlice`, migration `integracoes.0024`). Ver [candidata `v1.4.2`](../08-releases/v1.4.2-candidata.md).

## PLANEJADO — Próximas evoluções funcionais

- **Migração Windows → Linux/Docker (PENDENTE — próxima etapa):** preparar ambiente Linux com Docker Web e Worker, backup consistente e restauração validada do MySQL existente (preservando tabelas Django, histórico operacional, vínculos e arquivos persistentes), volumes persistentes, configuração de domínio, proxy reverso, HTTPS, substituição segura de DPAPI, documentação, autenticação e painel de gestão.

- **Correções de governança verificadas na auditoria documental:** separar escrita de importação de `view_reports` e definir Data Scope de competências/linhas; proteger contas por convite pendente na coorte do comando global de senhas (`manage_quality_contacts` já integrado ao catálogo administrativo). Não apresentar pendências abertas como implementadas.
- **Migração histórica produtiva:** habilitar modo seletivo autorizado, homologar em MySQL real e reconciliar o snapshot atualizado sem substituir o banco existente; o importador atual permanece DEV-only.
- **Microsoft 365 / Entra ID:** login corporativo, MFA, vinculação das contas locais existentes, sincronização/mapeamento de grupos para RBAC e preservação do Data Scope interno. Nenhum SSO/MFA Microsoft foi implementado na 1.4.0.

- **Central de Relatórios (fase futura):** evoluir o módulo para acompanhar resultados operacionais em tempo real por cliente/contrato e analisar desempenho operacional, SLA, qualidade, reincidência, volume, resultados alcançados e evolução ao longo do tempo. Apoiar também o acompanhamento do desempenho da ARION junto a clientes e contratos, incluindo rankings e indicadores fornecidos por clientes quando houver fonte oficial.

- **Critérios formais e playbooks R1–R5 na Qualidade (PLANEJADO):** formalização operacional definitiva dos critérios de classificação R1–R5 e playbooks orientados por nível.
- **Duração histórica por etapa (PLANEJADO):** ampliar o uso de eventos para métricas homologadas de tempo em etapas de Cancelamentos/Suporte. O cache de estado/fila/procedência usado no backlog histórico de Suporte já está implementado.
- **Evolução da ARI I.A (PLANEJADO):** conexão a provedor LLM aprovado com governança de dados e escopo (atualmente opera em modo Preview local).
- **Histórico temporal de denominadores CMDB (PLANEJADO):** snapshots periódicos da base de links para permitir denominadores históricos em análises retrospectivas de longo prazo.
- **Definições formais ANOP / ETIT e SLA contratual (PLANEJADO):** dependentes de homologação das regras de negócio com a operação antes de qualquer implementação.

## PLANEJADO — Evolução de escalabilidade e infraestrutura

A arquitetura V1 opera como um **monólito modular Django** com Waitress e MySQL local. Conforme o volume de acessos e dados crescer, a evolução planejada prevê etapas progressivas (não implementadas na V1):

O próximo destino de hospedagem planejado é Linux corporativo com Docker, DNS/IP fornecidos pela ARION, domínio `arionops.grupoarion.com.br`, HTTPS e independência do Cloudflare Tunnel atual. Ainda não implantado; Windows Server e o domínio conhecido continuam a referência operacional. Evitar novas dependências de Windows sem necessidade, preservando compatibilidade das funcionalidades existentes e planejando a substituição segura de DPAPI.

1. Otimização contínua de consultas e índices no MySQL;
2. Separação dedicada de workers de sincronização e processamento assíncrono;
3. Camada de cache compartilhado (ex.: Redis) para sessões e agregações quentes;
4. Escala vertical de banco e servidor de aplicação;
5. Múltiplas instâncias web com balanceamento de carga (load balancing);
6. Serviços especializados apenas onde houver justificativa técnica real.

## Limitações conhecidas do histórico operacional atual

- A CMDB sincronizada reflete o estado cadastral corrente; não há série histórica nativa de alterações de quantidade de links por provedor em meses passados.
- O espelho OTRS ainda não materializa toda a sequência histórica de `ticket_history` (`Move` e `StateUpdate`).
- No Cockpit, a métrica de Novos utiliza `source_created_at` (criação do ticket no OTRS), não o instante de transferência entre filas.
- Tickets sem Provider resolvido e tickets vinculados a múltiplos ConfigItems afetam a cobertura e a soma por linha conforme detalhado em [Rankings](..\05-funcionalidades\rankings.md).

As diretrizes de publicação e operação permanecem em [Implantação e Produção](..\02-arquitetura\implantacao-e-producao.md).
