import calendar
from collections import defaultdict
from datetime import date, datetime, time, timedelta
import unicodedata
import re

from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from django.conf import settings

from aplicativos.chamados.models import TicketTracking
from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem, OTRSStateMapping
from aplicativos.integracoes.services.analytics import (
    BUSINESS_OPEN_STATUSES, OPERATIONAL_RESOLUTION_FIELD, LinkQueryService,
    PeriodService, TicketQueryService, operational_resolution_expression,
)
from aplicativos.integracoes.services.otrs_status import (
    OPERATIONAL_VALID_STATUSES, RESOLVED_STATUS, TERMINAL_STATUSES,
    business_status_query, business_statuses_query, normalize_business_status,
)
from aplicativos.integracoes.services.ticket_business_identity import TicketBusinessIdentityService
from aplicativos.integracoes.services.link_business_identity import BusinessIdentityRules
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
from aplicativos.integracoes.services.project_names import canonical_project_name, project_alias_groups
from aplicativos.integracoes.services.geography import REGIONS, STATE_BY_UF, region_states, state_query
from aplicativos.integracoes.services.incidents import IncidentRules


STATE_NAMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapa", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceara", "DF": "Distrito Federal", "ES": "Espirito Santo", "GO": "Goias",
    "MA": "Maranhao", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
    "PA": "Para", "PB": "Paraiba", "PR": "Parana", "PE": "Pernambuco", "PI": "Piaui",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul",
    "RO": "Rondonia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "Sao Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}
STATE_NAMES_ACCENTED = {
    "AP": "Amap\u00e1", "CE": "Cear\u00e1", "ES": "Esp\u00edrito Santo", "GO": "Goi\u00e1s",
    "MA": "Maranh\u00e3o", "PA": "Par\u00e1", "PB": "Para\u00edba", "PI": "Piau\u00ed",
    "PR": "Paran\u00e1", "RO": "Rond\u00f4nia", "SP": "S\u00e3o Paulo",
}
CLIENT_KEYS = {"claro": "Claro", "vivo": "Vivo", "algar": "Algar"}
VALID_TICKET_STATES = OPERATIONAL_VALID_STATUSES
ACTIVE_TICKET_STATES = BUSINESS_OPEN_STATUSES
VALID_PROCEDENCE = ("PROCEDENTE", "INDETERMINADO")
TERMINAL_TICKET_STATES = TERMINAL_STATUSES


def _norm(value):
    text = unicodedata.normalize("NFKD", (value or "").strip())
    return " ".join("".join(char for char in text if not unicodedata.combining(char)).casefold().split())


def _uf(value):
    key = _norm(value)
    aliases = {code.casefold(): code for code in STATE_NAMES}
    aliases.update({_norm(name): code for code, name in STATE_NAMES.items()})
    return aliases.get(key, "")


def _bounds(params):
    period = (params.get("period") or "last_month").strip()
    today = timezone.localdate()
    if period == "this_month":
        return today.replace(day=1), today
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "last_30":
        return today - timedelta(days=29), today
    if period == "last_60":
        return today - timedelta(days=59), today
    if period == "last_90":
        return today - timedelta(days=89), today
    if len(period) == 7 and period[4] == "-":
        from aplicativos.integracoes.services.weather import month_bounds
        return month_bounds(period)
    if period in {"", "custom"}:
        return PeriodService.parse(params)
    raise ValueError("Periodo do mapa invalido.")


def _datetime_bounds(start, end):
    current_tz = timezone.get_current_timezone()
    lower = timezone.make_aware(datetime.combine(start, time.min), current_tz) if start else None
    upper = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), current_tz) if end else None
    return lower, upper


def _client_filter(queryset, value):
    if not value or value == "all":
        return queryset
    if value == "other":
        return queryset.exclude(contracting_client="").exclude(contracting_client__in=(*CLIENT_KEYS.values(), "TELMEX"))
    if value == "Claro":
        return queryset.filter(Q(contracting_client="Claro") | Q(contracting_client="TELMEX"))
    return queryset.filter(contracting_client=value)


def _client_label_matches(label, requested):
    if not requested or requested == "all":
        return True
    if requested == "other":
        return bool(label) and label not in CLIENT_KEYS.values()
    return label == requested


def filter_tracking_relations(relations, *, arion_status="", assignee=""):
    if arion_status:
        if arion_status == TicketTracking.InternalStatus.OPEN:
            excluded_status_ids = TicketTracking.objects.exclude(
                internal_status=arion_status,
            ).values("ticket_external_id")
            relations = relations.exclude(ticket__external_id__in=excluded_status_ids)
        else:
            matching_status_ids = TicketTracking.objects.filter(
                internal_status=arion_status,
            ).values("ticket_external_id")
            relations = relations.filter(ticket__external_id__in=matching_status_ids)
    if assignee == "unassigned":
        assigned_ids = TicketTracking.objects.exclude(
            assigned_to__isnull=True,
        ).values("ticket_external_id")
        relations = relations.exclude(ticket__external_id__in=assigned_ids)
    elif assignee:
        assigned_ids = TicketTracking.objects.filter(
            assigned_to_id=int(assignee),
        ).values("ticket_external_id")
        relations = relations.filter(ticket__external_id__in=assigned_ids)
    return relations


class MapAnalyticsService:
    MODES = {"base", "projects", "calls", "repeat", "motives", "climate"}

    @staticmethod
    def _unlinked_ticket_queryset(
        *, user, scoped_links, mode, start_at, end_exclusive, status_filter,
        priority_filter, reason_filter, client, project, provider, uf_filter,
        arion_status, assignee, provenance,
    ):
        if mode not in {"calls", "motives"} or client not in {"", "all"} or any(
            (project, provider, uf_filter)
        ):
            return OTRSTicket.objects.none()

        tickets = TicketQueryService.base(user, links=scoped_links).filter(
            responsibility_normalized__in=(provenance,) if provenance else VALID_PROCEDENCE,
        )
        if arion_status:
            tracking = TicketTracking.objects.filter(ticket_external_id=OuterRef("external_id"))
            tickets = tickets.annotate(_map_has_tracking=Exists(tracking))
            if arion_status == TicketTracking.InternalStatus.OPEN:
                tickets = tickets.filter(
                    Q(_map_has_tracking=False)
                    | Q(external_id__in=TicketTracking.objects.filter(
                        internal_status=arion_status,
                    ).values("ticket_external_id"))
                )
            else:
                tickets = tickets.filter(external_id__in=TicketTracking.objects.filter(
                    internal_status=arion_status,
                ).values("ticket_external_id"))
        if assignee == "unassigned":
            tickets = tickets.exclude(external_id__in=TicketTracking.objects.exclude(
                assigned_to__isnull=True,
            ).values("ticket_external_id"))
        elif assignee:
            tickets = tickets.filter(external_id__in=TicketTracking.objects.filter(
                assigned_to_id=int(assignee),
            ).values("ticket_external_id"))
        if mode == "calls":
            tickets = tickets.filter(business_statuses_query("business_status", VALID_TICKET_STATES))
            if start_at:
                tickets = tickets.filter(source_created_at__gte=start_at)
            if end_exclusive:
                tickets = tickets.filter(source_created_at__lt=end_exclusive)
            if status_filter:
                tickets = tickets.filter(business_status_query("business_status", status_filter))
            if priority_filter:
                tickets = tickets.filter(priority_name=priority_filter)
        else:
            tickets = tickets.filter(
                business_statuses_query("business_status", TERMINAL_TICKET_STATES),
            ).exclude(closing_code_raw="").annotate(**{
                OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression(),
            }).exclude(**{f"{OPERATIONAL_RESOLUTION_FIELD}__isnull": True})
            if start_at:
                tickets = tickets.filter(**{f"{OPERATIONAL_RESOLUTION_FIELD}__gte": start_at})
            if end_exclusive:
                tickets = tickets.filter(**{f"{OPERATIONAL_RESOLUTION_FIELD}__lt": end_exclusive})
            if reason_filter:
                tickets = tickets.filter(
                    closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(reason_filter),
                )

        has_scoped_link = OTRSTicketConfigItem.objects.filter(
            ticket_id=OuterRef("pk"), is_present_in_source=True,
            cmdb_link__in=scoped_links,
        )
        return tickets.annotate(_has_scoped_link=Exists(has_scoped_link)).filter(
            _has_scoped_link=False,
        )

    @classmethod
    def query(cls, user, params, *, fixed_client=False):
        mode = (params.get("mode") or "base").strip()
        if mode not in cls.MODES:
            raise ValueError("Modo de mapa invalido.")
        # Climate is a visualization of the monthly incident universe.
        requested_mode = mode
        monthly_motives = mode == "motives" and params.get("cohort") == "created"
        if mode == "climate":
            mode = "calls"
        start, end = _bounds(params)
        if not start or not end or start>end or end>timezone.localdate() or (end-start).days>366:
            raise ValueError("Selecione um intervalo histórico válido de até 366 dias, sem datas futuras.")
        client = (params.get("client") or "all").strip()
        project = canonical_project_name(params.get("project"))
        provider = (params.get("provider") or "").strip()
        if mode in {"base","projects"}:
            params = dict(params.items())
            for name in ("category","reason","status","priority","arion_status","assignee","provenance"):
                params.pop(name,None)
        reason_filter = ClosingReasonCanonicalizer.key(params.get("reason")) if params.get("reason") else ""
        status_filter = normalize_business_status(params.get("status")) if params.get("status") else ""
        priority_filter = (params.get("priority") or "").strip()
        arion_status = (params.get("arion_status") or "").strip().upper()
        assignee = (params.get("assignee") or "").strip()
        provenance = (params.get("provenance") or "").strip().upper()
        if status_filter not in {"", *VALID_TICKET_STATES}:
            raise ValueError("Status do ticket invalido.")
        if len(priority_filter) > 160:
            raise ValueError("Prioridade do ticket invalida.")
        if arion_status not in {"", *(value for value, _ in TicketTracking.InternalStatus.choices)}:
            raise ValueError("Status ARION invalido.")
        if assignee and assignee != "unassigned" and (
            not assignee.isdigit() or len(assignee) > 20
        ):
            raise ValueError("Responsavel ARION invalido.")
        if provenance not in {"", *VALID_PROCEDENCE}:
            raise ValueError("Procedencia do ticket invalida.")
        uf_filter = (params.get("uf") or "").strip().upper()
        region = (params.get("region") or "").strip().upper()
        if params.get("category") and params.get("category") not in IncidentRules.LABELS:
            raise ValueError("Categoria operacional inválida.")
        allowed_ufs = region_states(region)
        if uf_filter and (uf_filter not in STATE_NAMES or uf_filter not in allowed_ufs):
            raise ValueError("UF incompatível com a região selecionada.")

        # Data Scope is applied first, before option catalogs, dimensions, and counts.
        scoped = LinkQueryService.base(user)
        if fixed_client:
            scoped = _client_filter(scoped,client)
        if region:
            scoped = scoped.filter(state_query(allowed_ufs))
        filtered = _client_filter(scoped, client)
        ticket_candidates = scoped
        if provider:
            if not provider.isdigit():
                raise ValueError("Provedor do mapa invalido.")
            filtered = filtered.filter(operator__pk=int(provider), operator__is_present_in_source=True)
            ticket_candidates = ticket_candidates.filter(operator__pk=int(provider), operator__is_present_in_source=True)
        if uf_filter in STATE_NAMES:
            state_values = {uf_filter, STATE_NAMES[uf_filter], STATE_NAMES_ACCENTED.get(uf_filter, STATE_NAMES[uf_filter])}
            from django.db.models import Q
            state_q = Q()
            for value in state_values:
                state_q |= Q(state__iexact=value)
            filtered = filtered.filter(state_q)
            ticket_candidates = ticket_candidates.filter(state_q)

        project_scope = _client_filter(scoped, client)
        project_groups = project_alias_groups(
            project_scope.order_by().exclude(business_project="").values_list(
                "business_project", flat=True,
            ).distinct()
        )
        project_options = sorted(project_groups, key=lambda value: (value.casefold(), value))
        if project and project not in project_groups:
            raise ValueError("Projeto incompatível com o cliente e o recorte autorizado.")
        if project:
            raw_projects = project_groups.get(project, ())
            filtered = filtered.filter(business_project__in=raw_projects) if raw_projects else filtered.none()
        source_total = scoped.count()
        filtered_total = filtered.count()

        clients = list(scoped.exclude(contracting_client="").exclude(contracting_client="TELMEX").values_list(
            "contracting_client", flat=True).distinct().order_by("contracting_client"))
        provider_scope = project_scope.filter(business_project__in=project_groups.get(project,())) if project else project_scope
        providers = list(provider_scope.filter(operator__is_present_in_source=True).values(
            "operator_id", "operator__name").distinct().order_by("operator__name"))
        if provider and int(provider) not in {row["operator_id"] for row in providers}:
            raise ValueError("ISP incompatível com o projeto e o recorte autorizado.")

        start, end = (None, None) if mode == "base" else (start, end)
        start_at, end_exclusive = _datetime_bounds(start, end)
        current_relations = OTRSTicketConfigItem.objects.none()
        period_relations = OTRSTicketConfigItem.objects.none()
        terminal_relations = OTRSTicketConfigItem.objects.none()
        priority_options = []
        if mode in {"projects", "calls", "repeat", "motives"}:
            valid_noc_tickets = IncidentRules.valid_for_operator(TicketQueryService.noc_tickets(), client=client)
            period_relations = OTRSTicketConfigItem.objects.filter(
                is_present_in_source=True, ticket__in=valid_noc_tickets,
                ticket__responsibility_normalized__in=(provenance,) if provenance else VALID_PROCEDENCE,
            ).filter(business_statuses_query(
                "ticket__business_status", VALID_TICKET_STATES,
            )).select_related("ticket")
            current_relations = OTRSTicketConfigItem.objects.filter(
                is_present_in_source=True, ticket__in=valid_noc_tickets,
                ticket__business_status__in=ACTIVE_TICKET_STATES,
                ticket__responsibility_normalized__in=(provenance,) if provenance else VALID_PROCEDENCE,
            ).select_related("ticket")
            terminal_relations = OTRSTicketConfigItem.objects.annotate(**{
                OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression("ticket__"),
            }).filter(
                is_present_in_source=True, ticket__in=valid_noc_tickets,
                ticket__responsibility_normalized__in=(provenance,) if provenance else VALID_PROCEDENCE,
            ).filter(business_statuses_query(
                "ticket__business_status", TERMINAL_TICKET_STATES,
            )).exclude(ticket__closing_code_raw="").exclude(
                **{f"{OPERATIONAL_RESOLUTION_FIELD}__isnull": True},
            ).select_related("ticket")
            if start:
                period_relations = period_relations.filter(ticket__source_created_at__gte=start_at)
                terminal_relations = terminal_relations.filter(**{f"{OPERATIONAL_RESOLUTION_FIELD}__gte": start_at})
            if end:
                period_relations = period_relations.filter(ticket__source_created_at__lt=end_exclusive)
                terminal_relations = terminal_relations.filter(**{f"{OPERATIONAL_RESOLUTION_FIELD}__lt": end_exclusive})
            priority_options = sorted({
                rel.ticket.priority_name
                for rel in period_relations.filter(cmdb_link__in=scoped).exclude(ticket__priority_name="").select_related("ticket", "cmdb_link").only(
                    "ticket__priority_name", "ticket__title", "ticket__closing_code_raw",
                    "ticket__business_status", "ticket__responsibility_normalized", "cmdb_link__contracting_client",
                )
                if IncidentRules.is_valid_ticket(rel.ticket, IncidentRules.resolve_ticket_operator(rel.ticket, rel.cmdb_link))
            })
            if status_filter:
                status_query = business_status_query("ticket__business_status", status_filter)
                period_relations = period_relations.filter(status_query)
                current_relations = current_relations.filter(status_query)
                terminal_relations = terminal_relations.filter(status_query)
            if priority_filter:
                period_relations = period_relations.filter(ticket__priority_name=priority_filter)
                current_relations = current_relations.filter(ticket__priority_name=priority_filter)
                terminal_relations = terminal_relations.filter(ticket__priority_name=priority_filter)
            period_relations = filter_tracking_relations(
                period_relations, arion_status=arion_status, assignee=assignee,
            )
            current_relations = filter_tracking_relations(
                current_relations, arion_status=arion_status, assignee=assignee,
            )
            terminal_relations = filter_tracking_relations(
                terminal_relations, arion_status=arion_status, assignee=assignee,
            )
            period_relations = IncidentRules.filter(period_relations, params, "ticket__")
            current_relations = IncidentRules.filter(current_relations, params, "ticket__")
            terminal_relations = IncidentRules.filter(terminal_relations, params, "ticket__")
            if mode == "motives" and reason_filter:
                terminal_relations = terminal_relations.filter(
                    ticket__closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(reason_filter),
                )
            if monthly_motives:
                terminal_relations = period_relations
            query_source = filtered if mode == "projects" else ticket_candidates
            query_links = query_source.prefetch_related(
                Prefetch("ticket_relations", queryset=period_relations, to_attr="_map_period"),
                Prefetch("ticket_relations", queryset=current_relations, to_attr="_map_active"),
                Prefetch("ticket_relations", queryset=terminal_relations, to_attr="_map_terminal"),
            )
        else:
            query_links = filtered

        unlinked_tickets = cls._unlinked_ticket_queryset(
            user=user, scoped_links=scoped, mode="calls" if monthly_motives else mode, start_at=start_at,
            end_exclusive=end_exclusive, status_filter=status_filter,
            priority_filter=priority_filter, reason_filter=reason_filter,
            client=client, project=project, provider=provider, uf_filter=uf_filter,
            arion_status=arion_status, assignee=assignee, provenance=provenance,
        )
        unlinked_tickets = IncidentRules.filter(IncidentRules.valid_for_operator(unlinked_tickets, client=client), params)
        if region:
            unlinked_tickets = unlinked_tickets.none()
        unlinked_rows = [
            ticket for ticket in unlinked_tickets.only(
                "pk", "title", "source_created_at", "closing_code_raw", "business_status", "responsibility_normalized",
            ).iterator(chunk_size=500)
            if IncidentRules.is_valid_ticket(ticket, TicketBusinessIdentityService.parse(ticket.title).client)
        ]
        unlinked_ticket_count = len(unlinked_rows)

        result = {}
        global_tickets, global_ticket_statuses, global_ticket_priorities, global_ticket_reasons, active_ids, global_terminal = set(), {}, {}, {}, set(), set()
        global_recurrence_ticket_ids = set()
        global_ticket_contexts = {}
        global_ticket_dates, global_ticket_categories = {}, {}
        weather_events = []
        scoped_cities = set()
        region_category_ticket_ids = defaultdict(lambda: defaultdict(set))
        ticket_identity_cache = {}
        def identity_for(ticket):
            if ticket.pk not in ticket_identity_cache:
                ticket_identity_cache[ticket.pk] = TicketBusinessIdentityService.parse(ticket.title)
            return ticket_identity_cache[ticket.pk]
        global_client_identified = global_client_unidentified = 0
        invalid_uf_count = 0
        located_ticket_ids, affected_link_ids, recurrent_link_ids = set(), set(), set()
        for link in query_links.iterator(chunk_size=500):
            client_label = BusinessIdentityRules.client_for_token(link.contracting_client) or link.contracting_client
            link_project = canonical_project_name(link.business_project)
            link_matches = _client_label_matches(client_label, client) and (not project or link_project == project)
            if link_matches:
                if client_label:
                    global_client_identified += 1
                else:
                    global_client_unidentified += 1
            uf = _uf(link.state)
            if not uf:
                invalid_uf_count += int(link_matches)
            if link_matches and uf and link.city.strip():
                scoped_cities.add((uf, link.city.strip()))
            item = result.setdefault(uf, {
                "total_links": 0, "client_counts": {}, "project_counts": {}, "account_counts": {},
                "provider_counts": {}, "city_counts": {}, "ticket_status_counts": {},
                "ticket_client_counts": {}, "ticket_project_counts": {}, "ticket_provider_counts": {}, "ticket_priority_counts": {}, "ticket_reason_counts": {},
                "ticket_project_ticket_ids": {}, "ticket_provider_ticket_ids": {},
                "ticket_city_counts": {},
                "recurrence_client_counts": {}, "recurrence_provider_counts": {},
                "ticket_count": 0, "active_tickets": 0,
                "affected_links": 0, "recurrence_links": 0, "recurrence_tickets": 0,
                "terminal_links": 0, "reason_counts": {},
                "tickets_by_id": {}, "ticket_context_by_id": {}, "active_ticket_ids": set(),
                "recurrence_ticket_ids": set(), "terminal_by_id": {},
            })
            if link_matches:
                item["total_links"] += 1
                _inc(item["client_counts"], client_label or "Cliente não identificado")
                _inc(item["project_counts"], link_project or "Projeto não identificado")
                _inc(item["account_counts"], link.business_account or "Conta não identificada")
                _inc(item["provider_counts"], link.operator.name if link.operator_id and link.operator.is_present_in_source else "Provedor não identificado")
                if link.city:
                    city_key = _norm(link.city)
                    city = item["city_counts"].setdefault(city_key, {"count": 0, "variants": {}})
                    city["count"] += 1
                    city["variants"][link.city.strip()] = city["variants"].get(link.city.strip(), 0) + 1
            period_rows = getattr(link, "_map_period", [])
            active_rows = getattr(link, "_map_active", [])
            terminal_rows = getattr(link, "_map_terminal", [])
            def context_matches(ticket):
                parsed = identity_for(ticket)
                ticket_client = parsed.client or client_label
                if not IncidentRules.is_valid_ticket(ticket, ticket_client):
                    return False
                ticket_project = parsed.project or link.business_project
                return _client_label_matches(ticket_client, client) and (
                    not project or canonical_project_name(ticket_project) == project
                )
            tickets = {rel.ticket_id: rel.ticket for rel in period_rows if context_matches(rel.ticket)}
            for ticket in tickets.values():
                category = IncidentRules.category(ticket.closing_code_raw)
                if category not in {None,"financeiro","sem_classificacao"}:
                    if uf in STATE_BY_UF:
                        region_category_ticket_ids[STATE_BY_UF[uf]["region_id"]][category].add(ticket.pk)
                    weather_events.append({"uf":uf,"city":link.city.strip(),"link":link.pk,"ticket":ticket.pk,"at":ticket.source_created_at,"category":category})
            active = {rel.ticket_id for rel in active_rows if context_matches(rel.ticket)}
            terminal = {rel.ticket_id: rel.ticket for rel in terminal_rows if context_matches(rel.ticket)}
            global_tickets.update(tickets)
            if uf:
                located_ticket_ids.update(tickets)
            if tickets:
                affected_link_ids.add(link.pk)
            if len(tickets) >= 2:
                recurrent_link_ids.add(link.pk)
            global_ticket_statuses.update({ticket_id: normalize_business_status(ticket.business_status) for ticket_id, ticket in tickets.items()})
            global_ticket_priorities.update({ticket_id: ticket.priority_name for ticket_id, ticket in tickets.items()})
            global_ticket_reasons.update({ticket_id: ClosingReasonCanonicalizer.display(ticket.closing_code_raw) for ticket_id, ticket in tickets.items()})
            global_ticket_dates.update({ticket_id: timezone.localtime(ticket.source_created_at).date().isoformat() for ticket_id,ticket in tickets.items() if ticket.source_created_at})
            global_ticket_categories.update({ticket_id: IncidentRules.category(ticket.closing_code_raw) for ticket_id,ticket in tickets.items()})
            active_ids.update(active)
            global_terminal.update(terminal)
            item["tickets_by_id"].update(tickets)
            context_tickets = terminal if mode == "motives" else tickets
            for ticket_id, ticket in context_tickets.items():
                identity = identity_for(ticket)
                linked_client = client_label
                linked_project = link_project
                context = {
                    "client": identity.client or linked_client or "Cliente n\u00e3o identificado",
                    "project": canonical_project_name(identity.project or linked_project) or "Projeto n\u00e3o identificado",
                    "provider": link.operator.name if link.operator_id and link.operator.is_present_in_source else "Provedor n\u00e3o identificado",
                }
                item["ticket_context_by_id"].setdefault(ticket_id, context)
                global_ticket_contexts.setdefault(ticket_id, context)
                item["ticket_project_ticket_ids"].setdefault(context["project"], set()).add(ticket_id)
                item["ticket_provider_ticket_ids"].setdefault(context["provider"], set()).add(ticket_id)
            if link.city and context_tickets and (mode != "repeat" or len(tickets) >= 2):
                _inc(item["ticket_city_counts"], link.city.strip())
            item["active_ticket_ids"].update(active)
            item["terminal_by_id"].update(terminal)
            if tickets:
                item["affected_links"] += 1
            if len(tickets) >= 2:
                item["recurrence_links"] += 1
                item["recurrence_ticket_ids"].update(tickets)
                global_recurrence_ticket_ids.update(tickets)
                _inc(item["recurrence_client_counts"], client_label or "Cliente não identificado")
                _inc(item["recurrence_provider_counts"], link.operator.name if link.operator_id and link.operator.is_present_in_source else "Provedor não identificado")
            if terminal:
                item["terminal_links"] += 1
        result = {uf: item for uf, item in result.items() if uf and (item["total_links"] or item["tickets_by_id"])}
        for item in result.values():
            total_links = item["total_links"]
            identified = total_links - item["client_counts"].get("Cliente não identificado", 0)
            item["client_identified_links"] = identified
            item["client_unidentified_links"] = total_links - identified
            item["client_coverage"] = round(identified / total_links * 100, 1) if total_links else 0
            city_counts = {}
            for city in item["city_counts"].values():
                variants = city["variants"]
                accented = [label for label in variants if any(unicodedata.combining(char) for char in unicodedata.normalize("NFD", label))]
                candidates = accented or list(variants)
                label = max(candidates, key=lambda value: (variants[value], value != value.upper(), len(value)))
                if not accented or label.isupper():
                    label = label.title()
                city_counts[label] = city_counts.get(label, 0) + city["count"]
            item["city_counts"] = city_counts
            item["ticket_count"] = len(item["tickets_by_id"])
            item["daily_counts"], item["technical_daily_counts"], item["category_counts"] = {}, {}, {}
            for ticket in item["tickets_by_id"].values():
                category = IncidentRules.category(ticket.closing_code_raw)
                _inc(item["category_counts"], IncidentRules.LABELS[category])
                if ticket.source_created_at:
                    day = timezone.localtime(ticket.source_created_at).date().isoformat()
                    _inc(item["daily_counts"], day)
                    if category not in {"financeiro", "sem_classificacao"}:
                        _inc(item["technical_daily_counts"], day)
            item["active_tickets"] = len(item["active_ticket_ids"])
            item["recurrence_tickets"] = len(item["recurrence_ticket_ids"])
            for ticket in item["tickets_by_id"].values():
                _inc(item["ticket_status_counts"], normalize_business_status(ticket.business_status))
                if ticket.priority_name:
                    _inc(item["ticket_priority_counts"], ticket.priority_name)
                reason = ClosingReasonCanonicalizer.display(ticket.closing_code_raw)
                if reason:
                    _inc(item["ticket_reason_counts"], reason)
            for context in item["ticket_context_by_id"].values():
                _inc(item["ticket_client_counts"], context["client"])
            item["ticket_project_counts"] = {
                label: len(ticket_ids)
                for label, ticket_ids in item["ticket_project_ticket_ids"].items()
            }
            item["ticket_provider_counts"] = {
                label: len(ticket_ids)
                for label, ticket_ids in item["ticket_provider_ticket_ids"].items()
            }
            for ticket in item["terminal_by_id"].values():
                reason = ClosingReasonCanonicalizer.display(ticket.closing_code_raw)
                if reason:
                    _inc(item["reason_counts"], reason)
            item.pop("tickets_by_id")
            item.pop("ticket_context_by_id")
            item.pop("ticket_project_ticket_ids")
            item.pop("ticket_provider_ticket_ids")
            item.pop("active_ticket_ids")
            item.pop("recurrence_ticket_ids")
            item.pop("terminal_by_id")

        reasons = sorted({reason for row in result.values() for reason in row["reason_counts"]})
        reason_options = set(reasons)
        if reason_filter:
            reason_options.add(ClosingReasonCanonicalizer.display(reason_filter))
        status_counts, priority_counts, ticket_reason_counts = {}, {}, {}
        for status in global_ticket_statuses.values():
            _inc(status_counts, status)
        if mode == "calls" or monthly_motives:
            for ticket in unlinked_rows:
                _inc(status_counts, normalize_business_status(ticket.business_status))
        for priority in global_ticket_priorities.values():
            if priority:
                _inc(priority_counts, priority)
        for reason in global_ticket_reasons.values():
            if reason:
                _inc(ticket_reason_counts, reason)
        recurrence_clients, recurrence_providers = {}, {}
        ticket_clients, ticket_projects, ticket_providers = {}, {}, {}
        for row in result.values():
            for label, amount in row["recurrence_client_counts"].items():
                _inc(recurrence_clients, label, amount)
            for label, amount in row["recurrence_provider_counts"].items():
                _inc(recurrence_providers, label, amount)
        selected_context_ids = global_terminal if mode == "motives" else global_tickets
        for ticket_id in selected_context_ids:
            context = global_ticket_contexts.get(ticket_id)
            if not context:
                continue
            _inc(ticket_clients, context["client"])
            _inc(ticket_projects, context["project"])
            _inc(ticket_providers, context["provider"])
        metric_fields = {"base": "total_links", "projects": "total_links", "calls": "ticket_count",
                         "repeat": "recurrence_links", "motives": "motives"}
        metric = metric_fields[mode]
        daily_counts, technical_daily_counts, category_counts = {}, {}, {}
        for ticket in unlinked_rows:
            cat = IncidentRules.category(ticket.closing_code_raw)
            global_ticket_categories[ticket.pk] = cat
            if ticket.source_created_at:
                global_ticket_dates[ticket.pk] = timezone.localtime(ticket.source_created_at).date().isoformat()
            if cat not in {None, "financeiro", "sem_classificacao"}:
                weather_events.append({
                    "uf": "", "city": "", "link": None, "ticket": ticket.pk,
                    "at": ticket.source_created_at, "category": cat,
                })
        for ticket_id, day in global_ticket_dates.items():
            _inc(daily_counts, day)
            if global_ticket_categories.get(ticket_id) not in {None, "financeiro", "sem_classificacao"}:
                _inc(technical_daily_counts, day)
        for category in global_ticket_categories.values():
            _inc(category_counts, IncidentRules.LABELS[category])
        incident_total = len(global_tickets) + (unlinked_ticket_count if mode == "calls" or monthly_motives else 0)
        unclassified = category_counts.get(IncidentRules.LABELS["sem_classificacao"],0)
        from .weather_context import correlate
        correlation = {}
        if mode in {"calls","motives","repeat"}:
            correlation = correlate(weather_events,start,end,sum(c not in {None,"financeiro","sem_classificacao"} for c in global_ticket_categories.values()),scoped_cities=scoped_cities)
        region_category_counts = {
            reg["nome"]: {
                IncidentRules.LABELS[cat]: len(ids)
                for cat, ids in region_category_ticket_ids[reg["id"]].items()
                if ids
            }
            for reg in REGIONS
            if any(region_category_ticket_ids[reg["id"]].values())
        }
        period_comparison = {}
        if requested_mode in {"calls", "motives", "climate"} and start and end:
            period_comparison = cls._previous_period_comparison(
                user=user, scoped=scoped, ticket_candidates=ticket_candidates,
                start=start, end=end, mode=mode, monthly_motives=monthly_motives,
                client=client, project=project, provider=provider, uf_filter=uf_filter,
                region=region, status_filter=status_filter, priority_filter=priority_filter,
                reason_filter=reason_filter, arion_status=arion_status, assignee=assignee,
                provenance=provenance, params=params, current_total=incident_total,
            )
        return {
            "correlation":correlation,
            "operator_policy": IncidentRules.operator_policy(client if client != "all" else ""),
            "region_category_counts": region_category_counts,
            "period_comparison": period_comparison,
            "reconciliation":{"incidents":incident_total,"classified":sum(category_counts.values())-unclassified,
                              "unclassified":unclassified,"with_uf":len(located_ticket_ids),
                              "without_uf":incident_total-len(located_ticket_ids),
                              "affected_links":len(affected_link_ids),"recurrent_links":len(recurrent_link_ids),
                              "state_occurrences":sum(row["ticket_count"] for row in result.values())},
            "region": region, "allowed_ufs": allowed_ufs,
            "daily_counts": daily_counts, "technical_daily_counts": technical_daily_counts,
            "category_counts": category_counts,
            "mode": requested_mode, "location_mode": "uf", "states": result,
            "source_total": source_total, "filtered_source_total": filtered_total,
            "client_identified_count": global_client_identified,
            "client_unidentified_count": global_client_unidentified,
            "client_coverage": round(global_client_identified / filtered_total * 100, 1) if filtered_total else 0,
            "valid_uf_count": filtered_total - invalid_uf_count, "invalid_uf_count": invalid_uf_count,
            "ticket_count": incident_total,
            "unlinked_ticket_count": unlinked_ticket_count,
            "active_ticket_count": len(active_ids),
            "affected_link_count": len(affected_link_ids),
            "recurrence_link_count": len(recurrent_link_ids),
            "recurrence_ticket_count": len(global_recurrence_ticket_ids),
            "terminal_ticket_count": len(global_terminal) + (unlinked_ticket_count if mode == "motives" else 0), "metric": metric,
            "terminal_link_count": sum(row["terminal_links"] for row in result.values()),
            "ticket_status_counts": status_counts,
            "ticket_priority_counts": priority_counts, "priority_options": priority_options,
            "ticket_reason_counts": ticket_reason_counts,
            "ticket_client_counts": ticket_clients,
            "ticket_project_counts": ticket_projects,
            "ticket_provider_counts": ticket_providers,
            "recurrence_client_counts": recurrence_clients,
            "recurrence_provider_counts": recurrence_providers,
            "arion_statuses": [{"value": value, "label": label} for value, label in TicketTracking.InternalStatus.choices],
            "assignees": cls._assignee_options(user, scoped),
            "provenance_options": [{"value": value, "label": value.title()} for value in VALID_PROCEDENCE],
            "clients": clients, "projects": project_options,
            "project_colors":{name:color for name,color in getattr(settings,"GEO_PROJECT_COLORS",{}).items() if name in project_options and re.fullmatch(r"#[0-9a-fA-F]{6}",str(color))},
            "selected_project": project if project in project_options else "",
            "providers": [{"id": row["operator_id"], "name": row["operator__name"]} for row in providers],
            "reasons": reasons, "reason_options": [
                {**option, "raw_values": list(ClosingReasonCanonicalizer.raw_values(option["value"]))}
                for option in ClosingReasonCanonicalizer.options(reason_options)
            ],
            "period": {"date_from": start.isoformat() if start else "", "date_to": end.isoformat() if end else ""},
        }

    @staticmethod
    def previous_period_bounds(start, end):
        if start.year == end.year and start.month == end.month and start.day == 1:
            month_last = calendar.monthrange(start.year, start.month)[1]
            prev_month_end = start - timedelta(days=1)
            prev_start = prev_month_end.replace(day=1)
            if end.day >= month_last:
                return prev_start, prev_month_end, "Mês anterior completo", False
            prev_end = prev_month_end.replace(day=min(end.day, prev_month_end.day))
            return prev_start, prev_end, "Janela equivalente do mês anterior", True
        span_days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days - 1)
        return prev_start, prev_end, "Período anterior equivalente", False

    @classmethod
    def _previous_period_comparison(
        cls, *, user, scoped, ticket_candidates, start, end, mode, monthly_motives,
        client, project, provider, uf_filter, region, status_filter, priority_filter,
        reason_filter, arion_status, assignee, provenance, params, current_total,
    ):
        prev_start, prev_end, criterion, is_partial = cls.previous_period_bounds(start, end)
        if prev_start.year < 2020:
            return {}
        prev_start_at, prev_end_exclusive = _datetime_bounds(prev_start, prev_end)
        valid_tickets = IncidentRules.valid_for_operator(TicketQueryService.noc_tickets(), client=client)
        relations = OTRSTicketConfigItem.objects.filter(
            is_present_in_source=True, ticket__in=valid_tickets, cmdb_link__in=ticket_candidates,
            ticket__responsibility_normalized__in=(provenance,) if provenance else VALID_PROCEDENCE,
        )
        if mode == "motives" and not monthly_motives:
            relations = relations.annotate(**{
                OPERATIONAL_RESOLUTION_FIELD: operational_resolution_expression("ticket__"),
            }).filter(business_statuses_query("ticket__business_status", TERMINAL_TICKET_STATES)).exclude(
                ticket__closing_code_raw="",
            ).exclude(**{f"{OPERATIONAL_RESOLUTION_FIELD}__isnull": True}).filter(**{
                f"{OPERATIONAL_RESOLUTION_FIELD}__gte": prev_start_at,
                f"{OPERATIONAL_RESOLUTION_FIELD}__lt": prev_end_exclusive,
            })
            if reason_filter:
                relations = relations.filter(ticket__closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(reason_filter))
        else:
            relations = relations.filter(business_statuses_query("ticket__business_status", VALID_TICKET_STATES)).filter(
                ticket__source_created_at__gte=prev_start_at,
                ticket__source_created_at__lt=prev_end_exclusive,
            )
        if status_filter:
            relations = relations.filter(business_status_query("ticket__business_status", status_filter))
        if priority_filter:
            relations = relations.filter(ticket__priority_name=priority_filter)
        relations = IncidentRules.filter(filter_tracking_relations(relations, arion_status=arion_status, assignee=assignee), params, "ticket__")

        prev_categories = {}
        seen_ids = set()
        identity_cache = {}
        for rel in relations.select_related("ticket", "cmdb_link").only(
            "ticket_id", "ticket__title", "ticket__closing_code_raw", "ticket__business_status",
            "ticket__responsibility_normalized", "cmdb_link__contracting_client", "cmdb_link__business_project",
        ).iterator(chunk_size=1000):
            t = rel.ticket
            if t.pk in seen_ids:
                continue
            parsed = identity_cache.get(t.pk)
            if parsed is None:
                parsed = TicketBusinessIdentityService.parse(t.title)
                identity_cache[t.pk] = parsed
            link_client = BusinessIdentityRules.client_for_token(rel.cmdb_link.contracting_client) or rel.cmdb_link.contracting_client
            t_client = parsed.client or link_client
            if not IncidentRules.is_valid_ticket(t, t_client):
                continue
            t_project = parsed.project or rel.cmdb_link.business_project
            if not _client_label_matches(t_client, client) or (project and canonical_project_name(t_project) != project):
                continue
            seen_ids.add(t.pk)
            prev_categories[t.pk] = IncidentRules.category(t.closing_code_raw)

        unlinked = cls._unlinked_ticket_queryset(
            user=user, scoped_links=scoped, mode="calls" if monthly_motives else mode,
            start_at=prev_start_at, end_exclusive=prev_end_exclusive, status_filter=status_filter,
            priority_filter=priority_filter, reason_filter=reason_filter,
            client=client, project=project, provider=provider, uf_filter=uf_filter,
            arion_status=arion_status, assignee=assignee, provenance=provenance,
        )
        unlinked = IncidentRules.filter(IncidentRules.valid_for_operator(unlinked, client=client), params)
        if not region:
            for ticket in unlinked.only("pk", "title", "closing_code_raw", "business_status", "responsibility_normalized").iterator(chunk_size=500):
                if ticket.pk in seen_ids:
                    continue
                if IncidentRules.is_valid_ticket(ticket, TicketBusinessIdentityService.parse(ticket.title).client):
                    seen_ids.add(ticket.pk)
                    prev_categories[ticket.pk] = IncidentRules.category(ticket.closing_code_raw)

        prev_total = len(seen_ids)
        cat_counts = {}
        for cat in prev_categories.values():
            _inc(cat_counts, IncidentRules.LABELS[cat])
        delta = current_total - prev_total
        return {
            "date_from": prev_start.isoformat(),
            "date_to": prev_end.isoformat(),
            "current_from": start.isoformat(),
            "current_to": end.isoformat(),
            "current_days": (end - start).days + 1,
            "previous_days": (prev_end - prev_start).days + 1,
            "criterion": criterion,
            "is_partial": is_partial,
            "ticket_count": prev_total,
            "delta": delta,
            "delta_percent": round(delta / prev_total * 100, 1) if prev_total else None,
            "category_counts": cat_counts,
        }

    @staticmethod
    def _assignee_options(user, scoped_links):
        from django.contrib.auth import get_user_model

        scoped_tickets = TicketQueryService.base(user, links=scoped_links)
        assigned_ids = TicketTracking.objects.filter(
            ticket_external_id__in=scoped_tickets.values("external_id"),
            assigned_to__isnull=False,
        ).values("assigned_to_id")
        return [{
            "id": user_row.pk,
            "name": user_row.get_full_name() or user_row.get_username(),
        } for user_row in get_user_model().objects.filter(
            is_active=True, pk__in=assigned_ids,
        ).order_by("first_name", "last_name", "username")]


def _inc(counter, key, amount=1):
    counter[key] = counter.get(key, 0) + amount
