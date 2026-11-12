"""Selection of valid operational incidents, without changing source history."""
from django.db.models import Q

from .closing_reason import ClosingReasonCanonicalizer
from .otrs_status import OPERATIONAL_VALID_STATUSES, business_statuses_query


class IncidentRules:
    EXCLUDED_KEYS = ("chamado_cancelado", "cancelamento_servico", "sem_intervencao_tecnica", "sem_atuacao", "improcedente", "UNKNOWN")
    CATEGORIES = {
        "massiva": ("falha_massiva",), "rompimento": ("rompimento_fibra",),
        "equipamento": ("equipamento_travado", "equipamento_substituido", "equipamento_desligado", "fonte_queimada"),
        "energia": ("falha_eletrica",),
        "configuracao": ("configuracao_vlan", "configuracao_rede", "erro_configuracao_ip", "atualizacao_configuracao", "vlan", "tunnel_mtu"),
        "reset_remoto": ("reset_remoto",), "financeiro": ("pendencia_financeira",),
    }
    LABELS = {"massiva":"Massiva", "rompimento":"Rompimento", "equipamento":"Equipamento", "energia":"Energia", "configuracao":"Configuração", "reset_remoto":"Reset remoto", "financeiro":"Pendência Financeira", "demais":"Demais motivos técnicos", "sem_classificacao":"Sem classificação"}

    @classmethod
    def category(cls, raw):
        key = ClosingReasonCanonicalizer.key(raw)
        return next((category for category,keys in cls.CATEGORIES.items() if key in keys), "demais" if key in ClosingReasonCanonicalizer._BY_KEY else "sem_classificacao")

    @classmethod
    def valid(cls, queryset, prefix=""):
        query = Q(**{f"{prefix}responsibility_normalized__in": ("PROCEDENTE", "INDETERMINADO")})
        query &= business_statuses_query(f"{prefix}business_status", OPERATIONAL_VALID_STATUSES)
        excluded = Q()
        for key in cls.EXCLUDED_KEYS:
            for raw in ClosingReasonCanonicalizer.raw_values(key):
                excluded |= Q(**{f"{prefix}closing_code_raw__iexact": raw})
        return queryset.filter(query).exclude(excluded)

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
