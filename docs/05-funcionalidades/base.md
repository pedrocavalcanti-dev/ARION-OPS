# Base de Links

## Papel e rotas

A Base explora ConfigItems espelhados da CMDB. `/base/` alterna Links de Internet e Provedores/ISP (`view=links|providers`); views do app `base_links` consultam mirrors locais. HTTP requests não conectam CMDB/OTRS.

Rotas principais: `/base/`, `/base/provedores/`, `/base/exportar/`. As rotas antigas de importação XLSX não devem ser tratadas como carga operacional habilitada; confirme permissões e resposta das views antes de considerar o fluxo ativo. Atualizações oficiais entram pelo worker CMDB.

## Links de Internet

A listagem usa `CMDBLinkMirror`; Data Scope é aplicado antes de filtros, detalhe, contagens e export. Busca/filtros usam dimensões disponíveis (Cliente, Projeto derivado, Provider/ISP, UF e atributos do Link). Paginação é server-side, com tamanhos 25, 50, 100, 200 e 300. Export CSV é leitura autorizada.

Detalhe usa `ConfigItemID` como chave técnica. Estado/definição do Link vêm da CMDB, não são inferidos do ticket. Um chamado pode se relacionar a vários ConfigItems.

## Providers / ISP

Visão de catálogo `CMDBOperatorMirror`, agregada por FK `CMDBLinkMirror.operator`; nome da entidade é rótulo Provider/ISP. A tela expõe somente campos seguros e agrega links/indicadores locais dentro do scope. Se a resolução falha, permanece não classificado; não usa ID raw como nome nem aproximação ambígua.

O nome histórico `operator` não demonstra por si que a entidade seja operadora contratada. O código atual e o estudo de CMDB identificam o papel apresentado como Provider/ISP.

## Cliente, Projeto e Data Scope

`LinkBusinessIdentityService` preserva nome CMDB original e deriva Cliente/Projeto com source/confiança. Não são campos oficiais independentes. `legacy_project` é campo XLSX histórico, separado, e fallback de escopo somente quando `business_project` está vazio. Provider/ISP vem da entidade CMDB relacionada.

`DataScopeService` limita queryset antes de busca, detalhe, aggregate e export. Ausência de classificação não amplia acesso. Ver [Usuários, Grupos e Escopos](usuarios-grupos-e-escopos.md), [CMDB](..\06-integracoes-e-dados\cmdb.md) e [Dicionário](..\02-arquitetura\dicionario-de-dados.md).

## Proteção e legado

Senha de portal/PPPoE não é persistida; username de portal não pertence ao model atual do mirror de Provider. Não use dados reais de contato em exemplos. Models e importador XLSX continuam para compatibilidade/rollback, mas não alimentam a Base atual.
