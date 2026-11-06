# 08 · Normalização

## Extração do payload

O serviço separa campos diretos e CIXMLData. O objeto dinâmico pode ser dicionário recursivo ou XML; a transformação achata folhas, remove namespace e compara nomes sem distinção de caixa. Se um campo direto está vazio, tenta o atributo. Valores textuais são limpos, aparados e limitados ao tamanho da coluna; data de origem é parseada por source_datetime. Campo ausente não é inferido a partir de outro conceito sem regra explícita.

## Identidade de cliente/projeto no Name

LinkBusinessIdentityService preserva name e separa tokens usando hífen, meia-risca e travessão. Para comparação remove diacríticos, normaliza espaços e caixa; o texto canônico fica nas dimensões derivadas.

| Dimensão | Regras confirmadas no parser |
|---|---|
| Cliente | Claro/TELMEX → Claro; Vivo/Telefônica → Vivo; Algar → Algar. Os aliases são normalizados sem acentos e caixa. |
| Projeto | Aliases codificados incluem Correios, Banco do Brasil, Caixa/CEF, Carrefour, Bradesco/BRAD, FUST, L2L, Arcos Dourados/McDonald’s, Escolas Conectadas e Diversos. |
| Conta | Só recebe um segmento remanescente quando a composição permite identificar exatamente um. |
| Business ID | Primeiro token BD ou BL que contenha ao menos um dígito; o resultado é convertido a maiúsculas. |
| Alias local | ARION_LINK_CLIENT_ALIASES pode alterar o nome canônico de um cliente explicitamente reconhecido; JSON inválido é ignorado. |

Quando o nome não identifica diretamente um cliente, as regras explícitas de projeto inferem a dimensão apenas para projetos associados:

| Projeto reconhecido | Cliente inferido |
|---|---|
| Correios, Banco do Brasil, Caixa Econômica/CEF, Carrefour | Claro |
| FUST, L2L, Arcos Dourados e aliases McDonald’s | Vivo |
| Escolas Conectadas | Algar |
| BRAD/Bradesco, Diversos e variantes | Sem inferência |

O fallback de projeto adjacente só ocorre com cliente explícito. Não trata prefixos regionais CLARO.UF nem TELMEX como projeto. Cliente, projeto e Business ID não são identificadores de origem.

Confiança é HIGH quando há cliente, projeto e ID; MEDIUM quando ao menos uma dimensão foi reconhecida; LOW quando nenhuma foi reconhecida ou quando a origem é UNKNOWN. A fonte pode ser CMDB_EXPLICIT_CLIENT, CMDB_PROJECT_RULE ou UNKNOWN. A classificação do parser é recalculável pelo comando classificar_identidade_cmdb; tal comando altera somente colunas derivadas locais.

## Resolução do ISP

ISP é preservado em isp_raw. O sync prefere correspondência exata por ConfigItemID de Operadoras. Como fallback, normaliza caixa e exige exatamente um nome igual. Sem match ou com nome ambíguo, não escolhe arbitrariamente. Serviços de exibição podem resolver referências legadas quando ISP armazena um identificador local, mas essa compatibilidade não muda a regra de escrita principal.

## Semântica de projeto antigo

legacy_project é carregado do valor já existente no mirror, por ConfigItemID, e conservado no upsert. project_source identifica LEGACY_XLSX. Alguns consumidores usam business_project ou, se ausente, legacy_project. Data Scope usa a mesma precedência para não ampliar um escopo quando existe classificação nova.

Ver [Dicionário](cmdb-dicionario.md), [Entidades](cmdb-entidades.md) e [Relacionamentos](cmdb-relacionamentos.md).
