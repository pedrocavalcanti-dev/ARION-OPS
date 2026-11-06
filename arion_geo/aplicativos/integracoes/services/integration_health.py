"""Classify the latest OTRS connection outcome without conflating sync errors."""

from aplicativos.integracoes.models import Integration
from aplicativos.integracoes.services.errors import (
    IntegrationNotConfigured,
    OTRSAuthenticationError,
    OTRSConnectionRefusedError,
    OTRSCredentialDecryptionError,
    OTRSCredentialInvalidError,
    OTRSCredentialMissingError,
    OTRSDatabaseNotFoundError,
    OTRSHostUnreachableError,
    OTRSTLSRequiredError,
    OTRSTLSError,
    OTRSTimeoutError,
    OTRSUnavailableError,
)


_CONFIGURATION_FAILURES = (
    IntegrationNotConfigured,
    OTRSCredentialMissingError,
    OTRSCredentialDecryptionError,
    OTRSCredentialInvalidError,
    OTRSAuthenticationError,
    OTRSDatabaseNotFoundError,
    OTRSTLSRequiredError,
    OTRSTLSError,
)
_TRANSPORT_FAILURES = (
    OTRSConnectionRefusedError,
    OTRSHostUnreachableError,
    OTRSTimeoutError,
    OTRSUnavailableError,
)


def status_after_otrs_failure(error, current_status):
    """Return a connection status only when the failure gives clear evidence."""
    if isinstance(error, IntegrationNotConfigured):
        return Integration.Status.NOT_CONFIGURED
    if isinstance(error, _CONFIGURATION_FAILURES):
        return Integration.Status.ATTENTION
    if isinstance(error, _TRANSPORT_FAILURES):
        return Integration.Status.UNAVAILABLE
    # Query/schema and unclassified driver failures describe this operation only.
    return current_status
