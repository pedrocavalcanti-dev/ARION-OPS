# 06 · Entidades

## Dois tipos de ConfigItem sincronizados

| Classe na fonte | Modelo local | Interpretação |
|---|---|---|
| Links de internet | CMDBLinkMirror | Circuito/Link técnico consumido pela Base, mapa e análises. |
| Operadoras | CMDBOperatorMirror | Entidade de ISP/Provider à qual um Link pode resolver. |

O nome local operator é histórico do relacionamento. CMDBOperatorMirror é semanticamente Provider/ISP; não comprova a identidade da operadora contratante. A operadora contratante é uma dimensão separada derivada do Name do Link. Não confundir Provider, cliente contratante, projeto, contrato, CustomerID ou Number.

## Identidade dos objetos

ConfigItemID é a chave técnica única em cada classe e o identificador de vínculo com OTRS. Number é único e anulável no mirror quando presente, destinado a busca/apresentação. Name é o nome literal preservado. O parser utiliza Name como entrada sem sobrescrever esse valor.

O sistema documenta duas entidades empresariais espelhadas, não uma entidade local independente para cliente ou projeto. Cliente, projeto, conta e Business ID são colunas derivadas no Link. Business ID não é ConfigItemID nem Number. CustomerID continua como atributo da fonte.

## Campos de projeto de origem diferente

business_project vem da regra atual sobre Name. legacy_project tem origem histórica XLSX e é conservado por ConfigItemID ao sincronizar. O sync CMDB não atualiza legacy_project a partir de planilha. Consumidores podem usar legacy_project como fallback quando business_project está vazio, mas as duas colunas não são intercambiáveis.

## Operadoras e dados operacionais

CMDBOperatorMirror conserva contatos operacionais autorizados pelo mapeamento, horário, categoria, CNPJ e URL de portal. Não tem campos para login e senha do portal. Esse espelho só participa da resolução de ISP e das informações de exibição/filtro; a referência ao Provider não transforma os contatos em credenciais.

## Estados e ciclo de vida

deployment_state e incident_state são valores textuais lidos da versão atual do ConfigItem (Cur*) com fallback para campos sem Cur. O código não estabelece uma enumeração de estados da ServiceUp nem traduz todos os estados para um ciclo de vida oficial. is_present_in_source indica que o item apareceu na última reconciliação completa válida.

Ver [Relacionamentos](cmdb-relacionamentos.md), [Normalização](cmdb-normalizacao.md) e [Glossário](cmdb-glossario.md).
