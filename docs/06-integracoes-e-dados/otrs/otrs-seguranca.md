# Segurança da integração OTRS

## Conexão e transporte

OTRSReadOnlyClient exige host, usuário e segredo. TLS é padrão para host remoto; mínimo configurado TLS 1.2. ssl_verify=False é recusado para host remoto. CA customizada pode ser informada pelo setting ou variável ARION_OTRS_SSL_CA.

Compatibilidade remota sem TLS só é aceita se o opt-in de insegurança estiver ligado e o host corresponder exatamente ao OTRS_LEGACY_NO_TLS_HOST configurado. Loopback pode usar conexão local sem TLS. Essa exceção não deve ser expandida para um host diferente.

**Observação de revisão:** o código cria contexto SSL e define check_hostname = bool(cafile). Quando nenhuma CA customizada é informada, isso desabilita a checagem de hostname, mesmo que transporte TLS e CERT_REQUIRED permaneçam ativos. A documentação registra esse comportamento; ele não foi alterado nesta tarefa. Ver clients/otrs.py, função _build_ssl_context.

## Credenciais

O segredo de integração é cifrado com Windows DPAPI no escopo do usuário atual. A aplicação precisa executar com a mesma identidade Windows para descriptografar o valor. Host, database, username e TLS são configuração; senha não é guardada no JSON de configuração. O client recebe o segredo em memória e o wrapper de exceção remove username/password de detalhes conhecidos.

Não colocar .env, ciphertext, username, host interno, certificados, DSN, dados de ticket ou capturas de tela com dados pessoais no Markdown. Não há valores reais de configuração nesta referência.

## Escritas e dados sensíveis

Cada conexão executa SET SESSION TRANSACTION READ ONLY. O executor de SELECT proíbe múltiplas instruções, comandos de escrita/efeitos colaterais, SELECT * e colunas sensíveis como senha, sessão, body, artigo e identificador do usuário cliente. Metadados são consultados em information_schema.

Exceções restritas: get_sample_rows gera SELECT * só após descoberta/validação do identificador, bloqueia nomes de tabela com article/customeruser e limita a página a 50 linhas; correlação especializada lê a_body em lote limitado para parser de QID. Esse corpo não é armazenado no mirror, log ou auditoria.

O mirror grava customer_id, user responsável numérico, título/motivo e outros campos da fonte conforme model. Trate esses valores como informação operacional potencialmente identificável. Relatórios e auditoria registram contagens/IDs/códigos seguros; nunca credenciais ou corpo do artigo.

## Acesso interno e grants

A documentação do portal herda login e permission manage_groups, como as páginas atuais do catálogo. Consultas a dados usam Data Scope no backend antes de filtros e agregações. Superuser mantém escopo global pelo comportamento central existente.

SET SESSION READ ONLY é controle da aplicação, não prova de que o usuário MySQL tenha grants mínimos. Grants atuais, rotação de credenciais e cadeia de certificado não foram verificados nesta tarefa.

Referências: clients/otrs.py, clients/factory.py, services/secrets.py, services/errors.py, services/analytics.py e configuracoes/views.py.
