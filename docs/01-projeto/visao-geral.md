# Projeto — ARION OPS

> **Identidade visual:** Produto/interface **ARION OPS**. Empresa: **ARION**. Operação atendida: **NOC ATACADO**. Diretório raiz do projeto: `ARION-OPS` (package técnico Django: `arion_geo`).

## Objetivo e público

O **ARION OPS** é uma plataforma Django de inteligência operacional voltada ao **NOC ATACADO**. Reúne a base de links da CMDB, os chamados sincronizados do OTRS e o acompanhamento operacional local da equipe. Dashboard, Chamados, Cockpit, Mapa, Rankings, Qualidade, Base e Central de Relatórios reutilizam espelhos locais para cruzar chamado, Link, Provider/ISP, Cliente, Projeto e UF conforme as limitações das fontes disponíveis.

O sistema reduz a fragmentação entre consultas e facilita a exploração diária de:

- Base de links e provedores;
- Mapa operacional por UF;
- Rankings de provedores/ISP;
- Dashboard e Cockpit analítico;
- Qualidade operacional (reincidências, OQs e ciclos RREIP);
- Cancelamentos, da triagem humana ao acompanhamento oficial, com Dashboard Gerencial e valores informados;
- Central de Relatórios (Claro Multas);
- análise por projeto, cliente, operadora/ISP e comparação temporal.

### O que o ARION OPS não substitui

- **OTRS** (registro oficial de tickets, filas, estados e tratativas);
- **CMDB** (cadastro oficial de links, ConfigItems e operadoras/ISP);
- **BI e relatórios executivos formais** de governança externa.

Usuários reais: analistas NOC, gestores, administradores de sistema e usuários de consulta/auditoria. O acesso funcional deriva de Django Groups e Permissions (RBAC); o Data Scope restringe linhas operacionais no backend. Não há role separada chamada “apresentação”.

## Identidade visual e login

A interface do produto usa a marca **ARION OPS**; ARION identifica a empresa e **NOC ATACADO**, o contexto operacional atendido. O login institucional combina branding, formulário Django e carrossel de três artes. Avisos de nova versão são reservados às páginas autenticadas.

## Desenvolvimento e autoria

Concepção e desenvolvimento: **Pedro Cavalcanti**, para apoio às operações do NOC Atacado da ARION Serviços de Telecomunicações. Produto: **ARION OPS**; contexto operacional: **NOC ATACADO**; empresa: **ARION Serviços de Telecomunicações**. A página autenticada **Sobre o ARION OPS** e o arquivo `NOTICE.md` registram este crédito e a versão ativa do catálogo.

## Fontes oficiais e papel do banco local

OTRS é o registro oficial dos chamados e CMDB é a fonte oficial dos itens de configuração. O ARION OPS não grava alterações nesses sistemas: OTRS é acessado em MySQL somente leitura e CMDB por operações de leitura de ConfigItems (`ConfigItemSearch` e `ConfigItemGet`). O banco local do ARION OPS contém espelhos sincronizados, preferências, Data Scope, auditoria, OQs/monitoramento da Qualidade, apurações da Central de Relatórios e camadas analíticas.

Status ARION, responsável interno, prioridade interna, notas e eventos criados em Chamados pertencem exclusivamente ao ARION OPS e não alteram o ticket no OTRS. A atualidade dos espelhos depende do `integration_worker`; as telas sempre consultam a cópia local disponível.

## Módulos e limites

| Área | Capacidade | Limite |
|---|---|---|
| Dashboard | Indicadores executivos, links, tickets e séries de período. | Distingue estado atual de coortes por data de criação. |
| Chamados | Fila NOC, filtros e acompanhamento interno. | Não altera ticket, artigo ou nota no OTRS. |
| Cockpit | Analytics, contextos Suporte/Cancelamentos/Geral, views salvas e exportações. | Consulta espelhos locais e respeita Data Scope no backend. |
| Mapa e Rankings | Agregações geográficas por UF e análise de Provider/ISP. | Não são relatório contratual de SLA nem prova automática de causalidade. |
| Qualidade | Links prioritários, OQs, TQs locais, ciclos RREIP, contatos e decisão humana final. | Brevo pode enviar solicitação autorizada; correlação QID confirma o ticket externo, sem escrita direta OTRS. |
| Base | Links e Providers/ISP do espelho CMDB. | Importação XLSX antiga é legado e não faz parte do fluxo operacional normal. |
| Relatórios | Claro Multas, baseline automático, pausas, evidências e tratativas. | Cálculo automático é baseline; decisão final é humana e auditada. |
| ARI I.A | Conversas, anexos privados e artefatos em Preview. | Não anunciar integração LLM externa como ativa nesta versão. |
| Administração | Conta, grupos, escopos, integrações, documentação e auditoria. | Todas as operações exigem permissões verificadas no backend. |

## Princípios

- A fonte oficial de tickets e itens de configuração continua sendo OTRS/CMDB; o espelho é projeção local reconstruível.
- Views operacionais não consultam fontes remotas durante requests HTTP de leitura.
- Consultas operacionais aplicam Data Scope no backend. Limites de competências de Relatórios e permissões estão explícitos na [matriz por módulo](../05-funcionalidades/usuarios-grupos-e-escopos.md); não presumir segregação onde ainda não existe.
- Cliente e Projeto derivados do nome CMDB mantêm origem e confiança explícitas.
- Não apresentar métrica operacional interna como SLA contratual ou verdade oficial externa.
- Estudos e contagens pontuais ficam em `docs/07-estudos/`, fora da referência normativa.

Consulte [Estado Atual](estado-atual.md), [Arquitetura](..\02-arquitetura\arquitetura.md), [Fontes de Dados](..\02-arquitetura\fontes-de-dados.md), [Banco de Dados](..\02-arquitetura\banco-de-dados.md), [Chamados](..\05-funcionalidades\chamados.md) e [Implantação e Produção](..\02-arquitetura\implantacao-e-producao.md).
