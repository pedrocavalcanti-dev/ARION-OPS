# Documentação do ARION OPS

Esta pasta é a fonte Markdown oficial da Central de Documentação em `/configuracoes/documentacao/`.

- **Produto:** ARION OPS
- **Contexto operacional:** NOC ATACADO
- **Empresa:** ARION Serviços de Telecomunicações
- **Concepção e desenvolvimento:** Pedro Cavalcanti

OTRS e CMDB permanecem como fontes oficiais nos seus domínios; o banco do ARION OPS mantém espelhos sincronizados (somente leitura na origem), camada analítica e registros operacionais próprios.

## 01. Projeto

- [Visão geral](01-projeto/visao-geral.md)
- [Estado atual](01-projeto/estado-atual.md)
- [Roadmap](01-projeto/roadmap.md)

## 02. Arquitetura

- [Arquitetura](02-arquitetura/arquitetura.md)
- [Banco de dados](02-arquitetura/banco-de-dados.md)
- [Fontes de dados](02-arquitetura/fontes-de-dados.md)
- [Dicionário de dados](02-arquitetura/dicionario-de-dados.md)
- [Worker e sincronização](02-arquitetura/worker-e-sincronizacao.md)
- [Implantação e produção](02-arquitetura/implantacao-e-producao.md)

## 03. Segurança e qualidade

- [Segurança](03-seguranca-e-qualidade/seguranca.md)
- [Testes e QA](03-seguranca-e-qualidade/testes-e-qa.md)

## 04. Interface

- [Diretrizes de interface](04-interface/diretrizes-de-interface.md)

## 05. Funcionalidades

- [Autenticação e sessão](05-funcionalidades/autenticacao.md)
- [Dashboard](05-funcionalidades/dashboard.md)
- [Chamados](05-funcionalidades/chamados.md)
- [Cancelamentos](05-funcionalidades/cancelamentos.md)
- [Dashboard de Cancelamentos](05-funcionalidades/cancelamentos-dashboard.md)
- [Migra??o hist?rica ArionBroker](05-funcionalidades/cancelamentos-migracao-arionbroker.md)
- [Cockpit](05-funcionalidades/cockpit.md)
- [Mapa operacional](05-funcionalidades/mapa.md)
- [GEO Inteligência](05-funcionalidades/geo-intelligence.md)
- [GEO Inteligência · Nacional e Local](05-funcionalidades/geo-intelligence-nacional-local.md)
- [Rankings](05-funcionalidades/rankings.md)
- [Qualidade operacional](05-funcionalidades/qualidade.md)
- [Base de links](05-funcionalidades/base.md)
- [Central de relatórios](05-funcionalidades/relatorios.md)
- [ARI I.A](05-funcionalidades/ari.md)
- [Configurações](05-funcionalidades/configuracoes.md)
- [Integrações](05-funcionalidades/integracoes.md)
- [Auditoria](05-funcionalidades/auditoria.md)
- [Usuários, grupos e escopos](05-funcionalidades/usuarios-grupos-e-escopos.md)
- [Tratamento global de erros](05-funcionalidades/tratamento-global-de-erros.md)

## 06. Integrações e dados

- [CMDB](06-integracoes-e-dados/cmdb.md)
- [OTRS](06-integracoes-e-dados/otrs.md)
- [Mapeamento CMDB e OTRS](06-integracoes-e-dados/mapeamento-cmdb-otrs.md)
- [Carga futura de Cancelamentos em produ??o](06-integracoes-e-dados/arionbroker/carga-producao.md)
- [Nomes hist?ricos](06-integracoes-e-dados/arionbroker/usuarios-historicos.md)
- [Transfer?ncia completa para candidato MySQL](06-integracoes-e-dados/migracao-completa-arion-mysql.md)

## 07. Estudos

Snapshots e levantamentos técnicos datados (não substituem os contratos normativos acima):

- [Estudo de rankings — outubro/2026](07-estudos/rankings-outubro-2026.md)
- [Campos CMDB](07-estudos/campos-cmdb.md)
- [Estudo real CMDB](07-estudos/estudo-real-cmdb.md)
- [Estrutura OTRS](07-estudos/estrutura-otrs.md)
- [Estudo real OTRS](07-estudos/estudo-real-otrs.md)
- [Motivos de fechamento OTRS](07-estudos/motivos-de-fechamento-otrs.md)
- [NOC Atacado — campos e semântica](07-estudos/noc-atacado-campos-e-semantica.md)
- [Patch semanal 2026-W44](07-estudos/patch-semanal-2026-w44.md)

## 08. Releases

- [Versão 1.4.1 — GEO Inteligência, Qualidade e Cancelamentos](08-releases/v1.4.1.md)
- [Versão 1.4.1 — Checklist de implantação Windows Server](08-releases/v1.4.1-implantacao-windows.md)
- [Consolidação DEV da candidata 1.4.1](08-releases/v1.4.1-candidata.md)
- [Versão 1.4.0 — Nota técnica](08-releases/v1.4.0.md)
- [Catálogo de versões (`releases.json`)](08-releases/releases.json)

## 09. Tarefas

- [Registro de tarefa atual](09-tarefas/atual.md)
