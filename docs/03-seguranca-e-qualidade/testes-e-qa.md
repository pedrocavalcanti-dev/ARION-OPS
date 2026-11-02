# Testes e QA

## Ambiente e comandos

Use Python da `.venv` a partir da raiz:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py check
.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check
.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.configuracoes.test_documentation
```

Testes Django criam banco isolado. Se o usuário MySQL local não tiver grant para `CREATE DATABASE` do banco de teste, rode o teste focado com SQLite em memória apenas naquele processo:

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
.venv\Scripts\python.exe arion_geo\gerenciar.py test aplicativos.configuracoes.test_documentation
Remove-Item Env:DATABASE_URL
```

É uma contingência daquele ambiente, não uma limitação universal do MySQL. Execute check e `makemigrations --check` com a configuração normal.

## Cobertura registrada

- `autenticacao`: login, contas ativas/inativas e sessão.
- `configuracoes`: permissões, preferências, usuários/grupos/escopos, erros globais e portal documental.
- `auditoria`: sanitização, autenticação, mudanças administrativas e export.
- `ari`: conversas, ownership, anexos, validação e artifacts.
- `base_links`: legado XLSX, busca, classificação, escopo, filtros e paginação.
- `painel`: Dashboard/Cockpit, analytics, scope, saved views e export.
- `chamados`: fila e acompanhamento local, views, nota/evento e permissões.
- `mapa`, `rankings`: agregações, APIs e Data Scope.
- `integracoes`: clients simulados, sync/mirrors, semântica, worker, lease e migração de banco.

## QA da Central de Documentação

Teste home, todos os slugs permitidos, login/permissão, metadata title/category/description/search terms, busca por termos de domínio, renderização Mermaid/callouts, anchors, links internos e links inválidos. Mudanças em template global exigem smoke test autenticado de `/painel/`, `/dashboard/`, `/cockpit/`, `/mapa/`, `/rankings/`, `/base/` e `/configuracoes/`. Não é preciso rodar suíte inteira para ajuste documental.

## Troubleshooting

- Django ausente: confirme `.venv\Scripts\python.exe`, não use Python global.
- Sem permissão de criação de DB de teste no MySQL local: configure grant apropriado ou use o override SQLite em memória acima.
- Override persistiu: remova `DATABASE_URL` da sessão após o teste.
- `makemigrations --check` divergiu: avalie model/schema em tarefa própria; documentação não exige migration.
- Erro de rota/template global: revise reverse, URL namespace e as rotas autenticadas listadas acima.

`check` valida configuração Django; `makemigrations --check` verifica estado model/migration; testes focados verificam comportamento. Um comando não substitui o outro.
