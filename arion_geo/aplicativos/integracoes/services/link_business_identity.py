"""Central rules for business identity parsed from CMDB and OTRS titles."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass


SOURCE = "CMDB_NAME_ORIGINAL"
BUSINESS_ID_PATTERN = re.compile(r"\b(?:BDL?|BL|IPSD)[A-Z0-9]*\d[A-Z0-9]*\b", re.IGNORECASE)
SEPARATOR_PATTERN = re.compile(r"\s*[-\u2013\u2014]\s*")
REGIONAL_OPERATOR_PATTERN = re.compile(
    r"^(CLARO|VIVO|ALGAR)\.(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)(?:\.|$)",
    re.IGNORECASE,
)
INTERNAL_ARION_POP_PATTERN = re.compile(r"\b(?:POP\s+ARION|ARION\s+POP)\b", re.IGNORECASE)
INTERNAL_ARION_INFRA_PATTERN = re.compile(
    r"\b(?:BACKBONE\s+ARION|ARION\s+BACKBONE|NOC\s+ARION|ARION\s+NOC|INFRA(?:ESTRUTURA)?\s+ARION|LINK\s+INTERNO\s+ARION)\b",
    re.IGNORECASE,
)
INTERNAL_ARION_EXCLUDE_PATTERN = re.compile(
    r"\b(?:CNPJ\s+ARION|GESTORES\s+COMERCIAL|CARTEIRA\s+DE\s+CLIENTES)\b",
    re.IGNORECASE,
)


def comparison_key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(ch for ch in folded if not unicodedata.combining(ch)).upper().split())


def normalized_words(value: str) -> str:
    """Accent-folded uppercase words with punctuation replaced by spaces."""
    key = comparison_key(value)
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", key).split())


class BusinessIdentityRules:
    """Single source for client precedence, project identity and display labels."""

    CLIENTS = {
        "CLARO": "Claro",
        "CLARO S A": "Claro",
        "CLARO SA": "Claro",
        "CLARO BRASIL": "Claro",
        "CLARO TELMEX": "Claro",
        "LINK CLARO": "Claro",
        "LINKS EMBRATEL CLARO": "Claro",
        "EMBRATEL CLARO": "Claro",
        "TELMEX": "Claro",
        "TELMEX DO BRASIL": "Claro",
        "TELMEX DO BRASIL S A": "Claro",
        "TELMEX DO BRASIL SA": "Claro",
        "VIVO": "Vivo",
        "VIVO S A": "Vivo",
        "VIVO SA": "Vivo",
        "VIVO DIVERSOS": "Vivo",
        "LINK VIVO": "Vivo",
        "TELEFONICA": "Vivo",
        "TELEFONICA BRASIL": "Vivo",
        "TELEFONICA BRASIL S A": "Vivo",
        "TELEFONICA BRASIL SA": "Vivo",
        "TELEFONICA DO BRASIL": "Vivo",
        "TELEFONICA DO BRASIL S A": "Vivo",
        "TELEFONICA DO BRASIL SA": "Vivo",
        "TELEFONICA DATA": "Vivo",
        "ALGAR": "Algar",
        "ALGAR TELECOM": "Algar",
        "ALGAR TELECOM S A": "Algar",
        "ALGAR TELECOM SA": "Algar",
        "PROJETO ALGAR": "Algar",
    }
    PROJECT_ALIASES = {
        "CORREIOS": "Correios",
        "PROJETO CORREIOS": "Correios",
        "BANCO DO BRASIL": "Banco do Brasil",
        "PROJETO BB": "Banco do Brasil",
        "BB": "Banco do Brasil",
        "CAIXA ECONOMICA": "Caixa Econômica Federal",
        "CAIXA ECONOMICA FEDERAL": "Caixa Econômica Federal",
        "CEF": "Caixa Econômica Federal",
        "CARREFOUR": "Carrefour",
        "PROJETO CARREFOUR": "Carrefour",
        "BRAD": "Bradesco",
        "BRADESCO": "Bradesco",
        "PROJETO BRADESCO": "Bradesco",
        "BDN": "Bradesco",
        "BDN BRADESCO": "Bradesco",
        "BANCO BRADESCO": "Bradesco",
        "BANCO BRADESCO S A": "Bradesco",
        "BANCO BRADESCO SA": "Bradesco",
        "FUST": "Fust",
        "PROJETO FUST": "Fust",
        "FUST UBS": "Fust",
        "L2L": "L2L",
        "ARCOS DOURADOS": "Arcos Dourados",
        "MC DONALDS": "Arcos Dourados",
        "MCDONALDS": "Arcos Dourados",
        "MCDONALD S": "Arcos Dourados",
        "MCDONALD'S": "Arcos Dourados",
        "ESCOLAS CONECTADAS": "Escolas Conectadas",
        "SANTANDER": "Santander",
        "BANCO SANTANDER": "Santander",
        "BANCO SANTANDER BRASIL S A": "Santander",
        "BANCO SANTANDER BRASIL SA": "Santander",
        "DIVERSOS": "Diversos",
        "PROJETO DIVERSOS": "Diversos",
        "PROJETOS DIVERSOS": "Diversos",
    }
    # Corporate accounts/subsidiaries explicitly mapped to (final_client, canonical_project).
    # Never forces contracting operator without independent confirmation.
    CORPORATE_CLIENT_MAP = {
        "BANCO CSF": ("Banco CSF", "Carrefour"),
        "BANCO CSF S A": ("Banco CSF", "Carrefour"),
        "BANCO CSF SA": ("Banco CSF", "Carrefour"),
        "CARREFOUR SOLUCOES FINANCEIRAS": ("Banco CSF", "Carrefour"),
    }
    PROJECT_CLIENT = {
        "CORREIOS": "Claro",
        "PROJETO CORREIOS": "Claro",
        "BANCO DO BRASIL": "Claro",
        "PROJETO BB": "Claro",
        "BB": "Claro",
        "CAIXA ECONOMICA": "Claro",
        "CAIXA ECONOMICA FEDERAL": "Claro",
        "CEF": "Claro",
        "CARREFOUR": "Claro",
        "PROJETO CARREFOUR": "Claro",
        "FUST": "Vivo",
        "PROJETO FUST": "Vivo",
        "L2L": "Vivo",
        "ARCOS DOURADOS": "Vivo",
        "MC DONALDS": "Vivo",
        "MCDONALDS": "Vivo",
        "MCDONALD S": "Vivo",
        "MCDONALD'S": "Vivo",
        "ESCOLAS CONECTADAS": "Algar",
    }
    SHARED_PROJECTS = {
        "BRAD", "BRADESCO", "PROJETO BRADESCO", "BDN", "BDN BRADESCO",
        "BANCO BRADESCO", "BANCO BRADESCO S A", "BANCO BRADESCO SA",
        "FUST UBS",
        "SANTANDER", "BANCO SANTANDER", "BANCO SANTANDER BRASIL S A", "BANCO SANTANDER BRASIL SA",
        "DIVERSOS", "PROJETO DIVERSOS", "PROJETOS DIVERSOS",
    }
    GENERIC_PROJECTS = {"DIVERSOS", "PROJETO DIVERSOS", "PROJETOS DIVERSOS"}
    MAJOR_OPERATORS = ("Claro", "Vivo", "Algar")
    INTERNAL_OPERATOR_LABEL = "ARION"

    @classmethod
    def regional_operator(cls, token: str) -> str:
        match = REGIONAL_OPERATOR_PATTERN.match(comparison_key(token))
        if not match:
            return ""
        return cls.CLIENTS.get(match.group(1).upper(), "")

    @classmethod
    def is_regional_token(cls, token: str) -> bool:
        return bool(REGIONAL_OPERATOR_PATTERN.match(comparison_key(token)))

    @classmethod
    def client_for_token(cls, token: str, *, allow_regional: bool = False) -> str:
        key = comparison_key(token)
        words = normalized_words(token)
        if key in cls.CLIENTS:
            return cls.CLIENTS[key]
        if words in cls.CLIENTS:
            return cls.CLIENTS[words]
        if words.startswith("TELEFONICA BRASIL") or words.startswith("TELEFONICA DO BRASIL"):
            return "Vivo"
        if words.startswith("TELMEX DO BRASIL"):
            return "Claro"
        if words.startswith("CLARO ") or words.startswith("PROJETO EILD CLARO"):
            return "Claro"
        if words.startswith("VIVO ") or words.startswith("PROJETO EILD VIVO"):
            return "Vivo"
        if words.startswith("ALGAR ") or words.startswith("PROJETO EILD ALGAR"):
            return "Algar"
        if allow_regional:
            return cls.regional_operator(token)
        return ""

    @classmethod
    def corporate_client_match(cls, token: str) -> tuple[str, str]:
        """Return (final_client, canonical_project) for known corporate subsidiaries/variants."""
        words = normalized_words(token)
        if words in cls.CORPORATE_CLIENT_MAP:
            return cls.CORPORATE_CLIENT_MAP[words]
        if words.startswith("CARREFOUR "):
            # E.g. "CARREFOUR ( SAMS CLUB )"
            detail = " ".join(str(token or "").split())
            return detail, "Carrefour"
        return "", ""

    @classmethod
    def canonical_project(cls, raw: str) -> str:
        key = comparison_key(raw)
        if key in cls.PROJECT_ALIASES:
            return cls.PROJECT_ALIASES[key]
        words = normalized_words(raw)
        if words in cls.PROJECT_ALIASES:
            return cls.PROJECT_ALIASES[words]
        _final_client, corp_project = cls.corporate_client_match(raw)
        if corp_project:
            return corp_project
        return raw.strip()

    @classmethod
    def project_rule_client(cls, raw: str) -> str:
        key = comparison_key(raw)
        if key in cls.PROJECT_CLIENT:
            return cls.PROJECT_CLIENT[key]
        words = normalized_words(raw)
        if words in cls.PROJECT_CLIENT:
            return cls.PROJECT_CLIENT[words]
        if words.startswith("CARREFOUR "):
            return "Claro"
        return ""

    @classmethod
    def project_key(cls, raw: str) -> str:
        key = comparison_key(raw)
        if key in cls.PROJECT_ALIASES:
            return key
        words = normalized_words(raw)
        if words in cls.PROJECT_ALIASES:
            return words
        if words in cls.CORPORATE_CLIENT_MAP or words.startswith("CARREFOUR "):
            return words
        return ""

    @classmethod
    def is_generic_project(cls, raw: str) -> bool:
        words = normalized_words(raw)
        return words in cls.GENERIC_PROJECTS or comparison_key(raw) in cls.GENERIC_PROJECTS

    @classmethod
    def detect_internal_arion(cls, *texts: str) -> str:
        """Detect owned ARION internal/POP infrastructure conservatively without false positives."""
        combined = " ".join(str(text or "").strip() for text in texts if str(text or "").strip())
        if not combined:
            return ""
        folded = comparison_key(combined)
        if INTERNAL_ARION_EXCLUDE_PATTERN.search(folded):
            return ""
        if INTERNAL_ARION_POP_PATTERN.search(folded):
            return "POP ARION"
        if INTERNAL_ARION_INFRA_PATTERN.search(folded):
            return "Infraestrutura ARION"
        return ""


def _configured_aliases(aliases=None):
    if aliases is not None:
        return aliases
    try:
        value = json.loads(os.environ.get("ARION_LINK_CLIENT_ALIASES", "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class LinkBusinessIdentity:
    contracting_client_raw: str = ""
    contracting_client: str = ""
    business_project: str = ""
    business_account: str = ""
    business_id: str = ""
    confidence: str = "UNCLASSIFIED"
    source: str = "UNKNOWN"
    final_client: str = ""
    is_internal_arion: bool = False
    internal_detail: str = ""

    def as_dict(self):
        return {
            "contracting_client_raw": self.contracting_client_raw,
            "contracting_client": self.contracting_client,
            "business_project": self.business_project,
            "business_account": self.business_account,
            "business_id": self.business_id,
            "business_identity_confidence": self.confidence,
            "business_identity_source": self.source,
        }


class LinkBusinessIdentityService:
    """Parse structured CMDB name segments, retaining the untouched source in name."""

    @classmethod
    def parse(cls, name: str, *, aliases=None, designation: str = "") -> LinkBusinessIdentity:
        original = " ".join((name or "").split())
        if not original:
            return LinkBusinessIdentity()
        match = BUSINESS_ID_PATTERN.search(original)
        business_id = match.group(0).upper() if match else ""
        if not business_id and designation:
            desig_match = BUSINESS_ID_PATTERN.search(designation)
            if desig_match:
                business_id = desig_match.group(0).upper()
        prefix = original[:match.start()] if match else original
        parts = [part.strip(" \t:;#") for part in SEPARATOR_PATTERN.split(prefix) if part.strip(" \t:;#")]
        if not parts:
            internal_detail = BusinessIdentityRules.detect_internal_arion(original, designation)
            if internal_detail:
                return LinkBusinessIdentity(
                    contracting_client_raw=internal_detail,
                    contracting_client=BusinessIdentityRules.INTERNAL_OPERATOR_LABEL,
                    business_project=internal_detail,
                    business_account="",
                    business_id=business_id,
                    confidence="HIGH",
                    source="CMDB_INTERNAL_ARION",
                    final_client="",
                    is_internal_arion=True,
                    internal_detail=internal_detail,
                )
            return LinkBusinessIdentity(business_id=business_id)

        raw_client = ""
        client = ""
        client_index = -1
        # Pass 1: explicit non-regional operator tokens take precedence (e.g. TELMEX before CLARO.BA).
        for index, part in enumerate(parts):
            candidate = BusinessIdentityRules.client_for_token(part, allow_regional=False)
            if candidate:
                raw_client, client, client_index = part, candidate, index
                break
        # Pass 2: regional operator prefixes like CLARO.SC. or VIVO.PE. when no explicit token appeared.
        if not client:
            for index, part in enumerate(parts):
                candidate = BusinessIdentityRules.regional_operator(part)
                if candidate:
                    raw_client, client, client_index = part.rstrip("."), candidate, index
                    break
        # Pass 3: check designation for regional prefix if name had no operator.
        if not client and designation:
            regional = BusinessIdentityRules.regional_operator(designation)
            if regional:
                raw_client = designation.split(".", 2)[0] + "." + designation.split(".", 2)[1]
                client = regional

        aliases_by_key = {
            comparison_key(raw): str(canonical).strip()
            for raw, canonical in _configured_aliases(aliases).items()
            if isinstance(raw, str) and isinstance(canonical, str) and canonical.strip()
        }
        if raw_client and comparison_key(raw_client) in aliases_by_key:
            client = aliases_by_key[comparison_key(raw_client)]
        client_tokens = {normalized_words(part) for part in parts}
        if client == "Claro" and {"CLARO", "TELMEX"}.issubset(client_tokens):
            raw_client = "CLARO / TELMEX"

        # Detect internal ARION/POP infrastructure when no external contracting operator is present.
        internal_detail = ""
        if not client:
            internal_detail = BusinessIdentityRules.detect_internal_arion(original, designation)
            if internal_detail:
                return LinkBusinessIdentity(
                    contracting_client_raw=internal_detail,
                    contracting_client=BusinessIdentityRules.INTERNAL_OPERATOR_LABEL,
                    business_project=internal_detail,
                    business_account="",
                    business_id=business_id,
                    confidence="HIGH",
                    source="CMDB_INTERNAL_ARION",
                    final_client="",
                    is_internal_arion=True,
                    internal_detail=internal_detail,
                )

        project_index = -1
        project_raw = ""
        project = ""
        final_client = ""
        corporate_account = ""
        for index, part in enumerate(parts):
            corp_client, corp_project = BusinessIdentityRules.corporate_client_match(part)
            if corp_client and corp_project:
                project_index, project_raw = index, part
                final_client = corp_client
                corporate_account = corp_client
                continue
            if BusinessIdentityRules.project_key(part):
                # Prefer non-generic project over generic DIVERSOS when both appear
                # (e.g., CLARO - DIVERSOS - CARREFOUR).
                if project_raw and BusinessIdentityRules.is_generic_project(part) and not BusinessIdentityRules.is_generic_project(project_raw):
                    continue
                project_index, project_raw = index, part
            elif normalized_words(part) in {"VIVO DIVERSOS", "CLARO DIVERSOS"} and not project_raw:
                project_index, project_raw = index, "DIVERSOS"

        if project_index >= 0:
            project = BusinessIdentityRules.canonical_project(project_raw)
        elif raw_client and client_index >= 0:
            # Only use the legacy adjacent-token fallback for a real commercial
            # segment. Regional operator circuit prefixes and TELMEX are technical/operator context.
            candidate_index = client_index + 1
            candidate = parts[candidate_index] if candidate_index < len(parts) else ""
            if (
                candidate
                and not BusinessIdentityRules.is_regional_token(candidate)
                and not BusinessIdentityRules.client_for_token(candidate, allow_regional=True)
            ):
                project_index = candidate_index
                project_raw = candidate
                project = BusinessIdentityRules.canonical_project(project_raw)
        else:
            project = ""

        source = "CMDB_EXPLICIT_CLIENT" if raw_client else "UNKNOWN"
        if not client and project:
            inferred = BusinessIdentityRules.project_rule_client(project_raw)
            if inferred:
                client = inferred
                source = "CMDB_PROJECT_RULE"
        if not client and corporate_account:
            source = "CMDB_CORPORATE_ALIAS"

        account = corporate_account
        if not account and raw_client and project_index >= 0:
            context_indices = {
                index for index, part in enumerate(parts)
                if (
                    (client == "Claro" and normalized_words(part).startswith("TELMEX"))
                    or BusinessIdentityRules.is_regional_token(part)
                    or (BusinessIdentityRules.is_generic_project(part) and project and not BusinessIdentityRules.is_generic_project(project))
                )
            }
            remaining = [
                part for index, part in enumerate(parts)
                if index not in ({client_index, project_index} | context_indices)
            ]
            if len(remaining) == 1:
                account = remaining[0]

        if not final_client:
            if account and not BusinessIdentityRules.is_generic_project(account):
                final_client = account
            elif project and not BusinessIdentityRules.is_generic_project(project):
                final_client = project

        confidence = (
            "HIGH" if client and project and business_id
            else "MEDIUM" if client or project or business_id
            else "LOW"
        )
        if source == "UNKNOWN":
            confidence = "LOW"
        elif source == "CMDB_CORPORATE_ALIAS":
            confidence = "MEDIUM"
        return LinkBusinessIdentity(
            contracting_client_raw=raw_client,
            contracting_client=client,
            business_project=project,
            business_account=account,
            business_id=business_id,
            confidence=confidence,
            source=source,
            final_client=final_client,
            is_internal_arion=False,
            internal_detail="",
        )


def classify_link_name(name: str, *, aliases=None, designation: str = "") -> dict:
    return LinkBusinessIdentityService.parse(name, aliases=aliases, designation=designation).as_dict()
