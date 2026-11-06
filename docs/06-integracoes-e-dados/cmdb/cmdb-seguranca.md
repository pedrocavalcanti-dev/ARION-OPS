# 16 · Segurança

## Segredo de integração

Integration guarda nome de usuário separado e senha em secret_ciphertext, sem permitir edição direta no admin/model field. CMDBConfigurationForm não devolve valor existente no HTML; deixar senha vazia mantém o valor protegido. _cmdb_client recupera o segredo através de get_secret_protector somente para instanciar o cliente. O provider atual é Windows DPAPI no escopo do usuário Windows corrente (CryptProtectData/CryptUnprotectData); o processo precisa executar sob a mesma conta que protegeu a credencial. O valor cifrado é armazenado em Base64 no campo, mas Base64 não é a proteção criptográfica. O backend de teste reversível não se destina a deployment. DPAPI protege a credencial em repouso no contexto dessa conta; não equivale a KMS nem cifra todo o banco.

## Transporte

O client rejeita URL sem HTTPS, credenciais embutidas, query e fragmento. TLS é verificado por padrão via contexto SSL padrão do Python. Há opção administrativa verify_tls que pode desligar a verificação; use-a apenas quando a decisão de risco e a infraestrutura estiverem formalmente autorizadas. Timeout máximo de 60s reduz espera indefinida. O tráfego usa POST JSON e limite de leitura de 4 MiB.

## Minimização e logs

CMDBClient mantém denylist por nomes sensíveis: password, passwd, pwd, senha, credential, secret, token, auth, cookie, session, apikey, privatekey e identificadores de usuário de portal. O sync usa whitelists explícitas. Usuário PPPoE não tem destino persistente. Logs de sincronização relatam o nome de campos não mapeados, contagens e tipo de erro, não valores de ConfigItem. O fluxo de diagnóstico genérico evita registrar corpo da exceção que poderia incluir segredo.

Telefones, e-mails, CNPJ e URL de portal podem ser atributos operacionais autorizados pelo mapeamento de Operadoras; não são credenciais, mas também não devem ser replicados para documentos públicos, logs ou exemplos reais.

## RBAC e Data Scope

A área Integrações exige permissions próprias para visualizar, configurar, testar, pesquisar e inspecionar amostras CMDB; operações de diagnóstico exigem run_integrations e inspeção de schema/amostras exige inspect_integrations. A documentação usa o controle existente do portal (manage_groups); não cria uma rota paralela. Para dados operacionais, LinkQueryService aplica Data Scope no backend antes de contagens/filtros. No escopo customizado, dimensão de cliente contratante e dimensão de projeto devem se cruzar; superuser é global. Ver a página [Usuários, Grupos e Escopos](../05-funcionalidades/usuarios-grupos-e-escopos.md).

## Estudos de schema

estudar_cmdb faz leitura externa e grava artefatos em docs/estudos; seu relatório deve ser protegido como dado operacional e revisado antes de versionamento/compartilhamento. O comando não foi executado nesta tarefa. Não publicar valores, IDs reais, campos de contato nem CIXMLData de produção.

Ver [Fonte ServiceUp](cmdb-fonte-serviceup.md), [Respostas](cmdb-respostas.md) e [Diagnóstico](cmdb-diagnostico.md).
