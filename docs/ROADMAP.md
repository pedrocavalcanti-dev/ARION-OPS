# Roadmap de evolução

Revisado em 30/10/2026. A versão **1.2.0** foi registrada no catálogo interno `docs/releases/releases.json`. Este roadmap mantém as melhorias futuras separadas dos recursos já entregues; publicação no ambiente de destino segue o procedimento de implantação.

## Concluído / 1.2.0

- Consolidada a identidade visual **ARION OPS · NOC ATACADO**, com fallback para os logotipos atuais, crédito institucional e preparação documentada para as futuras artes ARION OPS.
- Refinados o login institucional e o estado persistente do aviso de atualização PWA, mantendo a confirmação explícita do usuário.
- Cockpit com contextos Suporte, Cancelamentos e Geral, indicadores operacionais e dados de cancelamento disponíveis pelo mirror OTRS.
- Rankings com exposição por links ativos, incidência proporcional e leituras de risco/desempenho, mantendo escopo de dados e limites de interpretação.
- Central de Relatórios com Claro Multas, baseline automático, tratativas, pausas, evidências, enriquecimento explícito via mirrors e auditoria.
- FAST/FULL do OTRS com cobertura de Suporte e filas operacionais de Cancelamentos e sinalização da cobertura de sincronização aplicada.
- Catálogo de release atualizado para 1.2.0, consumido pelo endpoint de versão, pela área de Atualizações e pelo PWA.

## Próximas evoluções

- Refinar a experiência mobile após revisão em dispositivos reais.
- Evoluir capacidades da ARI I.A conforme disponibilidade de integrações e dados validados.
- Avançar análises e histórico de Rankings quando houver fonte temporal e regras de negócio aprovadas.
- Materializar eventos de entrada e mudança de fila para melhorar históricos por etapa no Cockpit.
- Avaliar futuras integrações operacionais com fonte, escopo e governança definidos.

## Qualidade Operacional — estado das fases

- Fase 1 — fundação e Dashboard: implementada no workspace.
- Fase 2 — Links prioritários e fila de Ordens de Qualidade: implementada no workspace, com validação Django/migrations ainda pendente do runtime funcional da `.venv`.
- Próxima etapa: detalhe completo da OQ e aprovação operacional das regras de classificação de reincidência e causa raiz.

## Pendências de validação

- Confirmar a definição operacional de ANOP e ETIT com a área responsável; fórmulas não devem ser inventadas.
- Usar indicadores de SLA/penalidade em Rankings somente quando houver regra comparável e fonte formal validada.
- Preservar `UNKNOWN` em Cancelamentos como condição explícita de cobertura.

## Limitações de histórico operacional

- A CMDB disponível representa o estado atual; não há série de snapshots históricos para construir denominadores antigos de Links.
- O mirror OTRS ainda não persiste todos os eventos `Move` e `StateUpdate` como sequência por ticket. Não há duração histórica confiável por etapa.
- No Cockpit, Novos usa `source_created_at`; por isso não mede quando um ticket foi movido para uma fila ou entrou em Cancelamentos.
- A série de backlog depende de datas confiáveis e reconciliação com o snapshot atual; ela não recompõe todos os ciclos de reabertura ou mudanças de fila.
- Tickets sem Provider resolvido e tickets associados a mais de um Provider afetam a cobertura e a soma das linhas; as regras estão descritas em [Rankings](funcionalidades/rankings.md).

O preparo da release não executa sincronização OTRS/CMDB, backfill nem alteração na base de produção. As limitações de implantação permanecem em [Implantação e Produção](IMPLANTACAO-E-PRODUCAO.md).
