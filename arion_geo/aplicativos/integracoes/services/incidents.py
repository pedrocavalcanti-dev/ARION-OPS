"""Selection of valid operational incidents, without changing source history."""
import re
import unicodedata

from django.db.models import Q

from .closing_reason import ClosingReasonCanonicalizer
from .link_business_identity import BusinessIdentityRules
from .otrs_status import OPERATIONAL_VALID_STATUSES, business_statuses_query, normalize_business_status
from .ticket_business_identity import TicketBusinessIdentityService


class IncidentRules:
    EXCLUDED_KEYS = ("chamado_cancelado", "cancelamento_servico", "sem_intervencao_tecnica", "sem_atuacao", "improcedente", "UNKNOWN")
    BASE_EXCLUDED_KEYS = ("chamado_cancelado", "cancelamento_servico", "sem_atuacao", "improcedente", "UNKNOWN")
    SEM_INTERVENCAO_KEYS = ("sem_intervencao_tecnica",)
    VALID_RESPONSIBILITIES = ("PROCEDENTE", "INDETERMINADO")
    CATEGORIES = {
        "massiva": ("falha_massiva",), "rompimento": ("rompimento_fibra",),
        "equipamento": ("equipamento_travado", "equipamento_substituido", "equipamento_desligado", "fonte_queimada"),
        "energia": ("falha_eletrica",),
        "configuracao": ("configuracao_vlan", "configuracao_rede", "erro_configuracao_ip", "atualizacao_configuracao", "vlan", "tunnel_mtu"),
        "reset_remoto": ("reset_remoto",), "financeiro": ("pendencia_financeira",),
    }
    LABELS = {"massiva":"Massiva", "rompimento":"Rompimento", "equipamento":"Equipamento", "energia":"Energia", "configuracao":"Configuração", "reset_remoto":"Reset remoto", "financeiro":"Pendência Financeira", "demais":"Demais motivos técnicos", "sem_classificacao":"Sem classificação"}

    @staticmethod
    def _normalized_reason(raw):
        text = unicodedata.normalize("NFKD", str(raw or "").strip())
        folded = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
        words = " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())
        compact = re.sub(r"[^a-z0-9]+", "", folded)
        return words, compact

    @classmethod
    def canonical_operator(cls, client=""):
        value = str(client or "").strip()
        if not value:
            return ""
        mapped = BusinessIdentityRules.client_for_token(value)
        if mapped:
            return mapped
        words, _ = cls._normalized_reason(value)
        if words in {"claro", "telmex", "claro telmex", "claro brasil"}:
            return "Claro"
        if words in {"vivo", "telefonica", "telefonica brasil", "telefonica brasil s a"}:
            return "Vivo"
        if words == "algar":
            return "Algar"
        return value

    @classmethod
    def excluded_keys_for_client(cls, client=""):
        operator = cls.canonical_operator(client)
        if operator == "Claro":
            return cls.BASE_EXCLUDED_KEYS
        if operator == "Vivo":
            return cls.EXCLUDED_KEYS
        # Other operators preserve the pre-existing default policy until documented otherwise.
        return cls.EXCLUDED_KEYS

    @classmethod
    def operator_policy(cls, client=""):
        operator = cls.canonical_operator(client)
        excluded_keys = cls.excluded_keys_for_client(operator)
        excludes_sem_intervencao = "sem_intervencao_tecnica" in excluded_keys
        excluded_groups = ["Cancelados", "Improcedentes", "Sem atuação / desconhecidos"]
        if operator == "Claro":
            label = "Claro"
            keeps_sem_intervencao = True
            summary = "Claro: exclui Cancelados e Improcedentes; mantém Sem intervenção."
        elif operator == "Vivo":
            label = "Vivo"
            keeps_sem_intervencao = False
            excluded_groups.append("Sem intervenção")
            summary = "Vivo: exclui Cancelados, Improcedentes e Sem intervenção."
        elif operator == "other":
            label = "Outras operadoras"
            keeps_sem_intervencao = False
            excluded_groups.append("Sem intervenção")
            summary = "Demais operadoras: preserva a política padrão documentada."
        elif operator:
            label = operator
            keeps_sem_intervencao = not excludes_sem_intervencao
            if excludes_sem_intervencao:
                excluded_groups.append("Sem intervenção")
            summary = f"{operator}: preserva a política padrão documentada."
        else:
            label = "Todas as operadoras"
            keeps_sem_intervencao = False
            excluded_groups.append("Sem intervenção (Vivo e demais; Claro mantém)")
            summary = "Política por operadora: Vivo e demais excluem Sem intervenção; Claro mantém Sem intervenção."
        return {
            "operator": operator or "Padrão",
            "label": label,
            "excludes_canceled": True,
            "excludes_improcedent": True,
            "excludes_sem_intervencao": excludes_sem_intervencao,
            "keeps_sem_intervencao": keeps_sem_intervencao,
            "excluded_keys": list(excluded_keys),
            "excluded_groups": excluded_groups,
            "summary": summary,
        }

    @classmethod
    def resolve_ticket_operator(cls, ticket, link=None):
        parsed = TicketBusinessIdentityService.parse(getattr(ticket, "title", "") or "")
        if parsed.client:
            return cls.canonical_operator(parsed.client)
        if link is not None:
            raw_client = getattr(link, "contracting_client", "") or ""
            return cls.canonical_operator(raw_client)
        return ""

    @classmethod
    def is_reason_excluded(cls, raw, client=""):
        closing_raw = str(raw or "").strip()
        if not closing_raw:
            return False
        key = ClosingReasonCanonicalizer.key(closing_raw)
        words, compact = cls._normalized_reason(closing_raw)
        if closing_raw.upper() == "UNKNOWN" or compact == "unknown":
            return True
        if key in {"chamado_cancelado", "cancelamento_servico", "improcedente"}:
            return True
        if re.search(r"\b(cancelad[oa]s?|cancelamento|improcedente|nao procedente)\b", words) or compact.startswith(
            ("cancelado", "cancelamento", "chamadocancelado", "improcedente")
        ):
            return True
        if key == "sem_atuacao" or compact == "sematuacao" or words == "sem atuacao":
            return True
        is_sem_intervencao = (
            key == "sem_intervencao_tecnica"
            or words == "sem intervencao"
            or words.startswith("sem intervencao ")
            or compact.startswith("semintervencao")
        )
        if is_sem_intervencao:
            return "sem_intervencao_tecnica" in cls.excluded_keys_for_client(client)
        return False

    @classmethod
    def is_valid_ticket(cls, ticket, client=""):
        if ticket is None:
            return False
        responsibility = str(getattr(ticket, "responsibility_normalized", "") or "").strip().upper()
        if responsibility not in cls.VALID_RESPONSIBILITIES:
            return False
        if normalize_business_status(getattr(ticket, "business_status", "")) not in OPERATIONAL_VALID_STATUSES:
            return False
        return not cls.is_reason_excluded(getattr(ticket, "closing_code_raw", ""), client=client)

    @classmethod
    def category(cls, raw):
        key = ClosingReasonCanonicalizer.key(raw)
        return next((category for category,keys in cls.CATEGORIES.items() if key in keys), "demais" if key in ClosingReasonCanonicalizer._BY_KEY else "sem_classificacao")

    @classmethod
    def _excluded_reason_query(cls, keys, prefix=""):
        excluded = Q()
        for key in keys:
            for raw in ClosingReasonCanonicalizer.raw_values(key):
                excluded |= Q(**{f"{prefix}closing_code_raw__iexact": raw})
        return excluded

    @classmethod
    def valid(cls, queryset, prefix=""):
        query = Q(**{f"{prefix}responsibility_normalized__in": cls.VALID_RESPONSIBILITIES})
        query &= business_statuses_query(f"{prefix}business_status", OPERATIONAL_VALID_STATUSES)
        return queryset.filter(query).exclude(cls._excluded_reason_query(cls.EXCLUDED_KEYS, prefix=prefix))

    @classmethod
    def valid_for_operator(cls, queryset, prefix="", client=None):
        query = Q(**{f"{prefix}responsibility_normalized__in": cls.VALID_RESPONSIBILITIES})
        query &= business_statuses_query(f"{prefix}business_status", OPERATIONAL_VALID_STATUSES)
        operator = cls.canonical_operator(client) if client not in {None, "", "all", "other"} else ""
        keys = cls.excluded_keys_for_client(operator) if operator else cls.BASE_EXCLUDED_KEYS
        return queryset.filter(query).exclude(cls._excluded_reason_query(keys, prefix=prefix))

    @classmethod
    def filter(cls, queryset, params, prefix=""):
        reason, category = (params.get("reason") or "").strip(), (params.get("category") or "").strip()
        def raw_query(values):
            query = Q(pk__in=[])
            for raw in values:
                query |= Q(**{f"{prefix}closing_code_raw__iexact":raw})
            return query
        if reason:
            queryset = queryset.filter(raw_query(ClosingReasonCanonicalizer.raw_values(reason)))
        if category:
            if category not in cls.LABELS:
                raise ValueError("Categoria operacional inválida.")
            if category == "sem_classificacao":
                known_values = [raw for row in ClosingReasonCanonicalizer.REASONS for raw in row.raw_values]
                queryset = queryset.exclude(raw_query(known_values))
            else:
                categorized_keys = {key for keys in cls.CATEGORIES.values() for key in keys}
                keys = (set(ClosingReasonCanonicalizer._BY_KEY)-categorized_keys) if category=="demais" else cls.CATEGORIES[category]
                raw_values = [raw for key in keys for raw in ClosingReasonCanonicalizer.raw_values(key)]
                queryset = queryset.filter(raw_query(raw_values))
        return queryset
