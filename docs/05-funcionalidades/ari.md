# ARI

## Estado implementado

ARI está no estado **Preview**. O app persiste conversas, mensagens, anexos e artifacts locais em `AriConversation`, `AriMessage`, `AriAttachment` e `AriArtifact`. `AriConversationService` coordena conversa; `AriSimulationService` produz comportamento demonstrativo. Não afirmar uso de LLM, API externa ou resposta generativa real sem integração ativa verificável.

## Fluxo e URLs

`/ari/` abre a experiência e `/ari/c/<uuid>/` uma conversa. APIs sob `/ari/api/conversations/` criam/alteram conversas e mensagens; há endpoints de rename/delete, download de attachment e acesso a artifacts definidos em `ari/urls.py` e `ari/views.py`. A autorização isola recursos por ownership/usuário.

O conteúdo de Preview deve estar identificado como demonstrativo e não alimenta Dashboard, Chamados ou analytics. ARI não consulta CMDB/OTRS nem modifica registros oficiais.

## Arquivos e segurança

`file_security.py` centraliza regras de upload/serving. O backend permite até cinco anexos por request, com limite de 20 MiB por arquivo e 40 MiB no total; um upload handler aplica a quota enquanto recebe os bytes. Views validam tipo/nome e conferem pertencimento antes de entregar anexo de storage privado. Ações relevantes passam por auditoria. Não anexar conteúdo sensível real de tickets em Preview nem incluir secrets/PII em artifact demonstrativo.

## Limites

Integração com modelo de IA, ferramentas operacionais e automação com decisão permanece **Planejado / Backlog** enquanto não houver código/configuração ativa. Preview não é automação autônoma nem fonte operacional.

## Identidade e estados da interface

A identidade visual da ARI usa um único asset: `arion_geo/static/branding/ari/ari-icon.png`, referenciado nos templates como `{% static 'branding/ari/ari-icon.png' %}`. O PNG tem fundo transparente e sem texto e é compartilhado pelos temas claro e escuro, desktop e mobile, estado vazio, avatar e estado `thinking` do chat. O mesmo arquivo é incluído no cache offline para a ARI funcionar visualmente também no PWA. Não há variantes por tema; o texto `ARI I.A` permanece como identificação junto ao ícone. A ARI continua como módulo do ARION OPS.

O aviso de Preview no topo mantém visível que fontes operacionais não estão conectadas, sem repetir essa informação em cada resposta. Os estados visuais `idle`, `sending`, `thinking`, `responding`, `success` e `error` acompanham o envio real da request e sua resposta ou falha. O indicador usa a resposta simulada atual; a revelação gradual é apenas apresentação no frontend depois que a resposta completa chega, sem streaming nem capacidade operacional adicional. `prefers-reduced-motion` reduz essas animações.
