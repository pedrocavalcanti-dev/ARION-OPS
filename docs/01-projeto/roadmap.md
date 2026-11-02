# Roadmap de evolução

Revisado em 02/11/2026. A versão **1.2.0** está registrada no catálogo interno `docs/08-releases/releases.json`. Este documento distingue explicitamente o que já está **IMPLEMENTADO** do que permanece **PLANEJADO**.

## IMPLEMENTADO — Entrega 1.2.0 e Qualidade Operacional (Fases 1 a 4)

- **Identidade visual ARION OPS · NOC ATACADO:** consolidada nos templates, cabeçalho, login institucional, exportações e documentação, com crédito institucional em Sobre o ARION OPS e `NOTICE.md`.
- **PWA e atualizações:** aviso de nova versão persistente em páginas autenticadas, indicador discreto no cabeçalho e ativação controlada pelo usuário.
- **Cockpit:** contextos Suporte, Cancelamentos e Geral, indicadores operacionais, séries reconciliadas e exportações CSV/PDF/XLSX respeitando Data Scope.
- **Rankings:** exposição por links ativos, incidência proporcional (`/100 ativos`) e leituras de risco/desempenho pelo Índice ARION.
- **Central de Relatórios:** módulo Claro Multas com importação, baseline automático, tratativas, pausas, evidências, enriquecimento explícito via espelhos locais e auditoria.
- **Qualidade Operacional (Fases 1–4):** Dashboard gerencial com ECharts local, carteira derivada de Links Prioritários (Ativos, Estabilizados e Todos), fila e detalhe completo de Ordens de Qualidade (Resumo, Ocorrências, Tratativas, Monitoramento RREIP de 30 dias e Histórico), diretório de contatos/escalonamentos e histórico auditado.
- **Sincronização OTRS FAST/FULL:** cobertura das quatro filas operacionais do Cockpit com controle de lease e sinalização de cobertura aplicada.

## PLANEJADO — Próximas evoluções funcionais

- **Automação de abertura de TQ por e-mail (PLANEJADO):** preparação estruturada e envio automatizado de solicitação de Ticket de Qualidade (TQ) a partir da OQ (atualmente toda associação de TQ é manual sobre tickets já existentes no espelho OTRS).
- **Critérios formais e playbooks R1–R5 na Qualidade (PLANEJADO):** formalização operacional definitiva dos critérios de classificação R1–R5 e playbooks orientados por nível.
- **Histórico de movimentação de filas OTRS (PLANEJADO):** materialização de eventos `Move` e `StateUpdate` para medir duração histórica por etapa em Cancelamentos e Suporte.
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
