class IntegrationError(Exception):
    """Base domain error with a safe message suitable for the UI and history."""

    safe_message = "Não foi possível concluir a operação da integração."

    def __init__(self, *args, error_code="", http_status=None):
        super().__init__(*args)
        self.error_code = error_code
        self.http_status = http_status


class SecretStorageUnavailable(IntegrationError):
    safe_message = "O armazenamento seguro de credenciais não está disponível neste ambiente."


class IntegrationNotConfigured(IntegrationError):
    safe_message = "Configure a integração e as credenciais antes de executar esta operação."


class CMDBAuthenticationError(IntegrationError):
    safe_message = "Falha de autenticação. O servidor CMDB rejeitou as credenciais configuradas."


class CMDBTimeoutError(IntegrationError):
    safe_message = "Tempo limite excedido. O servidor CMDB não respondeu dentro do período configurado."


class CMDBTLSFailure(IntegrationError):
    safe_message = "Falha na validação TLS. Não foi possível validar o certificado apresentado pelo servidor."


class CMDBUnavailableError(IntegrationError):
    safe_message = "Não foi possível alcançar o servidor CMDB."


class CMDBInvalidResponseError(IntegrationError):
    safe_message = "Resposta inválida. O servidor CMDB não retornou o formato esperado."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.http_status == 404:
            self.safe_message = "HTTP 404. Endpoint CMDB não encontrado."


class OTRSAuthenticationError(IntegrationError):
    safe_message = "Falha de autenticação no banco OTRS."


class OTRSCredentialMissingError(IntegrationError):
    safe_message = "Credencial OTRS ausente. Configure usuário e senha para testar a conexão."


class OTRSCredentialDecryptionError(IntegrationError):
    safe_message = "Falha ao descriptografar a credencial OTRS neste usuário do Windows. Salve a senha novamente nesta máquina."


class OTRSCredentialInvalidError(IntegrationError):
    safe_message = "A credencial OTRS descriptografada está vazia ou em formato inválido. Salve a senha novamente."


class OTRSTimeoutError(IntegrationError):
    safe_message = "A conexão com o banco OTRS excedeu o tempo limite."


class OTRSConnectionRefusedError(IntegrationError):
    safe_message = "O host OTRS recusou a conexão na porta configurada."


class OTRSHostUnreachableError(IntegrationError):
    safe_message = "Não foi possível localizar ou alcançar o host configurado para o OTRS."


class OTRSUnavailableError(IntegrationError):
    safe_message = "Servidor do banco OTRS indisponível."


class OTRSDatabaseNotFoundError(IntegrationError):
    safe_message = "O banco OTRS configurado não existe ou não está acessível para esta credencial."


class OTRSDriverError(IntegrationError):
    safe_message = "Erro inesperado do driver MySQL ao conectar ao OTRS. Consulte o tipo do erro no registro técnico."


class OTRSDatabaseError(IntegrationError):
    safe_message = "Não foi possível consultar a estrutura do banco OTRS."


class OTRSTLSRequiredError(IntegrationError):
    safe_message = "Conexão OTRS para host remoto exige TLS configurado ou túnel local em loopback (127.0.0.1)."
