"""Deterministic, non-mutating business context parser for OTRS ticket titles."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .link_business_identity import (
    BUSINESS_ID_PATTERN,
    BusinessIdentityRules,
    SEPARATOR_PATTERN,
    comparison_key,
    normalized_words,
)

CI_REFERENCE_PATTERN = re.compile(r"\bCI\s*:\s*([A-Z0-9._/-]+)", re.IGNORECASE)
IGNORED_CUSTOMER_DOMAINS = ("brevosend.com",)
IGNORED_CUSTOMER_VALUES = {"CSTI ARION", "ARION", "NOC ARION", "ARION TELECOM"}


@dataclass(frozen=True)
class TicketBusinessIdentity:
    client_raw: str = ""
    client: str = ""
    project_raw: str = ""
    project: str = ""
    occurrence: str = ""
    service: str = ""
    business_id: str = ""
    source: str = "UNKNOWN"
    final_client: str = ""
    ci_reference: str = ""
    is_generic_project: bool = False

    def as_dict(self):
        return {
            "client_raw": self.client_raw,
            "client": self.client,
            "project_raw": self.project_raw,
            "project": self.project,
            "occurrence": self.occurrence,
            "service": self.service,
            "business_id": self.business_id,
            "source": self.source,
            "final_client": self.final_client,
            "ci_reference": self.ci_reference,
            "is_generic_project": self.is_generic_project,
        }


def operator_from_customer_id(customer_id: str) -> tuple[str, str]:
    """Extract corroborative contracting operator from OTRS customer_id metadata.

    Returns (operator_canonical, raw_evidence). Ignores relay/internal addresses
    such as brevosend or CSTI ARION.
    """
    raw = " ".join((customer_id or "").split())
    if not raw:
        return "", ""
    lowered = raw.casefold()
    if any(domain in lowered for domain in IGNORED_CUSTOMER_DOMAINS):
        return "", ""
    words = normalized_words(raw)
    if words in IGNORED_CUSTOMER_VALUES:
        return "", ""
    if "@" in lowered:
        domain = lowered.rsplit("@", 1)[-1]
        if domain in {"telefonica.com", "telefonica.com.br", "vivo.com.br"}:
            return "Vivo", raw
        if domain in {"claro.com.br", "embratel.com.br", "telmex.com.br"}:
            return "Claro", raw
        if domain in {"algar.com.br", "algartelecom.com.br"}:
            return "Algar", raw
        return "", ""
    direct = BusinessIdentityRules.client_for_token(raw, allow_regional=False)
    if direct:
        return direct, raw
    return "", ""


class TicketBusinessIdentityService:
    OCCURRENCES = {
        "RESTAURACAO": "Restauração",
        "ROMPIMENTO": "Rompimento",
        "INDISPONIBILIDADE": "Indisponibilidade",
        "CANCELAMENTO": "Cancelamento",
        "SOLICITACAO": "Solicitação",
    }
    SERVICES = {"INTERNET": "Internet", "DADOS": "Dados", "VOZ": "Voz"}

    @classmethod
    def _split_service_and_project(cls, part: str) -> tuple[str, str]:
        """Handle unseparated service+project phrases like 'INTERNET PROJETO DIVERSOS'."""
        words = normalized_words(part)
        for service_key, service_label in cls.SERVICES.items():
            prefix = service_key + " "
            if words.startswith(prefix):
                remainder = part.strip()[len(service_key):].strip(" -:\t")
                if remainder:
                    return service_label, remainder
        return "", ""

    @classmethod
    def parse(cls, title: str, *, customer_id: str = "") -> TicketBusinessIdentity:
        value = " ".join((title or "").split())
        if not value:
            return TicketBusinessIdentity()

        ci_match = CI_REFERENCE_PATTERN.search(value)
        ci_reference = ci_match.group(1).strip() if ci_match else ""

        match = BUSINESS_ID_PATTERN.search(value)
        business_id = match.group(0).upper() if match else ""
        prefix = value[:match.start()] if match else value
        parts = [part.strip(" \t:;#") for part in SEPARATOR_PATTERN.split(prefix) if part.strip(" \t:;#")]

        raw_client = client = project_raw = project = final_client = occurrence = service = ""
        # Pass 1: inspect segments for explicit client, project, occurrence, service.
        for part in parts:
            words = normalized_words(part)
            candidate = BusinessIdentityRules.client_for_token(part, allow_regional=False)
            if candidate and not raw_client:
                raw_client, client = part, candidate

            corp_client, corp_project = BusinessIdentityRules.corporate_client_match(part)
            if corp_client and corp_project:
                final_client = corp_client
                project_raw, project = part, corp_project
            elif BusinessIdentityRules.project_key(part):
                # Do not let a later generic DIVERSOS overwrite an already detected specific project.
                if not (
                    project
                    and BusinessIdentityRules.is_generic_project(part)
                    and not BusinessIdentityRules.is_generic_project(project)
                ):
                    project_raw, project = part, BusinessIdentityRules.canonical_project(part)

            key = comparison_key(part)
            if key in cls.OCCURRENCES or words in cls.OCCURRENCES:
                occurrence = cls.OCCURRENCES.get(key) or cls.OCCURRENCES.get(words, "")
            if key in cls.SERVICES or words in cls.SERVICES:
                service = cls.SERVICES.get(key) or cls.SERVICES.get(words, "")
            else:
                svc_label, remainder = cls._split_service_and_project(part)
                if svc_label:
                    service = svc_label
                    corp_c, corp_p = BusinessIdentityRules.corporate_client_match(remainder)
                    if corp_c and corp_p:
                        final_client = corp_c
                        project_raw, project = remainder, corp_p
                    elif BusinessIdentityRules.project_key(remainder):
                        if not (
                            project
                            and BusinessIdentityRules.is_generic_project(remainder)
                            and not BusinessIdentityRules.is_generic_project(project)
                        ):
                            project_raw, project = remainder, BusinessIdentityRules.canonical_project(remainder)

        # Pass 2: fallback to regional prefix like CLARO.BA. only if no explicit client was found.
        if not raw_client:
            for part in parts:
                regional = BusinessIdentityRules.regional_operator(part)
                if regional:
                    raw_client, client = part.rstrip("."), regional
                    break

        client_tokens = {normalized_words(part) for part in parts}
        if client == "Claro" and {"CLARO", "TELMEX"}.issubset(client_tokens):
            raw_client = "CLARO / TELMEX"

        source = "OTRS_EXPLICIT_CLIENT" if raw_client else "UNKNOWN"
        if not raw_client and project:
            client = BusinessIdentityRules.project_rule_client(project_raw)
            if client:
                source = "OTRS_PROJECT_RULE"

        if not client and customer_id:
            cust_client, cust_raw = operator_from_customer_id(customer_id)
            if cust_client:
                client = cust_client
                raw_client = cust_raw
                source = "OTRS_CUSTOMER_METADATA"

        is_generic = bool(project and BusinessIdentityRules.is_generic_project(project))
        if not final_client and project and not is_generic:
            final_client = project

        return TicketBusinessIdentity(
            client_raw=raw_client,
            client=client,
            project_raw=project_raw,
            project=project,
            occurrence=occurrence,
            service=service,
            business_id=business_id,
            source=source,
            final_client=final_client,
            ci_reference=ci_reference,
            is_generic_project=is_generic,
        )
