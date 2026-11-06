# 12 · Geolocalização

## Dados guardados pela CMDB

O modelo Link armazena city (Cidade), state (Estado) e address (Endereco) como texto. O sync não geocodifica, não valida CEP, não deriva UF de endereço e não armazena latitude/longitude. Campo ausente fica vazio. O nome interno state representa a coluna local Estado e pode conter código ou nome, conforme a origem.

## Mapa ARION

O mapa consome LinkQueryService.base e agrega Links por UF e dimensões operacionais. O código normaliza acentos/espaços para comparar nomes de unidades federativas e reconhece aliases de sigla e nome. Cidades são normalizadas para consolidar variações de caixa/acentuação na apresentação. O recurso estático brasil_estados.geojson contém geometria de estados; Mapbox pode prover o mapa de apresentação. Essas geometrias não são coordenadas de circuitos e não acrescentam localização de endereço.

Assim, a posição exibida é uma agregação geográfica de UF (e, em algumas visões, contagem por cidade), não um ponto preciso de instalação do Link. Não interpretar um polígono estadual ou uma cidade textual como geocodificação confirmada.

## Limitações e recomendações de leitura

- Links sem state podem não entrar em agregados por UF.
- Valores fora do catálogo brasileiro reconhecido podem não formar uma dimensão de estado válida.
- Cidade sem UF pode ser ambígua e é mantida como texto.
- Não há coordenadas no espelho atual; uma futura camada de geocodificação exigiria fonte, precisão e política de atualização explícitas.
- Mapbox é configuração de visualização; não é fonte dos atributos de localização da CMDB.

Ver [Dicionário](cmdb-dicionario.md), [Módulos consumidores](cmdb-consumidores.md) e [Mapa Operacional](../05-funcionalidades/mapa.md).
