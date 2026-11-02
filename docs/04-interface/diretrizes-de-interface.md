# Diretrizes de Interface

## Princípios visuais

Preservar a identidade corporativa aprovada do **ARION OPS**: interface limpa, espaçada, premium, com baixo ruído visual e hierarquia tipográfica consistente (`Inter` ou equivalente do sistema). Utilizar bordas suaves, sombras discretas, ícones pequenos monocromáticos e poucos elementos decorativos. Evitar excesso de caixas ("card soup"), badges desnecessárias, glows exagerados, gradientes ornamentais e aparência genérica de painel administrativo pronto.

## Temas (Claro / Escuro) e arquitetura frontend

Toda tela ou componente deve funcionar integralmente nos temas **claro** e **escuro**, reutilizando as variáveis e tokens CSS globais existentes. A preferência persistida do usuário (`UserPreference`) é a fonte principal do tema.

Manter HTML, CSS e JavaScript separados sempre que possível:

- **HTML:** Django Templates semânticos e parciais reutilizáveis para componentes compartilhados.
- **CSS:** arquivos dedicados em `arion_geo/static/css/`, sem grandes blocos `<style>` inline.
- **JavaScript:** JavaScript vanilla em `frontend_src/js/`, sem introduzir frameworks SPA (React, Vue, Angular) ou bibliotecas de UI redundantes.

Formulários administrativos e operacionais devem apresentar labels claras, alinhamento consistente, foco visível e controles próprios estilizados (switches, pills, listas pesquisáveis), evitando controles nativos crus desalinhados.

## Interação, dados e feedback

- **Interação:** animações discretas e leves, carregamento localizado (sem travar a tela inteira sem necessidade), estados vazios (*empty states*) claros e ausência de overflow horizontal global.
- **Contexto dos dados:** indicadores devem explicitar unidade, período e origem quando aplicável. Registros demonstrativos de desenvolvimento local precisam exibir identificação explícita (`Demo` / `Demonstração`) e nunca parecer dados oficiais. Treatar estados de erro de forma amigável, sem expor tracebacks ou exceções técnicas ao usuário.

## Responsividade e acessibilidade

Viewports de referência prioritários: `1920×1080`, `1440×900`, `1366×768`, `1024×768`, `768 px` e `390 px` (desktop prioritário na operação NOC, com suporte completo a tablet e mobile). Em tabelas extensas, confinar a rolagem horizontal ao contêiner da tabela. Garantir foco visível, navegação por teclado (`Tab`, `Escape` em modais/drawers/popovers), atributos ARIA (`aria-expanded`, `aria-current`, `aria-haspopup`) e contraste adequado, sem comunicar estado exclusivamente por cor.

## Central de Documentação

A Central de Documentação (`arion_geo/templates/documentacao/` e `arion_geo/static/css/documentacao.css`) segue o design system do produto, respeita a preferência persistida de tema e organiza os documentos em categorias recolhíveis, mantendo visível a identidade **ARION OPS · NOC ATACADO**. O portal é renderizado no servidor por um parser Markdown seguro que escapa HTML bruto.

Diagramas de fluxo utilizam SVG inline gerado no servidor a partir de blocos Mermaid suportados, com opção de ampliação em modal (`dialog`). Notas tipadas renderizam callouts semânticos discretos mapeados aos tokens `--docs-*`. Ao registrar um novo documento em `aplicativos/configuracoes/documentation.py`, definir título, descrição, categoria, termos de busca e caminho relativo dentro de `docs/`, validando rotas, busca e links internos.

## Identidade visual ARION OPS

O nome público do produto é **ARION OPS**; **ARION** identifica a empresa e **NOC ATACADO** é o contexto operacional. A paleta institucional utiliza `#00BF9A` (verde), `#FF8440` (laranja) e `#6D6E71` (cinza), centralizados em `arion_geo/static/css/base.css` e `arion_geo/static/css/branding.css`. Os ativos oficiais ficam organizados em:

- `arion_geo/static/branding/arion/`: versões principal, clara e monocromática do logotipo (`arion-ops-logo-light.png` no tema escuro e `arion-ops-logo-primary.png` no tema claro, com fallback automático);
- `arion_geo/static/branding/symbols/`: símbolos oficiais;
- `arion_geo/static/favicon/`: favicons;
- `arion_geo/static/pwa/`: ícones, manifesto e recursos de experiência PWA.

Detalhes geométricos sutis em CSS (recortes diagonais e triângulos de baixa opacidade) são aplicados apenas em áreas institucionais/cabeçalhos aprovados, sempre com `pointer-events: none` e nunca sobre gráficos, tabelas, cards de dados ou mapas.

O crédito institucional (**Concepção e desenvolvimento: Pedro Cavalcanti**) consta na página **Sobre o ARION OPS**, em `README.md` e em `NOTICE.md`, sem marcas d'água sobre as telas operacionais.

## Login e atualizações PWA

Preservar a composição institucional do login **ARION OPS** (formulário Django e carrossel de três artes) sem exibir avisos de versão na tela de autenticação. Nas páginas autenticadas, o aviso de atualização PWA é compacto e aguarda ação do usuário: **Depois** dispensa o banner da versão corrente mantendo o indicador global no cabeçalho, enquanto uma nova release volta a sinalizar normalmente.
