# ARION OPS

Plataforma de inteligência operacional para o **NOC ATACADO** da ARION Serviços de Telecomunicações.

## Visão geral

O **ARION OPS** centraliza a exploração operacional diária dos circuitos sincronizados da CMDB, dos chamados operacionais do OTRS e dos fluxos internos de acompanhamento, análise, qualidade e apuração de multas do NOC Atacado.

O sistema opera sobre espelhos locais sincronizados em modo somente leitura a partir das fontes oficiais, mantendo a navegação rápida e desacoplada da latência dos sistemas externos.

## Objetivo

Reduzir a fragmentação entre consultas operacionais e oferecer visão consolidada de fila, impacto, reincidência, distribuição geográfica, desempenho de provedores/ISP e apurações gerenciais, **sem substituir** o OTRS ou a CMDB como sistemas de registro técnico oficial.

## Módulos

- **Dashboard (`/dashboard/`):** visão executiva de links, chamados e séries temporais.
- **Chamados (`/chamados/`):** fila operacional do NOC com acompanhamento interno (`TicketTracking`), notas, prioridade local e histórico.
- **Cockpit (`/cockpit/`):** motor analítico com contextos Suporte, Cancelamentos e Geral, views salvas e exportações CSV/PDF/XLSX.
- **Mapa (`/mapa/`):** distribuição geográfica por UF, modos operacionais e detalhamento estadual com Mapbox GL JS.
- **Rankings (`/rankings/`):** análise comparativa de provedores/ISP, exposição por links ativos, incidência proporcional e Índice ARION.
- **Qualidade (`/qualidade/`):** gestão de reincidências (episódios de 30 dias), carteira de Links Prioritários, Ordens de Qualidade (OQs), ciclos RREIP, contatos e histórico.
- **Base (`/base/`):** consulta e exportação do catálogo de Links e Provedores/ISP espelhados da CMDB.
- **Central de Relatórios (`/relatorios/`):** apuração de Claro Multas com baseline automático, pausas, evidências, enriquecimento controlado e auditoria.
- **ARI I.A (`/ari/`):** ambiente de conversas, anexos privados e artefatos em modo Preview local.
- **Configurações e Integrações (`/configuracoes/`):** conta, aparência (tema claro/escuro), usuários, grupos/RBAC, escopos de dados (Data Scope), conectores CMDB/OTRS, auditoria e Central de Documentação.

## Arquitetura

```text
Usuário
   ↓
HTTPS / Cloudflare
   ↓
Waitress (127.0.0.1)
   ↓
Django (arion_geo)
   ↓
MySQL local
   ↓
Espelhos locais
├── CMDB (ServiceUp Search/Get — somente leitura)
└── OTRS (MySQL remoto — somente leitura)
```

O diretório raiz do repositório é `ARION-OPS/`, enquanto o package técnico Django permanece em `arion_geo/` por estabilidade de imports, migrations e scripts operacionais.

## Tecnologias

- **Backend:** Python 3.12, Django 5.2, Waitress (WSGI), WhiteNoise
- **Banco de dados:** MySQL 8 (padrão operacional e produção) / SQLite (contingência local)
- **Frontend:** Django Templates, HTML5, CSS3 modular, JavaScript vanilla, Apache ECharts, Mapbox GL JS
- **Build de assets:** Node.js 24 / npm (`esbuild` + `javascript-obfuscator`)
- **Entrega e borda:** PWA (`manifest.webmanifest` + Service Worker) e Cloudflare Tunnel (`cloudflared`)

## Fontes de dados

- **CMDB (fonte oficial de cadastro):** links, Designação, ConfigItem, cliente, projeto, ISP/provedor, UF, velocidade e situação cadastral.
- **OTRS (fonte oficial de chamados):** tickets, fila, estado oficial, responsável na origem, motivos de fechamento, datas e relações Ticket–ConfigItem.
- **ARION OPS (fonte de dados locais):** preferências de usuário, RBAC/Data Scope, acompanhamento local da fila, auditoria, Ordens de Qualidade (OQs), classificações da Qualidade, ciclos RREIP e relatórios internos.

## Pré-requisitos

- Windows 10/11 ou Windows Server
- Python 3.12 com ambiente virtual em `.venv/`
- MySQL 8 local em execução
- Node.js 24 e npm (para build de produção do frontend)

## Configuração do ambiente

1. Crie e ative o ambiente virtual Python na raiz do projeto (`ARION-OPS`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-prod.txt
```

2. Copie `.env.example` para `.env` e preencha as variáveis locais (sem versionar segredos):

```powershell
Copy-Item .env.example .env
```

3. Instale as dependências de build do frontend:

```powershell
npm ci
```

## Banco de dados

O ARION OPS utiliza **MySQL local** como banco operacional principal. A configuração ativa fica armazenada localmente em `var/database-config.json` (com credenciais protegidas via DPAPI no Windows) ou variáveis de ambiente, nunca versionadas no Git. Em produção (`ARION_ENV=production`), o uso de MySQL local com usuário próprio (não `root`) é obrigatório.

## Execução local

Verifique a integridade do projeto e inicie o servidor de desenvolvimento a partir da raiz:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py check
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py runserver
```

## Build do frontend

Os fontes JavaScript ficam em `frontend_src/js/`. Para gerar os bundles minificados, ofuscados e com fingerprint em `arion_geo/static/arion_build/` e atualizar `var/arion-frontend-manifest.json`:

```powershell
npm run build
```

## Migrations

Para verificar se existem alterações de models pendentes ou aplicar migrations:

```powershell
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py migrate
```

## Sincronização CMDB/OTRS

A sincronização roda em processo separado do servidor web:

```powershell
# Worker contínuo (OTRS FAST 2 min / OTRS FULL 30 min / CMDB FULL 10 min)
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py integration_worker

# Execução manual controlada
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py sincronizar_cmdb --apply
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py sincronizar_otrs --full --apply
.\.venv\Scripts\python.exe .\arion_geo\gerenciar.py sincronizar_otrs --fast --apply
```

## Deploy

Os scripts operacionais para Windows e Cloudflare Tunnel estão em `deploy/windows/`:

- `deploy\windows\install-arion.ps1`: instala dependências de frontend, executa build de produção, `collectstatic` e `production-check`.
- `deploy\windows\production-check.ps1`: valida requisitos de segurança e configuração de produção sem exibir segredos.
- `deploy\windows\install-tasks.ps1`: registra as tarefas agendadas `ARION-Web` e `ARION-Integration-Worker`.
- `deploy\windows\start-arion.ps1` / `stop-arion.ps1` / `restart-arion.ps1` / `status-arion.ps1`: controle do supervisor local.
- `deploy\windows\backup-arion.ps1`: gera dump completo do MySQL fora da pasta web.

Consulte o roteiro completo em [Implantação e Produção](docs/02-arquitetura/implantacao-e-producao.md).

## Segurança

- **RBAC + Data Scope:** permissões funcionais via Django Groups/Permissions e recorte de dados (Operadora/Cliente × Projeto por interseção) imposto no backend antes de filtros, cálculos, paginação e exportações.
- **Proteção de segredos:** credenciais de banco e conectores protegidas via DPAPI vinculada ao usuário de serviço Windows; `SECRET_KEY`, senhas e tokens jamais são expostos em UI, logs ou auditoria.
- **Somente leitura nas origens:** nenhuma operação do ARION OPS altera dados no OTRS ou na CMDB.

Detalhes em [Segurança](docs/03-seguranca-e-qualidade/seguranca.md).

## Documentação

Toda a documentação oficial está em Português do Brasil no diretório `docs/` e disponível na aplicação em `/configuracoes/documentacao/`.

- **Portal da documentação:** [docs/indice.md](docs/indice.md)
- **Visão geral do projeto:** [docs/01-projeto/visao-geral.md](docs/01-projeto/visao-geral.md)
- **Arquitetura:** [docs/02-arquitetura/arquitetura.md](docs/02-arquitetura/arquitetura.md)
- **Fontes de dados:** [docs/02-arquitetura/fontes-de-dados.md](docs/02-arquitetura/fontes-de-dados.md)
- **Diretrizes de interface:** [docs/04-interface/diretrizes-de-interface.md](docs/04-interface/diretrizes-de-interface.md)

## Estado atual

Versão ativa no catálogo (`docs/08-releases/releases.json`): **1.2.0**. Consulte a matriz completa do que está **IMPLEMENTADO** e do que permanece **PLANEJADO** em:

- [Estado atual](docs/01-projeto/estado-atual.md)
- [Roadmap](docs/01-projeto/roadmap.md)

## Autoria

Concepção e desenvolvimento: **Pedro Cavalcanti**
