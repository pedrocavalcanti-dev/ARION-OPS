"""Factories for configured, secret-safe integration clients."""

from .otrs import OTRSReadOnlyClient
from ..services.errors import OTRSCredentialDecryptionError, OTRSCredentialMissingError
from ..services.secrets import get_secret_protector


def normalize_otrs_config(config):
    """Normalize persisted OTRS config, treating null/missing TLS fields safely."""
    raw = config if isinstance(config, dict) else {}
    use_tls = raw.get("use_tls", None)
    ssl_verify = raw.get("ssl_verify", True)
    ssl_ca = raw.get("ssl_ca", "")
    port = raw.get("port", 3306)
    timeout = raw.get("timeout", 10)
    return {
        "host": "" if raw.get("host") is None else str(raw.get("host")).strip(),
        "port": 3306 if port in (None, "") else port,
        "database": "" if raw.get("database") is None else str(raw.get("database")).strip(),
        "timeout": 10 if timeout in (None, "") else timeout,
        "use_tls": None if use_tls is None else bool(use_tls),
        "ssl_ca": "" if ssl_ca is None else str(ssl_ca).strip(),
        "ssl_verify": True if ssl_verify is None else bool(ssl_verify),
    }


def configured_otrs_client(integration, *, connection_factory=None):
    """Build the existing read-only OTRS client from a configured integration."""
    if not integration.username or not integration.secret_ciphertext:
        raise OTRSCredentialMissingError()
    try:
        password = get_secret_protector().unprotect(integration.secret_ciphertext)
    except Exception:
        raise OTRSCredentialDecryptionError() from None
    normalized = normalize_otrs_config(integration.config)
    return OTRSReadOnlyClient(
        host=normalized["host"],
        port=normalized["port"],
        database=normalized["database"],
        username=integration.username,
        password=password,
        timeout=normalized["timeout"],
        use_tls=normalized["use_tls"],
        ssl_ca=normalized["ssl_ca"],
        ssl_verify=normalized["ssl_verify"],
        connection_factory=connection_factory,
    )
