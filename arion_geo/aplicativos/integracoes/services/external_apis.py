"""Runtime resolvers for externally managed API credentials."""
from django.conf import settings

from aplicativos.integracoes.models import ExternalAPIIntegration

from .secrets import get_secret_protector


def decrypt_external_api_secret(integration):
    if not integration.secret_ciphertext:
        return ""
    return get_secret_protector().unprotect(integration.secret_ciphertext)


def decrypt_external_api_smtp_secret(integration):
    """Recover the separately encrypted SMTP credential without mixing REST keys."""
    if not integration.smtp_secret_ciphertext:
        return ""
    return get_secret_protector().unprotect(integration.smtp_secret_ciphertext)


def is_public_mapbox_token(value):
    token = str(value or "").strip()
    return token.startswith("pk.") and len(token) > 10 and not any(character.isspace() for character in token)


def get_mapbox_public_token():
    """Use an explicit database record first; only an absent record permits env fallback."""
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.MAPBOX).first()
    if integration is not None:
        token = (integration.config or {}).get("public_token", "")
        if integration.enabled and is_public_mapbox_token(token):
            return token
        return ""
    legacy_token = getattr(settings, "MAPBOX_PUBLIC_TOKEN", "")
    return legacy_token if is_public_mapbox_token(legacy_token) else ""


def get_mapbox_runtime_state():
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.MAPBOX).first()
    if integration is not None:
        token = (integration.config or {}).get("public_token", "")
        configured = is_public_mapbox_token(token)
        if token and not configured:
            status = "Erro: token público inválido"
        elif not configured:
            status = "Não configurada"
        elif integration.enabled:
            status = "Disponível"
        else:
            status = "Configurada, desabilitada"
        return {
            "configured": configured,
            "enabled": integration.enabled,
            "source": "database",
            "status": status,
            "integration": integration,
        }
    token = getattr(settings, "MAPBOX_PUBLIC_TOKEN", "")
    configured = is_public_mapbox_token(token)
    return {
        "configured": configured,
        "enabled": configured,
        "source": "environment" if configured else "none",
        "status": "Disponível (configuração legada)" if configured else "Não configurada",
        "integration": None,
    }
