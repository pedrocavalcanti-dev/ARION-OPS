"""Historical CMDB plant vs canonical OTRS ticket volume & proportional rates."""
import calendar
from collections import defaultdict
from datetime import date, datetime, time, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import (
    CMDBPlantMonthlySlice,
    CMDBPlantMonthlySnapshot,
    IntegrationSyncState,
    OTRSTicketConfigItem,
)
from aplicativos.integracoes.services.analytics import LinkQueryService, TicketQueryService
from aplicativos.integracoes.services.cmdb_plant_snapshot import _is_canceled_state, _is_maintenance_state
from aplicativos.integracoes.services.geography import REGIONS, STATES, region_states, state_query
from aplicativos.integracoes.services.incidents import IncidentRules
from aplicativos.integracoes.services.project_names import (
    canonical_project_name,
    display_project_name,
    project_alias_groups,
    project_comparison_key,
)
from .analytics import AnalyticsQueryService, _month_buckets, _month_case


_MONTH_NAMES = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
_STATE_BY_KEY = {
    key: row["sigla"]
    for row in STATES
    for key in (row["sigla"], project_comparison_key(row["nome"]))
}
_REGION_BY_KEY = {
    key: row["sigla"]
    for row in REGIONS
    for key in (row["sigla"], str(row["id"]), project_comparison_key(row["nome"]))
}
_PRIMARY_OPERATORS = ("Claro", "Vivo", "Algar")
_UNIDENTIFIED_MARKERS = {"", "nao identificado", "não identificado", "sem identificacao", "sem identificação", "desconhecido"}


def _month_label(month_key):
    year, month = map(int, month_key.split("-"))
    return f"{_MONTH_NAMES[month - 1]}/{year % 100:02d}"


def _norm_uf(raw):
    key = project_comparison_key(raw)
    return _STATE_BY_KEY.get(key, "")


def _norm_region(raw):
    key = project_comparison_key(raw)
    if key in _REGION_BY_KEY:
        return _REGION_BY_KEY[key]
    raw_up = str(raw or "").strip().upper()
    return _REGION_BY_KEY.get(raw_up, "")


def _is_unidentified(value):
    return str(value or "").strip().casefold() in _UNIDENTIFIED_MARKERS


def _pct_delta(current, previous):
    if current is None or previous is None or previous <= 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _format_pt_number(value, decimals=1):
    if value is None:
        return "—"
    return f"{value:.{decimals}f}".replace(".", ",")


def _share_pct(part, whole):
    if not whole or whole <= 0:
        return 0.0
    return round((part / whole) * 100, 1)


def _scoped_plant_slices(user, snapshots):
    slices = CMDBPlantMonthlySlice.objects.filter(snapshot__in=snapshots)
    if user is None or DataScopeService.is_global(user):
        return slices
    operator_ids = DataScopeService.allowed_operator_ids(user)
    project_ids = DataScopeService.allowed_project_ids(user)
    if not operator_ids or not project_ids:
        return slices.none()
    allowed_operators = [
        IncidentRules.canonical_operator(name)
        for name in Operadora.objects.filter(pk__in=operator_ids).values_list("name", flat=True)
    ]
    allowed_projects = [
        canonical_project_name(name)
        for name in Projeto.objects.filter(pk__in=project_ids).values_list("name", flat=True)
    ]
    return slices.filter(
        contracting_client__in=allowed_operators,
        canonical_project__in=allowed_projects,
    )


def _resolve_range(params, now):
    local_today = timezone.localtime(now).date()
    start_year, start_month = local_today.year, local_today.month - 11
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    default_start = date(start_year, start_month, 1)
    default_end = local_today

    raw_start = str(params.get("start") or params.get("date_from") or "").strip()
    raw_end = str(params.get("end") or params.get("date_to") or "").strip()
    start = parse_date(raw_start) if raw_start else None
    end = parse_date(raw_end) if raw_end else None
    if start is None or end is None or start > end:
        start, end = default_start, default_end
    if end > local_today:
        end = local_today
    # Cap range at 36 months for predictable query performance.
    min_allowed = date(end.year - 3, end.month, 1)
    if start < min_allowed:
        start = min_allowed
    return start, end


def _build_live_overview(filtered_links, *, period_tickets, period_affected, period_recurrent, snapshots):
    """Build current CMDB plant breakdown separating registered, active, canceled & unidentified."""
    total_registered = 0
    active_links = 0
    operational_links = 0
    maintenance_links = 0
    canceled_links = 0
    unidentified_operator_links = 0
    unidentified_project_links = 0
    unidentified_links = 0

    # Operator buckets: Claro, Vivo, Algar, Outras, Sem identificação -> [total, active, canceled]
    op_buckets = {
        "claro": ["Claro", 0, 0, 0],
        "vivo": ["Vivo", 0, 0, 0],
        "algar": ["Algar", 0, 0, 0],
        "outras": ["Outras", 0, 0, 0],
        "unidentified": ["Sem identificação", 0, 0, 0],
    }
    project_buckets = defaultdict(lambda: [0, 0])  # label -> [total, active]

    rows = filtered_links.values_list(
        "contracting_client",
        "business_project",
        "legacy_project",
        "deployment_state",
    )
    for raw_client, biz_proj, leg_proj, deployment_state in rows.iterator(chunk_size=1000):
        total_registered += 1
        is_canc = _is_canceled_state(deployment_state)
        is_maint = _is_maintenance_state(deployment_state)
        if is_canc:
            canceled_links += 1
        else:
            active_links += 1
            if is_maint:
                maintenance_links += 1
            else:
                operational_links += 1

        canonical_op = IncidentRules.canonical_operator(raw_client or "")
        op_missing = _is_unidentified(canonical_op)
        if op_missing:
            unidentified_operator_links += 1
            op_key = "unidentified"
        elif canonical_op == "Claro":
            op_key = "claro"
        elif canonical_op == "Vivo":
            op_key = "vivo"
        elif canonical_op == "Algar":
            op_key = "algar"
        else:
            op_key = "outras"

        op_buckets[op_key][1] += 1
        if is_canc:
            op_buckets[op_key][3] += 1
        else:
            op_buckets[op_key][2] += 1

        raw_proj = (biz_proj or leg_proj or "").strip()
        canonical_proj = canonical_project_name(raw_proj) if raw_proj else ""
        proj_missing = _is_unidentified(canonical_proj)
        if proj_missing:
            unidentified_project_links += 1
            proj_label = "Sem identificação"
        else:
            proj_label = display_project_name(canonical_proj)
        project_buckets[proj_label][0] += 1
        if not is_canc:
            project_buckets[proj_label][1] += 1

        if op_missing or proj_missing:
            unidentified_links += 1

    operator_distribution = []
    for key, (label, tot, act, canc) in op_buckets.items():
        pct = _share_pct(tot, total_registered)
        act_pct = _share_pct(act, active_links)
        operator_distribution.append({
            "key": key,
            "label": label,
            "total": tot,
            "active": act,
            "canceled": canc,
            "share_pct": pct,
            "share_label": f"{_format_pt_number(pct, 1)}%",
            "active_share_pct": act_pct,
            "active_share_label": f"{_format_pt_number(act_pct, 1)}%",
        })

    # Top projects (keep "Sem identificação" explicitly separate at the end if present)
    unid_proj_counts = project_buckets.pop("Sem identificação", None)
    sorted_projects = sorted(
        project_buckets.items(),
        key=lambda item: (-item[1][1], -item[1][0], item[0].casefold()),
    )
    top_limit = 10
    project_distribution = []
    for label, (tot, act) in sorted_projects[:top_limit]:
        pct = _share_pct(tot, total_registered)
        project_distribution.append({
            "label": label,
            "total": tot,
            "active": act,
            "share_pct": pct,
            "share_label": f"{_format_pt_number(pct, 1)}%",
            "is_unidentified": False,
        })
    if len(sorted_projects) > top_limit:
        rest_tot = sum(item[1][0] for item in sorted_projects[top_limit:])
        rest_act = sum(item[1][1] for item in sorted_projects[top_limit:])
        pct = _share_pct(rest_tot, total_registered)
        project_distribution.append({
            "label": f"Outros projetos ({len(sorted_projects) - top_limit})",
            "total": rest_tot,
            "active": rest_act,
            "share_pct": pct,
            "share_label": f"{_format_pt_number(pct, 1)}%",
            "is_unidentified": False,
        })
    if unid_proj_counts and unid_proj_counts[0] > 0:
        pct = _share_pct(unid_proj_counts[0], total_registered)
        project_distribution.append({
            "label": "Sem identificação",
            "total": unid_proj_counts[0],
            "active": unid_proj_counts[1],
            "share_pct": pct,
            "share_label": f"{_format_pt_number(pct, 1)}%",
            "is_unidentified": True,
        })

    # Last CMDB sync reference timestamp
    sync_state = IntegrationSyncState.objects.filter(key="cmdb").first()
    ref_dt = sync_state.last_success_at if (sync_state and sync_state.last_success_at) else None
    if ref_dt is None:
        ref_dt = filtered_links.aggregate(latest=Max("source_synced_at")).get("latest")
    if ref_dt is None and snapshots:
        ref_dt = snapshots[-1].captured_at
    if ref_dt is not None:
        local_ref = timezone.localtime(ref_dt)
        cmdb_last_sync_iso = local_ref.isoformat()
        cmdb_last_sync_label = local_ref.strftime("%d/%m/%Y %H:%M")
        reference_date_label = local_ref.strftime("%d/%m/%Y")
    else:
        cmdb_last_sync_iso = ""
        cmdb_last_sync_label = "Sem sincronização registrada"
        reference_date_label = "—"

    period_rate = round((period_tickets / active_links) * 100, 1) if active_links > 0 else None
    period_affected_pct = round((period_affected / active_links) * 100, 1) if active_links > 0 else None

    return {
        "total_registered": total_registered,
        "active_links": active_links,
        "operational_links": operational_links,
        "maintenance_links": maintenance_links,
        "canceled_links": canceled_links,
        "unidentified_links": unidentified_links,
        "unidentified_operator_links": unidentified_operator_links,
        "unidentified_project_links": unidentified_project_links,
        "active_share_label": f"{_format_pt_number(_share_pct(active_links, total_registered), 1)}%",
        "canceled_share_label": f"{_format_pt_number(_share_pct(canceled_links, total_registered), 1)}%",
        "unidentified_share_label": f"{_format_pt_number(_share_pct(unidentified_links, total_registered), 1)}%",
        "operator_distribution": operator_distribution,
        "project_distribution": project_distribution,
        "cmdb_last_sync_at": cmdb_last_sync_iso,
        "cmdb_last_sync_label": cmdb_last_sync_label,
        "reference_date_label": reference_date_label,
        "period_totals": {
            "tickets": period_tickets,
            "affected_links": period_affected,
            "recurrent_links": period_recurrent,
            "tickets_per_100_active": period_rate,
            "tickets_per_100_active_label": _format_pt_number(period_rate, 1),
            "affected_pct_active": period_affected_pct,
            "affected_pct_active_label": f"{_format_pt_number(period_affected_pct, 1)}%" if period_affected_pct is not None else "—",
        },
    }


class PlantHistoryService:
    """Compare monthly canonical ticket demand against verified monthly CMDB plant size."""

    @classmethod
    def build(cls, user, params=None, *, now=None):
        params = params or {}
        now = now or timezone.now()
        zone = ZoneInfo(settings.TIME_ZONE)
        local_today = timezone.localtime(now).date()
        start, end = _resolve_range(params, now)

        selected_operator = IncidentRules.canonical_operator(str(params.get("operator") or "").strip())
        if selected_operator.lower() in {"all", "todos", "todas"}:
            selected_operator = ""
        selected_project_raw = str(params.get("project") or "").strip()
        if selected_project_raw.lower() in {"all", "todos", "todas"}:
            selected_project_raw = ""
        selected_project = canonical_project_name(selected_project_raw) if selected_project_raw else ""
        selected_region = _norm_region(params.get("region") or "")
        selected_uf = _norm_uf(params.get("uf") or "")

        include_cancelled = str(params.get("include_cancelled", "true")).lower() not in {"false", "0", "no"}
        include_improcedente = str(params.get("include_improcedente", "true")).lower() not in {"false", "0", "no"}

        allowed_links = LinkQueryService.base(user)
        # Filter options (strictly within user's Data Scope)
        link_catalog_rows = list(
            allowed_links.order_by().values_list(
                "contracting_client", "business_project", "legacy_project", "state",
            ).distinct()
        )
        raw_project_names = []
        operator_set = set()
        uf_set = set()
        for raw_client, biz_proj, leg_proj, raw_state in link_catalog_rows:
            op = IncidentRules.canonical_operator(raw_client or "")
            if op:
                operator_set.add(op)
            if biz_proj:
                raw_project_names.append(biz_proj)
            if leg_proj:
                raw_project_names.append(leg_proj)
            uf = _norm_uf(raw_state)
            if uf:
                uf_set.add(uf)

        project_groups = project_alias_groups(raw_project_names)
        project_options = sorted(
            {display_project_name(key) for key in project_groups},
            key=str.casefold,
        )
        operator_options = sorted(
            operator_set,
            key=lambda item: ({"Claro": 0, "Vivo": 1, "Algar": 2}.get(item, 3), item.casefold()),
        )
        uf_options = sorted(uf_set)
        region_options = [
            {"code": r["sigla"], "name": r["nome"]}
            for r in REGIONS
            if any(uf in uf_set for uf in region_states(r["sigla"]))
        ]

        if selected_operator and selected_operator not in operator_set:
            selected_operator = ""
        if selected_project and selected_project not in project_groups:
            selected_project = ""
        if selected_uf and selected_uf not in uf_set:
            selected_uf = ""
        region_ufs = region_states(selected_region) if selected_region else []
        if selected_region and not any(uf in uf_set for uf in region_ufs):
            selected_region = ""
            region_ufs = []

        # Apply user filters to scoped links for overview and ticket filtering.
        filtered_links = allowed_links
        if selected_operator:
            filtered_links = filtered_links.filter(contracting_client__iexact=selected_operator)
        if selected_project:
            aliases = project_groups.get(selected_project, (selected_project,))
            filtered_links = filtered_links.filter(
                Q(business_project__in=aliases) | Q(business_project="", legacy_project__in=aliases)
            )
        if selected_region and region_ufs:
            filtered_links = filtered_links.filter(state_query(region_ufs, field="state"))
        if selected_uf:
            filtered_links = filtered_links.filter(state_query([selected_uf], field="state"))

        has_dimension_filter = bool(selected_operator or selected_project or selected_region or selected_uf)

        # Month buckets for the requested range.
        buckets = _month_buckets(start, end, zone)
        if not buckets:
            buckets = _month_buckets(local_today.replace(day=1), local_today, zone)
        range_start_utc = buckets[0][1]
        range_end_utc = buckets[-1][2]
        # Respect the exact requested end date if inside the last month.
        exact_end_local = datetime.combine(end, time.max, tzinfo=zone).astimezone(datetime_timezone.utc)
        query_end_utc = min(range_end_utc, exact_end_local)

        # Canonical created tickets (100% consistent with Dashboard & Cockpit).
        base_tickets = TicketQueryService.base(user, links=allowed_links).order_by()
        canonical_tickets = AnalyticsQueryService.canonical_created_tickets(
            base_tickets,
            include_cancelled=include_cancelled,
            include_improcedente=include_improcedente,
        ).filter(
            source_created_at__gte=range_start_utc,
            source_created_at__lt=query_end_utc,
        )
        if has_dimension_filter:
            matching_rel = OTRSTicketConfigItem.objects.filter(
                ticket_id=OuterRef("pk"),
                is_present_in_source=True,
                cmdb_link__in=filtered_links,
            )
            canonical_tickets = canonical_tickets.annotate(
                _matches_plant_filter=Exists(matching_rel),
            ).filter(_matches_plant_filter=True)

        month_expr = _month_case("source_created_at", buckets)
        ticket_counts_by_month = {
            row["month_key"]: row["total"]
            for row in canonical_tickets.annotate(month_key=month_expr)
            .exclude(month_key__isnull=True)
            .values("month_key")
            .annotate(total=Count("pk", distinct=True))
        }

        # Distinct affected links & recurrent links (2+ distinct canonical tickets) per month & across period.
        rel_month_expr = _month_case("ticket__source_created_at", buckets)
        rel_qs = OTRSTicketConfigItem.objects.filter(
            is_present_in_source=True,
            ticket_id__in=canonical_tickets.values("pk"),
            cmdb_link__in=filtered_links,
        ).annotate(month_key=rel_month_expr).exclude(month_key__isnull=True)

        affected_by_month = defaultdict(int)
        recurrent_by_month = defaultdict(int)
        for row in rel_qs.values("month_key", "config_item_id").annotate(
            ticket_cnt=Count("ticket_id", distinct=True),
        ):
            mkey = row["month_key"]
            affected_by_month[mkey] += 1
            if row["ticket_cnt"] >= 2:
                recurrent_by_month[mkey] += 1

        period_affected = 0
        period_recurrent = 0
        for row in rel_qs.values("config_item_id").annotate(
            ticket_cnt=Count("ticket_id", distinct=True),
        ):
            period_affected += 1
            if row["ticket_cnt"] >= 2:
                period_recurrent += 1
        period_tickets = sum(ticket_counts_by_month.values())

        # Load verified monthly CMDB plant snapshots (never fabricates missing months).
        month_dates = [date(int(k.split("-")[0]), int(k.split("-")[1]), 1) for k, _s, _e in buckets]
        snapshots = list(CMDBPlantMonthlySnapshot.objects.filter(month__in=month_dates).order_by("month"))
        snapshot_by_key = {snap.month.strftime("%Y-%m"): snap for snap in snapshots}

        scoped_slices = _scoped_plant_slices(user, snapshots)
        if selected_operator:
            scoped_slices = scoped_slices.filter(contracting_client__iexact=selected_operator)
        if selected_project:
            scoped_slices = scoped_slices.filter(canonical_project__iexact=selected_project)
        if selected_region and region_ufs:
            scoped_slices = scoped_slices.filter(uf__in=region_ufs)
        if selected_uf:
            scoped_slices = scoped_slices.filter(uf__iexact=selected_uf)

        slice_totals_by_snap = {
            row["snapshot_id"]: (row["active_sum"] or 0, row["total_sum"] or 0, row["canceled_sum"] or 0)
            for row in scoped_slices.values("snapshot_id").annotate(
                active_sum=Sum("active_links"),
                total_sum=Sum("total_links"),
                canceled_sum=Sum("canceled_links"),
            )
        }
        is_global_unfiltered = (user is None or DataScopeService.is_global(user)) and not has_dimension_filter

        # Assemble monthly series cleanly.
        series = []
        current_month_key = f"{local_today.year:04d}-{local_today.month:02d}"
        prev_row = None

        for month_key, _b_start, _b_end in buckets:
            year_i, month_i = map(int, month_key.split("-"))
            tickets = ticket_counts_by_month.get(month_key, 0)
            affected = affected_by_month.get(month_key, 0)
            recurrent = recurrent_by_month.get(month_key, 0)
            snap = snapshot_by_key.get(month_key)
            is_ongoing_month = (month_key == current_month_key and local_today.day < calendar.monthrange(year_i, month_i)[1])

            if snap is not None:
                if is_global_unfiltered and snap.slices.count() == 0:
                    active_links = snap.active_links
                    total_links = snap.total_links
                else:
                    active_links, total_links, _canc = slice_totals_by_snap.get(snap.pk, (0, 0, 0))
                is_partial = bool(snap.is_partial_month or is_ongoing_month)
                if snap.source == CMDBPlantMonthlySnapshot.Source.CMDB_IMPORT:
                    coverage_status = "PARTIAL"
                    coverage_label = f"Snapshot importado ({timezone.localtime(snap.captured_at):%d/%m/%Y})"
                elif is_partial:
                    coverage_status = "PARTIAL"
                    coverage_label = f"Mês parcial (até {timezone.localtime(snap.captured_at):%d/%m/%Y})"
                else:
                    coverage_status = "CONFIRMED"
                    coverage_label = f"Base confirmada ({timezone.localtime(snap.captured_at):%d/%m/%Y})"
                source_label = snap.get_source_display()
                captured_at_iso = timezone.localtime(snap.captured_at).strftime("%d/%m/%Y %H:%M")
                notes = snap.notes
            else:
                active_links = None
                total_links = None
                is_partial = is_ongoing_month
                coverage_status = "UNAVAILABLE"
                coverage_label = "Base histórica indisponível"
                source_label = "Sem snapshot CMDB"
                captured_at_iso = ""
                notes = "Sem snapshot mensal da planta CMDB para este mês; taxas proporcionais omitidas."

            if active_links is not None and active_links > 0:
                tickets_per_100 = round((tickets / active_links) * 100, 2)
                affected_pct = round((affected / active_links) * 100, 2)
            else:
                tickets_per_100 = None
                affected_pct = None

            mom_tickets = _pct_delta(tickets, prev_row["tickets"] if prev_row else None)
            mom_plant = _pct_delta(active_links, prev_row["active_links"] if prev_row else None)
            mom_rate = _pct_delta(tickets_per_100, prev_row["tickets_per_100_links"] if prev_row else None)

            row = {
                "month": month_key,
                "label": _month_label(month_key),
                "tickets": tickets,
                "affected_links": affected,
                "recurrent_links": recurrent,
                "active_links": active_links,
                "total_links": total_links,
                "tickets_per_100_links": tickets_per_100,
                "tickets_per_100_label": _format_pt_number(tickets_per_100, 1),
                "affected_links_pct": affected_pct,
                "affected_links_pct_label": f"{_format_pt_number(affected_pct, 1)}%" if affected_pct is not None else "—",
                "mom_tickets_pct": mom_tickets,
                "mom_plant_pct": mom_plant,
                "mom_rate_pct": mom_rate,
                "is_partial_month": is_partial,
                "coverage_status": coverage_status,
                "coverage_label": coverage_label,
                "source_label": source_label,
                "captured_at": captured_at_iso,
                "notes": notes,
            }
            series.append(row)
            prev_row = row

        covered_rows = [r for r in series if r["active_links"] is not None]
        latest_covered = covered_rows[-1] if covered_rows else None
        latest_full_covered = next((r for r in reversed(covered_rows) if not r["is_partial_month"]), None)

        overview = _build_live_overview(
            filtered_links,
            period_tickets=period_tickets,
            period_affected=period_affected,
            period_recurrent=period_recurrent,
            snapshots=snapshots,
        )

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "selected": {
                "operator": selected_operator,
                "project": display_project_name(selected_project) if selected_project else "",
                "region": selected_region,
                "uf": selected_uf,
                "include_cancelled": include_cancelled,
                "include_improcedente": include_improcedente,
            },
            "filter_options": {
                "operators": operator_options,
                "projects": project_options,
                "regions": region_options,
                "ufs": uf_options,
            },
            "overview": overview,
            "series": series,
            "summary": {
                "months_total": len(series),
                "months_with_plant": len(covered_rows),
                "months_unavailable": len(series) - len(covered_rows),
                "latest_month_label": latest_covered["label"] if latest_covered else "—",
                "latest_active_links": latest_covered["active_links"] if latest_covered else None,
                "latest_tickets": latest_covered["tickets"] if latest_covered else 0,
                "latest_tickets_per_100": latest_covered["tickets_per_100_links"] if latest_covered else None,
                "latest_tickets_per_100_label": latest_covered["tickets_per_100_label"] if latest_covered else "—",
                "latest_affected_pct_label": latest_covered["affected_links_pct_label"] if latest_covered else "—",
                "latest_is_partial": latest_covered["is_partial_month"] if latest_covered else False,
                "latest_coverage_label": latest_covered["coverage_label"] if latest_covered else "Base histórica indisponível",
                "latest_full_month_label": latest_full_covered["label"] if latest_full_covered else "",
                "latest_full_tickets_per_100_label": latest_full_covered["tickets_per_100_label"] if latest_full_covered else "—",
                "latest_full_active_links": latest_full_covered["active_links"] if latest_full_covered else None,
            },
            "methodology_note": (
                "Volume de chamados segue a regra canônica do Dashboard/Cockpit (fila NOC - ATACADO). "
                "A planta mensal usa apenas snapshots verificados do CMDB (sincronizações e importações registradas). "
                "Meses anteriores ao primeiro snapshot exibem 'Base histórica indisponível' sem retroprojetar a planta atual."
            ),
        }
