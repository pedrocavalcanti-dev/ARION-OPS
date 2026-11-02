"""Factories for configured, secret-safe integration clients."""

from .otrs import OTRSReadOnlyClient
from ..services.errors import OTRSCredentialDecryptionError, OTRSCredentialMissingError
from ..services.secrets import get_secret_protector


def configured_otrs_client(integration, *, connection_factory=None):
    """Build the existing read-only OTRS client from a configured integration."""
    if not integration.username or not integration.secret_ciphertext:
        raise OTRSCredentialMissingError()
    try:
        password = get_secret_protector().unprotect(integration.secret_ciphertext)
    except Exception:
        raise OTRSCredentialDecryptionError() from None
    config = integration.config or {}
    return OTRSReadOnlyClient(
        host=config.get("host", ""), port=config.get("port", 3306),
        database=config.get("database", ""), username=integration.username,
        password=password, timeout=config.get("timeout", 10),
        use_tls=config.get("use_tls", None),
        ssl_ca=config.get("ssl_ca", ""),
        ssl_verify=config.get("ssl_verify", True),
        connection_factory=connection_factory,
    )
