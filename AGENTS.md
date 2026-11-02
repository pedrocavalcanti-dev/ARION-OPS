# ARION OPS — Agent Instructions

## 1. Projeto

ARION OPS é uma plataforma Django de inteligência operacional para o NOC Atacado.

A aplicação centraliza consulta, análise, exploração e visualização de dados operacionais.

### Fonte de verdade

OTRS / CMDB é a fonte oficial dos dados.

O ARION OPS NÃO substitui o OTRS/CMDB e NÃO deve ser tratado como uma segunda fonte oficial.

O banco local funciona como:

- espelho sincronizado;
- cache operacional;
- camada analítica;
- camada de visualização;
- suporte a buscas e agregações.

### Papel do produto

OTRS / CMDB:
fonte oficial e registro técnico.

BI / Relatórios:
reporting periódico, governança e apresentações formais.

ARION OPS:
exploração operacional diária, consulta rápida, visualização e análise interativa.

---

# 2. Stack

- Python 3.12
- Django 5.2
- HTML
- CSS
- JavaScript vanilla
- SQLite em desenvolvimento
- MySQL em produção
- Mapbox GL JS
- Django Templates

Não utilizar sem necessidade:

- React
- Vue
- Angular
- Bootstrap
- Tailwind
- frameworks JS adicionais

CSS e JavaScript devem permanecer separados dos templates sempre que possível.

---

# 3. Estrutura principal

Apps Django:

`arion_geo/aplicativos/`

Templates globais:

`arion_geo/templates/`

Static:

`arion_geo/static/`

Entry point Django:

`arion_geo/gerenciar.py`

Documentação:

`docs/`

Ambiente virtual:

`.venv/`

---

# 4. Regra principal de trabalho

ANTES de alterar código:

1. inspecione os arquivos diretamente relacionados à tarefa;
2. entenda a arquitetura já existente;
3. reutilize services, models, components e padrões existentes;
4. preserve funcionalidades estáveis;
5. faça a menor alteração arquitetural necessária.

NÃO reescreva módulos estáveis apenas porque seria possível fazer diferente.

NÃO recrie funcionalidades que já existem.

NÃO crie sistemas paralelos quando o projeto já possui uma solução equivalente.

---

# 5. Continuidade da execução

Quando uma tarefa for solicitada, trabalhe até concluir o escopo solicitado.

NÃO encerre a execução apenas porque:

- um comando PowerShell falhou;
- quoting falhou;
- regex falhou;
- replace falhou;
- here-string falhou;
- um comando shell apresentou erro de sintaxe;
- uma tentativa de edição não funcionou;
- um teste falhou;
- uma migration apresentou erro corrigível;
- um template apresentou erro corrigível;
- um arquivo exige outra abordagem de edição.

Esses casos NÃO são bloqueios externos.

Corrija e continue.

---

# 6. Fallback obrigatório de edição

Falha de ferramenta NÃO significa falha da tarefa.

Se um método de edição falhar:

1. tente edição direta/incremental;
2. tente outra abordagem;
3. use Python para ler/modificar/escrever o arquivo quando apropriado;
4. divida a alteração em partes menores;
5. altere HTML, CSS e JS separadamente;
6. evite comandos shell gigantes como ponto único de falha.

Nunca dependa de um único `replace` PowerShell para concluir uma alteração importante.

PowerShell é apenas uma ferramenta, não parte da arquitetura do produto.

---

# 7. Não parar em checkpoints parciais

Não envie respostas intermediárias apenas informando:

- “comecei”;
- “adicionei o model”;
- “a migration foi criada”;
- “um teste falhou”;
- “ainda falta...”;
- “vou continuar...”;
- “não consegui terminar esta rodada”.

Quando o problema for corrigível dentro do repositório, corrija e continue.

Responda somente quando:

1. o escopo solicitado estiver concluído; ou
2. existir um bloqueio externo real e impossível de resolver com os arquivos e ferramentas disponíveis.

Exemplos de bloqueio real:

- credencial externa obrigatória indisponível;
- serviço externo inacessível necessário para concluir;
- arquivo essencial inexistente e impossível de reconstruir;
- decisão de negócio impossível de inferir com segurança.

Falha de shell, teste ou edição NÃO é bloqueio externo.

---

# 8. Prioridade: implementação antes de validação

Quando a tarefa solicitar mudança funcional ou visual:

1. implemente a funcionalidade;
2. implemente a interface;
3. revise o resultado;
4. só então execute testes e validações.

Não transforme uma tarefa de produto/UI em uma tarefa apenas de testes.

Testes existem para validar a implementação, não para substituir a implementação.

---

# 9. Interface e design system

Preserve o design atual aprovado do ARION.

Características:

- corporativo;
- clean;
- premium;
- espaçado;
- visual claro;
- hierarquia consistente;
- poucos elementos decorativos;
- bordas suaves;
- sombras discretas;
- ícones pequenos e monocromáticos;
- sem aparência genérica de painel administrativo.

Evitar:

- card soup;
- excesso de caixas;
- excesso de badges;
- glows;
- gradientes desnecessários;
- componentes grandes sem necessidade;
- aparência de template pronto de admin.

---

# 10. Dark / Light

Toda nova interface deve funcionar em:

- tema claro;
- tema escuro.

Reutilize:

- variáveis CSS existentes;
- tokens globais;
- `UserPreference`;
- sistema atual de tema.

NÃO criar um segundo sistema de aparência.

Se existir preferência persistida no banco, ela continua sendo a fonte principal.

---

# 11. HTML / CSS / JavaScript

## HTML

Templates devem priorizar semântica e reutilização.

Quando duas páginas usam o mesmo formulário/componente:

crie partial reutilizável quando adequado.

Evite duplicação.

## CSS

CSS separado.

Evite grandes blocos `<style>` dentro de templates.

Reutilize variáveis e componentes existentes.

## JavaScript

JavaScript vanilla.

JS separado sempre que a funcionalidade for relevante.

Evite grandes `<script>` inline.

Código deve ser:

- simples;
- null-safe;
- previsível;
- sem dependências novas desnecessárias.

---

# 12. Django

Reutilize recursos nativos do Django antes de criar soluções próprias.

Preferir:

- Django Auth;
- Groups;
- Permissions;
- forms;
- validators;
- ORM;
- sessions;
- CSRF;
- decorators/mixins.

Não implementar:

- autenticação própria;
- hash de senha próprio;
- sistema paralelo de permissions;
- gestão manual insegura de sessões.

---

# 13. Segurança de acesso

O projeto utiliza:

RBAC
+
Data Scope

### RBAC

Define:

O QUE o usuário pode fazer.

Base:

- Django Groups
- Django Permissions

### Data Scope

Define:

QUAIS dados o usuário pode visualizar.

Escopo inicial:

- Operadora
- Projeto

Nunca confundir permissão funcional com escopo de dados.

---

# 14. Data Scope

Data Scope deve ser aplicado no BACKEND.

Nunca confiar somente em:

- esconder botão;
- esconder opção de select;
- filtrar no JavaScript.

Se um usuário não possui acesso a determinado dado, uma requisição manual também não pode retornar esse dado.

Data Scope deve ser aplicado antes de:

- filtros;
- agregações;
- paginação;
- cálculo de KPI;
- rankings.

---

# 15. Escopo atual

O escopo suporta:

`ALL`

ou:

`CUSTOM`

CUSTOM utiliza:

- Operadoras permitidas;
- Projetos permitidos.

Quando Operadora + Projeto forem definidos:

o resultado deve representar a interseção adequada.

Não transformar em união sem intenção explícita.

---

# 16. Superuser

Superuser técnico possui acesso global.

Não exigir `UserDataScope` para superuser.

Superuser deve continuar funcionando mesmo após mudanças de RBAC/Data Scope.

---

# 17. Administração

O Django `/admin/` existe apenas para:

- manutenção técnica;
- desenvolvimento;
- emergência.

A administração operacional deve acontecer na Central de Configurações própria do Arion.

---

# 18. Central de Configurações

Estrutura atual planejada:

## Conta

- Minha conta
- Segurança
- Aparência

## Administração

- Usuários
- Grupos e acessos
- Escopos de dados
- Auditoria
- Integrações
- Sistema

Itens administrativos devem respeitar permissions.

Esconder menu NÃO substitui proteção backend.

---

# 19. Usuários e grupos

Não hardcodar comportamento com:

```python
if user.role == "analista":
```

ou:

```python
if username == "...":
```

ou regras equivalentes espalhadas.

Usar Groups e Permissions.

Perfis iniciais:

- Administrador do Sistema
- Gestor
- Analista NOC
- Consulta / Auditoria

---

# 20. Auditoria

Eventos relevantes devem usar o serviço central de auditoria.

Nunca armazenar em logs/auditoria:

- password;
- senha;
- token;
- access_token;
- refresh_token;
- secret;
- client_secret;
- api_key;
- Authorization;
- cookie;
- credenciais.

Metadata deve ser sanitizada.

Auditoria é histórico de eventos importantes, não debug log.

Não registrar eventos irrelevantes como hover ou simples mudança visual de filtro.

---

# 21. Secrets

Nunca enviar segredo completo ao frontend.

Exibir no máximo:

`Configurado`

ou valor mascarado.

Nunca expor:

- DATABASE_URL completa;
- senha do banco;
- API tokens;
- client secrets;
- cookies;
- headers Authorization.

Mesmo administradores não precisam receber secret armazenado.

---

# 22. Dados

## CMDB

Dados reais:

- links;
- ISP;
- UF;
- projeto;
- operadora;
- circuitos;
- dados técnicos presentes na base.

## Tickets

Ainda não integrados como fonte oficial.

Enquanto não houver integração:

métricas operacionais podem utilizar fonte DEMO determinística.

---

# 23. Dados demonstrativos

Dados demo devem ser:

- determinísticos;
- coerentes;
- matematicamente válidos;
- claramente identificados como demonstração.

Nunca apresentar demo como dado oficial.

Exemplos de label:

`Demo`

`Demonstração`

`Performance demonstrativa`

---

# 24. Consistência dos dados

Para o mesmo recorte:

Dashboard
Cockpit
Mapa
Rankings
Base

devem ser consistentes quando utilizarem a mesma fonte/conceito.

Não criar múltiplas fontes demo diferentes para representar a mesma informação.

---

# 25. Performance

Evite:

- N+1 queries;
- consultas repetidas;
- processamento idêntico em várias views;
- carregamento desnecessário de dados completos.

Prefira:

- services;
- agregações compartilhadas;
- `select_related`;
- `prefetch_related`;
- filtros no banco;
- paginação server-side quando adequado.

---

# 26. Banco

Desenvolvimento:

SQLite.

Produção:

MySQL.

Evite escrever código específico de SQLite quando a funcionalidade precisa funcionar em produção.

---

# 27. URLs

Sempre verifique o namespace real antes de usar:

```django
{% url "..." %}
```

Não invente nomes de rota.

Não hardcode URLs internas se existe reverse Django apropriado.

Mudanças em templates globais devem considerar risco de quebrar todas as páginas.

Especial atenção:

- `base.html`;
- header;
- menu;
- dropdown do usuário;
- sidebar.

---

# 28. Anti-regressão de templates globais

Se alterar:

- `base.html`;
- header;
- menu principal;
- dropdown global;
- sidebar global;

valide pelo menos as principais páginas autenticadas.

Um erro global como `NoReverseMatch` não pode derrubar toda a aplicação.

---

# 29. Forms administrativos

Evite controles HTML crus quando o restante da aplicação possui design próprio.

Evitar como interface final:

- `<select multiple>` cru;
- checkbox padrão gigante;
- campos desalinhados;
- listas sem busca quando possuem muitos itens.

Preferir componentes próprios simples:

- switches;
- checkboxes estilizados;
- selectable rows;
- pills;
- search box;
- listas com scroll.

Sem adicionar biblioteca frontend apenas para isso.

---

# 30. Responsividade

Prioridade:

- 1920x1080
- 1440x900
- 1366x768
- 1024x768

Evitar overflow horizontal global.

Interfaces administrativas devem continuar utilizáveis em notebooks comuns.

---

# 31. Acessibilidade básica

Novos componentes devem considerar:

- foco visível;
- labels;
- Tab;
- ESC em dropdown/modal quando apropriado;
- `aria-expanded` quando aplicável;
- contraste adequado.

Não precisa criar infraestrutura complexa de acessibilidade para componentes simples.

---

# 32. Dependências

Evite novas dependências.

Antes de instalar algo:

verifique se Django, Python ou JavaScript vanilla já resolvem.

Não adicionar biblioteca apenas para:

- toggle;
- dropdown;
- modal simples;
- busca local;
- checkbox estilizado.

---

# 33. Migrações

Quando models forem alterados:

- criar migration real;
- não escrever migration manualmente se `makemigrations` resolver;
- evitar migrations vazias;
- verificar pendências.

Nunca apagar migration existente sem necessidade clara.

---

# 34. Validação — ambiente correto

SEMPRE usar o Python da `.venv`.

A partir da raiz do projeto:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py check
```

Testes:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py test
```

Verificar migrations:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py makemigrations --check
```

Quando necessário:

```powershell
.venv\Scripts\python.exe arion_geo\gerenciar.py migrate
```

Não concluir que Django está ausente antes de verificar `.venv`.

---

# 35. Falha de teste

Se um teste falhar:

1. leia o traceback completo;
2. identifique a causa;
3. corrija a implementação;
4. reexecute o teste;
5. continue a tarefa.

Não pare apenas para reportar a primeira falha.

Não altere testes apenas para deixá-los verdes quando a implementação estiver errada.

Se o teste estiver desatualizado em relação à especificação atual, ajuste-o minimamente e de forma justificada.

---

# 36. Smoke tests

Quando alterar elementos globais ou segurança, validar rotas relevantes.

Principais:

- `/painel/`
- `/dashboard/`
- `/cockpit/`
- `/mapa/`
- `/rankings/`
- `/base/`
- `/configuracoes/`

Quando aplicável, validar também rotas administrativas.

---

# 37. Documentação

Leia apenas os documentos necessários em:

`docs/`

NÃO leia toda a pasta automaticamente.

Principais documentos:

- `docs/01-projeto/estado-atual.md`
- `docs/02-arquitetura/arquitetura.md`
- `docs/02-arquitetura/fontes-de-dados.md`
- `docs/04-interface/diretrizes-de-interface.md`
- arquivos específicos da feature em questão

Atualize documentação apenas quando a implementação mudar:

- arquitetura;
- estado atual;
- fonte de dados;
- comportamento importante.

Não transformar documentação em changelog de cada pequena alteração.

---

# 38. CURRENT-STATE

`docs/01-projeto/estado-atual.md` deve representar o estado ATUAL do projeto.

Atualize informação antiga em vez de apenas acumular histórico.

---

# 39. Git

NUNCA executar sem solicitação explícita:

```bash
git add
git commit
git push
```

Também não criar branch automaticamente.

Pode utilizar comandos somente de leitura, quando necessário, como:

```bash
git status
git diff
git diff --check
```

---

# 40. Não apagar trabalho existente

Ao continuar uma tarefa parcialmente executada:

- preserve arquivos já alterados;
- inspecione o diff atual;
- continue a partir dele;
- não reverta trabalho válido sem necessidade.

Não assumir que a versão original do arquivo é a correta.

---

# 41. Escopo da tarefa

Faça somente o que foi solicitado.

Não aproveite uma tarefa pequena para:

- refatorar arquitetura inteira;
- alterar módulos não relacionados;
- redesenhar páginas aprovadas;
- trocar bibliotecas;
- renomear apps;
- reorganizar projeto inteiro.

---

# 42. Tarefas grandes

Se a tarefa for extensa:

execute por partes internamente.

Mas NÃO encerre a resposta em cada subetapa.

Exemplo interno:

1. models;
2. service;
3. UI;
4. JS;
5. testes;
6. docs.

Continue até completar o escopo possível.

---

# 43. Critério de conclusão

Antes de declarar uma tarefa concluída, confirme:

- implementação existe;
- interface solicitada existe;
- lógica funciona;
- rotas resolvem;
- segurança foi preservada;
- check passou;
- testes relevantes passaram;
- não há migration pendente quando aplicável.

Não declare conclusão apenas porque arquivos foram criados.

---

# 44. Entrega final

Ao finalizar, forneça resumo objetivo.

Preferencialmente:

1. o que foi implementado;
2. arquivos/áreas principais alteradas;
3. migrations, se houver;
4. resultado do `check`;
5. resultado dos testes;
6. algum item realmente não concluído, somente se houver bloqueio real.

Evite relatório gigante.

---

# 45. Regra final

Prioridades:

1. preservar estabilidade;
2. concluir a funcionalidade solicitada;
3. manter segurança;
4. manter consistência visual;
5. reutilizar arquitetura existente;
6. validar;
7. documentar somente o necessário.

Falha de comando não é motivo para abandonar a tarefa.

Falha de teste não é motivo para abandonar a tarefa.

Falha de uma abordagem de edição não é motivo para abandonar a tarefa.

Quando houver alternativa segura disponível:

use-a e continue.

# 46. Preservar etapas já aprovadas

Quando uma funcionalidade já tiver sido implementada e aprovada durante a mesma sequência de trabalho:

- NÃO refaça;
- NÃO redesenhe;
- NÃO substitua por outra abordagem;
- NÃO reverta;
- NÃO altere novamente sem necessidade técnica real.

Antes de continuar uma tarefa grande, inspecione o estado atual do repositório e considere o que já foi concluído.

Exemplos atuais da Central de Configurações que devem ser PRESERVADOS:

- RBAC com Django Groups/Permissions;
- Data Scope por Operadora/Projeto;
- escopo `ALL / CUSTOM`;
- formulário compartilhado de criação/edição de usuário;
- switch visual de conta ativa;
- grupos com seleção visual;
- Operadoras com seleção visual;
- Projetos com busca;
- contador de projetos;
- selecionar projetos visíveis;
- limpar seleção;
- dropdown global do usuário;
- avatar por inicial;
- Minha conta / Configurações / Sair em linhas separadas;
- seletor rápido Escuro/Claro;
- persistência de tema via `UserPreference`.

Trate esses componentes como BASE APROVADA.

Faça apenas correções de regressão ou ajustes indispensáveis.

# 47. Continuação de tarefa parcialmente implementada

Quando uma tarefa estiver parcialmente concluída:

1. leia o diff/estado atual;
2. identifique exatamente o que JÁ está pronto;
3. identifique apenas o que falta;
4. continue a partir dali.

Não reinicie a tarefa desde o começo.

Não recrie arquivos que já possuem implementação válida.

Não substitua uma solução funcional apenas porque outra implementação seria possível.

# 48. Validação interrompida

Se um comando de validação foi iniciado, mas o resultado não foi obtido:

não assuma sucesso nem falha.

Execute novamente o comando antes da entrega final.

Exemplo:

se `gerenciar.py check` foi iniciado sem resultado confirmado,
rode novamente e registre o resultado real.

# 49. Interface aprovada primeiro, testes depois

Quando a interface solicitada já estiver implementada:

não continue alterando o visual durante a fase de testes,
exceto para corrigir defeito comprovado.

Depois que o componente visual atingir o resultado solicitado:

1. preserve;
2. integre;
3. teste;
4. corrija regressões;
5. finalize.

# 50. Estado atual da Central de Configurações

A Central de Configurações já possui base funcional de:

CONTA

- Minha conta
- Segurança
- Aparência

ADMINISTRAÇÃO

- Usuários
- Grupos e acessos
- Escopos de dados

Infraestrutura adicional já iniciada:

- app `auditoria` registrado em `INSTALLED_APPS`;
- `AuditEvent`;
- `AuditService`;
- migration inicial de Auditoria;
- permissão de auditoria;
- rotas iniciais de Auditoria;
- rotas iniciais de Integrações;
- rotas iniciais de Sistema.

A continuação deve EVOLUIR esse estado,
não recriá-lo.