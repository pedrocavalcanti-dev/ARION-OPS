"""Idempotent capture of monthly CMDB plant snapshots (never fabricates history)."""
import calendar
from collections import defaultdict
from datetime import date

from django.db import transaction
from django.utils import timezone

from aplicativos.base_links.models import CMDBImport, CMDBLink
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    CMDBPlantMonthlySlice,
    CMDBPlantMonthlySnapshot,
)
from aplicativos.integracoes.services.geography import STATES
from aplicativos.integracoes.services.incidents import IncidentRules
from aplicativos.integracoes.services.project_names import canonical_project_name, project_comparison_key


_STATE_BY_KEY = {
    key: row["sigla"]
    for row in STATES
    for key in (row["sigla"], project_comparison_key(row["nome"]))
}


def _norm_uf(raw):
    key = project_comparison_key(raw)
    return _STATE_BY_KEY.get(key, str(raw or "").strip().upper()[:2] if len(str(raw or "").strip()) == 2 else "")


def _is_canceled_state(deployment_state):
    return "cancel" in str(deployment_state or "").casefold()


def _is_maintenance_state(deployment_state):
    folded = str(deployment_state or "").casefold()
    return "manut" in folded or "maint" in folded


def _is_partial_month(local_day):
    last_day = calendar.monthrange(local_day.year, local_day.month)[1]
    return local_day.day < last_day


@transaction.atomic
def capture_current_cmdb_plant_snapshot(*, now=None, source=CMDBPlantMonthlySnapshot.Source.CMDB_SYNC, source_run=None):
    """Capture/update the current month's CMDB plant snapshot from CMDBLinkMirror."""
    now = now or timezone.now()
    local_day = timezone.localtime(now).date()
    month_date = date(local_day.year, local_day.month, 1)
    partial = _is_partial_month(local_day)

    links = CMDBLinkMirror.objects.filter(is_present_in_source=True).values_list(
        "contracting_client",
        "business_project",
        "legacy_project",
        "state",
        "deployment_state",
    )

    total = active = canceled = maintenance = 0
    slice_counts = defaultdict(lambda: [0, 0, 0])  # [total, active, canceled]

    for raw_client, business_project, legacy_project, raw_state, deployment_state in links.iterator(chunk_size=1000):
        total += 1
        is_canc = _is_canceled_state(deployment_state)
        is_maint = _is_maintenance_state(deployment_state)
        if is_canc:
            canceled += 1
        else:
            active += 1
        if is_maint:
            maintenance += 1

        operator = IncidentRules.canonical_operator(raw_client or "")[:120]
        project = canonical_project_name(business_project or legacy_project or "")[:160]
        uf = _norm_uf(raw_state)
        bucket = slice_counts[(operator, project, uf)]
        bucket[0] += 1
        if is_canc:
            bucket[2] += 1
        else:
            bucket[1] += 1

    notes = (
        f"Snapshot CMDB sincronizado em {timezone.localtime(now):%d/%m/%Y %H:%M} (mês em andamento)."
        if partial
        else f"Snapshot CMDB sincronizado em {timezone.localtime(now):%d/%m/%Y %H:%M}."
    )
    snapshot, _created = CMDBPlantMonthlySnapshot.objects.update_or_create(
        month=month_date,
        defaults={
            "captured_at": now,
            "source": source,
            "coverage_status": (
                CMDBPlantMonthlySnapshot.CoverageStatus.PARTIAL
                if partial
                else CMDBPlantMonthlySnapshot.CoverageStatus.CONFIRMED
            ),
            "is_partial_month": partial,
            "total_links": total,
            "active_links": active,
            "canceled_links": canceled,
            "maintenance_links": maintenance,
            "source_run": source_run,
            "notes": notes,
        },
    )
    snapshot.slices.all().delete()
    CMDBPlantMonthlySlice.objects.bulk_create(
        [
            CMDBPlantMonthlySlice(
                snapshot=snapshot,
                contracting_client=operator,
                canonical_project=project,
                uf=uf,
                total_links=counts[0],
                active_links=counts[1],
                canceled_links=counts[2],
            )
            for (operator, project, uf), counts in slice_counts.items()
        ],
        batch_size=500,
    )
    return snapshot


@transaction.atomic
def capture_legacy_import_plant_snapshot():
    """Backfill ONLY verifiable historical month(s) that have a real CMDBImport + CMDBLink table.

    Never fabricates or extrapolates months without an actual CMDB import/sync snapshot.
    Never overwrites an existing CMDB_SYNC snapshot.
    """
    latest_import = CMDBImport.objects.order_by("-created_at").first()
    if latest_import is None:
        return None
    import_day = timezone.localtime(latest_import.created_at).date()
    month_date = date(import_day.year, import_day.month, 1)

    existing = CMDBPlantMonthlySnapshot.objects.filter(month=month_date).first()
    if existing and existing.source == CMDBPlantMonthlySnapshot.Source.CMDB_SYNC:
        return existing

    links = CMDBLink.objects.filter(is_present_in_latest_import=True).select_related("operadora", "projeto").values_list(
        "operadora__name",
        "projeto__name",
        "uf",
        "deployment_state",
    )
    total = active = canceled = maintenance = 0
    slice_counts = defaultdict(lambda: [0, 0, 0])
    for op_name, proj_name, raw_uf, deployment_state in links.iterator(chunk_size=1000):
        total += 1
        is_canc = _is_canceled_state(deployment_state)
        is_maint = _is_maintenance_state(deployment_state)
        if is_canc:
            canceled += 1
        else:
            active += 1
        if is_maint:
            maintenance += 1

        operator = IncidentRules.canonical_operator(op_name or "")[:120]
        project = canonical_project_name(proj_name or "")[:160]
        uf = _norm_uf(raw_uf)
        bucket = slice_counts[(operator, project, uf)]
        bucket[0] += 1
        if is_canc:
            bucket[2] += 1
        else:
            bucket[1] += 1

    if total == 0:
        return None

    notes = f"Snapshot importado do CMDB ({latest_import.filename}) em {timezone.localtime(latest_import.created_at):%d/%m/%Y} — cobertura parcial do mês."
    snapshot, _ = CMDBPlantMonthlySnapshot.objects.update_or_create(
        month=month_date,
        defaults={
            "captured_at": latest_import.created_at,
            "source": CMDBPlantMonthlySnapshot.Source.CMDB_IMPORT,
            "coverage_status": CMDBPlantMonthlySnapshot.CoverageStatus.PARTIAL,
            "is_partial_month": False,
            "total_links": total,
            "active_links": active,
            "canceled_links": canceled,
            "maintenance_links": maintenance,
            "notes": notes[:255],
        },
    )
    snapshot.slices.all().delete()
    CMDBPlantMonthlySlice.objects.bulk_create(
        [
            CMDBPlantMonthlySlice(
                snapshot=snapshot,
                contracting_client=operator,
                canonical_project=project,
                uf=uf,
                total_links=counts[0],
                active_links=counts[1],
                canceled_links=counts[2],
            )
            for (operator, project, uf), counts in slice_counts.items()
        ],
        batch_size=500,
    )
    return snapshot
