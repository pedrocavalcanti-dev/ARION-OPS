# Roadmap de evolução

Revisado em 10/11/2026. A versão **1.4.0** está registrada no catálogo interno `docs/08-releases/releases.json`. Este documento distingue o que está **IMPLEMENTADO** no código do que permanece **PLANEJADO**; publicação não equivale a implantação.

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

## PLANEJADO — Próximas evoluções funcionais

- **Central de Relatórios (fase futura):** evoluir o módulo para acompanhar resultados operacionais em tempo real por cliente/contrato e analisar desempenho operacional, SLA, qualidade, reincidência, volume, resultados alcançados e evolução ao longo do tempo. Apoiar também o acompanhamento do desempenho da ARION junto a clientes e contratos, incluindo rankings e indicadores fornecidos por clientes quando houver fonte oficial.

- **Critérios formais e playbooks R1–R5 na Qualidade (PLANEJADO):** formalização operacional definitiva dos critérios de classificação R1–R5 e playbooks orientados por nível.
- **Duração histórica por etapa (PLANEJADO):** ampliar o uso de eventos para métricas homologadas de tempo em etapas de Cancelamentos/Suporte. O cache de estado/fila/procedência usado no backlog histórico de Suporte já está implementado.
- **Evolução da ARI I.A (PLANEJADO):** conexão a provedor LLM aprovado com governança de dados e escopo (atualmente opera em modo Preview local).
- **Histórico temporal de denominadores CMDB (PLANEJADO):** snapshots periódicos da base de links para permitir denominadores históricos em análises retrospectivas de longo prazo.
- **Definições formais ANOP / ETIT e SLA contratual (PLANEJADO):** dependentes de homologação das regras de negócio com a operação antes de qualquer implementação.

## PLANEJADO — Evolução de escalabilidade e infraestrutura

A arquitetura V1 opera como um **monólito modular Django** com Waitress e MySQL local. Conforme o volume de acessos e dados crescer, a evolução planejada prevê etapas progressivas (não implementadas na V1):

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
