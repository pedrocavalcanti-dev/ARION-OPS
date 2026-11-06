"""Queries immutable OTRS FULL snapshots for point-in-time backlog evidence."""

import unicodedata
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import (
    OTRSOperationalConfigItemSnapshot, OTRSOperationalSnapshot,
)
from aplicativos.integracoes.otrs_queues import (
    CANCELLATION_QUEUE_NAMES, COCKPIT_QUEUE_NAMES, NOC_QUEUE_NAME,
    queue_signature,
)
from aplicativos.integracoes.services.otrs_status import (
    BACKLOG_STATUSES, business_status_query,
)
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer


def backlog_snapshot_observations(*, user, allowed_links, scope, filters, buckets,
                                  period_start=None, period_end=None):
    """Return the last validated full snapshot observed in each selected bucket.

    Each value is a count at ``captured_at``. It is not projected to the end of
    a day/week/month. Missing buckets remain absent so the caller can leave a
    gap in the chart.
    """
    if not buckets:
        return {}
    coverage = queue_signature(COCKPIT_QUEUE_NAMES)
    first = period_start or min(bucket[2] for bucket in buckets)
    last = period_end or max(bucket[3] for bucket in buckets)
    snapshots = list(OTRSOperationalSnapshot.objects.filter(
        scope_signature=coverage,
        captured_at__gte=first,
        captured_at__lt=last,
    ).annotate(detail_count=Count("tickets")).order_by("captured_at", "pk"))
    if not snapshots:
        return {}

    is_global = user is None or DataScopeService.is_global(user)
    allowed_config_items = None
    if not is_global:
        allowed_config_items = set(allowed_links.values_list("source_config_item_id", flat=True))
        if not allowed_config_items:
            return {}

    snapshots_by_bucket = {}
    for snapshot in snapshots:
        # A partial detail write can never be treated as stock coverage.
        if snapshot.detail_count != snapshot.ticket_count:
            continue
        for key, _label, bucket_start, bucket_end in buckets:
            if bucket_start <= snapshot.captured_at < bucket_end:
                snapshots_by_bucket.setdefault(key, []).append(snapshot)
                break

    result = {}
    for key, candidates in snapshots_by_bucket.items():
        snapshot = max(candidates, key=lambda row: (row.captured_at, row.pk))
        tickets = snapshot.tickets.filter(
            business_status__in=BACKLOG_STATUSES,
        ).exclude(responsibility_normalized="IMPROCEDENTE")

        if scope == "support":
            tickets = tickets.filter(queue_name=NOC_QUEUE_NAME)
        elif scope == "cancellation":
            tickets = tickets.filter(queue_name__in=CANCELLATION_QUEUE_NAMES)
        else:
            tickets = tickets.filter(queue_name__in=COCKPIT_QUEUE_NAMES)

        direct_filters = {
            "service": "service_name", "sla": "sla_name", "queue": "queue_name",
            "responsibility": "responsibility_normalized", "stage": "cancellation_stage",
        }
        for filter_name, field_name in direct_filters.items():
            if filters.get(filter_name):
                tickets = tickets.filter(**{field_name: filters[filter_name]})

        if filters.get("origin") == "SUPPORT":
            tickets = tickets.filter(queue_name=NOC_QUEUE_NAME)
        elif filters.get("origin") == "CANCELLATION":
            tickets = tickets.filter(queue_name__in=CANCELLATION_QUEUE_NAMES)

        if filters.get("status"):
            tickets = tickets.filter(business_status_query("business_status", filters["status"]))
        source_state = filters.get("source_state")
        if source_state:
            if source_state.upper() in {"OPEN", "PENDING", "SCHEDULED", "RESOLVED", "CLOSED", "CANCELED", "UNKNOWN"}:
                tickets = tickets.filter(business_status_query("business_status", source_state))
            else:
                tickets = tickets.filter(external_state_name=source_state)
        if filters.get("priority") == "high-critical":
            names = tickets.exclude(priority_name="").values_list("priority_name", flat=True).distinct()
            high_critical = [name for name in names if unicodedata.normalize(
                "NFKD", name,
            ).encode("ascii", "ignore").decode().casefold() in {"alto", "critico"}]
            tickets = tickets.filter(priority_name__in=high_critical)
        elif filters.get("priority"):
            tickets = tickets.filter(priority_name=filters["priority"])
        reason = filters.get("termination_reason")
        if reason:
            tickets = tickets.filter(closing_code_raw__in=ClosingReasonCanonicalizer.raw_values(reason))
        if filters.get("responsible") == "unassigned":
            tickets = tickets.filter(Q(responsible_user_id__isnull=True) | Q(responsible_user_id=0))
        elif filters.get("responsible") == "assigned":
            tickets = tickets.filter(responsible_user_id__gt=0)
        if filters.get("age_bucket") == "OVER_24H":
            tickets = tickets.filter(source_created_at__lte=snapshot.captured_at - timedelta(hours=24))

        link_filters = filters.get("provider") or filters.get("uf") or filters.get("project")
        config_items = OTRSOperationalConfigItemSnapshot.objects.filter(
            ticket_snapshot__in=tickets,
        )
        if not is_global:
            config_items = config_items.filter(config_item_id__in=allowed_config_items)
            tickets = tickets.filter(config_items__config_item_id__in=allowed_config_items)
        if filters.get("provider"):
            config_items = config_items.filter(provider_name=filters["provider"])
        if filters.get("uf"):
            config_items = config_items.filter(uf=filters["uf"])
        if filters.get("project"):
            config_items = config_items.filter(
                Q(project=filters["project"])
                | Q(legacy_project=filters["project"])
                | Q(client=filters["project"]),
            )
        if link_filters:
            tickets = tickets.filter(config_items__in=config_items)
        result[key] = {
            "value": tickets.values("pk").distinct().count(),
            "captured_at": snapshot.captured_at,
            "snapshot_id": snapshot.pk,
            "ticket_count": snapshot.ticket_count,
            "coverage": snapshot.scope_signature,
        }
    return result
