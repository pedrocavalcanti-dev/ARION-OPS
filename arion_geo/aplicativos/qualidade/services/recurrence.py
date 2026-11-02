"""Operational occurrence episodes shared by Quality views and analytics."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicketConfigItem

from .constants import QUALITY_RREIP_DAYS
from .data_scope import QualityDataScopeService
from .ticket_validity import quality_ticket_sets

ACTIVE_RECURRENCE = "ACTIVE_RECURRENCE"
STABILIZED = "STABILIZED"
NEW_EPISODE = "NEW_EPISODE"
SINGLE_OCCURRENCE = "SINGLE_OCCURRENCE"


def _operational_level(recurrence_number):
    if recurrence_number <= 0:
        return "Inicial"
    return f"R{recurrence_number}" if recurrence_number <= 4 else "R5+"


@dataclass(frozen=True)
class OccurrenceEpisode:
    number: int
    started_at: object
    last_at: object
    occurrences: tuple
    recurrence_count: int
    active: bool
    state: str
    is_current: bool
    days_since_last: int
    stabilized_at: object
    operational_recurrence_level: str


def build_occurrence_episodes(occurrences, *, now=None, window_days=QUALITY_RREIP_DAYS):
    """Group valid occurrences; a gap greater than 30 days starts a new episode."""
    now = now or timezone.now()
    ordered = sorted(
        occurrences,
        key=lambda item: (item["occurred_at"], item.get("external_id", 0)),
    )
    episodes = []
    current = []
    previous_at = None
    for occurrence in ordered:
        occurred_at = occurrence["occurred_at"]
        if previous_at is not None and occurred_at - previous_at > timedelta(days=window_days):
            episodes.append(current)
            current = []
        current.append(occurrence)
        previous_at = occurred_at
    if current:
        episodes.append(current)

    result = []
    cutoff = now - timedelta(days=window_days)
    for number, items in enumerate(episodes, start=1):
        last_at = items[-1]["occurred_at"]
        recurrence_count = max(len(items) - 1, 0)
        is_current = number == len(episodes)
        active = recurrence_count > 0 and cutoff < last_at <= now
        is_stabilized = recurrence_count > 0 and last_at <= cutoff
        if active:
            state = ACTIVE_RECURRENCE
        elif is_stabilized:
            state = STABILIZED
        elif is_current and number > 1:
            state = NEW_EPISODE
        else:
            state = SINGLE_OCCURRENCE
        previous_recurrence = any(len(previous) > 1 for previous in episodes[: number - 1])
        stabilized_at = last_at + timedelta(days=window_days) if recurrence_count else None
        days_since_last = max((now - last_at).days, 0)
        for index, occurrence in enumerate(items):
            occurrence["episode_number"] = number
            occurrence["episode_position"] = index + 1
            occurrence["is_recurrence"] = index > 0
            occurrence["recurrence_number"] = index
            occurrence["operational_recurrence_level"] = _operational_level(index)
            occurrence["is_new_episode_after_stability"] = index == 0 and previous_recurrence
        result.append(OccurrenceEpisode(
            number=number,
            started_at=items[0]["occurred_at"],
            last_at=last_at,
            occurrences=tuple(items),
            recurrence_count=recurrence_count,
            active=active,
            state=state,
            is_current=is_current,
            days_since_last=days_since_last,
            stabilized_at=stabilized_at,
            operational_recurrence_level=_operational_level(recurrence_count),
        ))
    return tuple(result)


def build_quality_recurrence_analysis(
    user, *, scope_links=None, scoped_orders=None, base_tickets=None, now=None,
):
    """Load valid mirror tickets once and derive per-circuit episode context.

    The mirror is the source for operational occurrences. QualityOccurrence records
    enrich/review those tickets and can explicitly exclude a ticket through the
    shared validity policy; a review row is never required for a ticket to count.
    """
    now = now or timezone.now()
    scope_links = scope_links if scope_links is not None else DataScopeService.filter_queryset(
        user, CMDBLinkMirror.objects.filter(is_present_in_source=True).select_related("operator"),
    )
    scoped_orders = scoped_orders if scoped_orders is not None else QualityDataScopeService.orders_for(user)
    ticket_sets = quality_ticket_sets(
        user, scope_links=scope_links, scoped_orders=scoped_orders,
        base_tickets=base_tickets, end=now,
    )
    valid_tickets = ticket_sets["valid_tickets"].exclude(source_created_at__isnull=True)
    relations = OTRSTicketConfigItem.objects.filter(
        cmdb_link__in=scope_links,
        cmdb_link__is_present_in_source=True,
        is_present_in_source=True,
        ticket_id__in=valid_tickets.order_by().values("pk"),
    ).order_by().values_list(
        "cmdb_link__source_config_item_id",
        "ticket__external_id",
        "ticket__source_created_at",
    ).distinct()

    grouped = defaultdict(dict)
    for config_item_id, external_id, occurred_at in relations.iterator(chunk_size=2000):
        if config_item_id and occurred_at:
            grouped[config_item_id][external_id] = {
                "external_id": external_id,
                "occurred_at": occurred_at,
            }

    links = {}
    ticket_index = {}
    for config_item_id, by_ticket in grouped.items():
        episodes = build_occurrence_episodes(list(by_ticket.values()), now=now)
        current_episode = episodes[-1] if episodes else None
        active_recurrence = bool(current_episode and current_episode.active)
        all_occurrences = [item for episode in episodes for item in episode.occurrences]
        reopened_episodes = [
            episode for episode in episodes
            if episode.occurrences and episode.occurrences[0].get("is_new_episode_after_stability")
        ]
        for item in all_occurrences:
            ticket_index[(config_item_id, item["external_id"])] = item
        links[config_item_id] = {
            "episodes": episodes,
            "current_episode": current_episode,
            "is_active_recurrence": active_recurrence,
            "is_stabilized": bool(current_episode and current_episode.state == STABILIZED),
            "operational_state": current_episode.state,
            "operational_state_label": {
                ACTIVE_RECURRENCE: "Reincidente ativo",
                STABILIZED: "Estabilizado",
                NEW_EPISODE: "Novo episódio",
                SINGLE_OCCURRENCE: "Ocorrência isolada",
            }[current_episode.state],
            "episode_count": len(episodes),
            "current_episode_number": current_episode.number,
            "current_episode_started_at": current_episode.started_at,
            "current_episode_last_at": current_episode.last_at,
            "current_episode_days_since_last": current_episode.days_since_last,
            "current_episode_stabilized_at": current_episode.stabilized_at,
            "operational_recurrence_level": current_episode.operational_recurrence_level,
            "reopened_after_stability": bool(
                current_episode.occurrences
                and current_episode.occurrences[0].get("is_new_episode_after_stability")
            ),
            "reopened_episodes": reopened_episodes,
            "reopened_count": len(reopened_episodes),
            "recurrence_count": current_episode.recurrence_count,
            "current_episode_recurrence_count": current_episode.recurrence_count,
            "current_episode_occurrence_count": len(current_episode.occurrences),
            "valid_occurrence_count": len(all_occurrences),
            "total_recurrence_count": sum(episode.recurrence_count for episode in episodes),
            "last_occurrence": all_occurrences[-1]["occurred_at"] if all_occurrences else None,
            "first_occurrence": all_occurrences[0]["occurred_at"] if all_occurrences else None,
            "occurrences": all_occurrences,
        }
    return {
        "now": now,
        "scope_links": scope_links,
        "scoped_orders": scoped_orders,
        "period_tickets": ticket_sets["period_tickets"],
        "valid_tickets": valid_tickets,
        "links": links,
        "ticket_index": ticket_index,
    }


def episode_ticket_ids(analysis, config_item_id):
    """Return ticket external IDs with the centralized episode labels for one CI."""
    summary = analysis["links"].get(config_item_id, {})
    return {
        item["external_id"]: item
        for item in summary.get("occurrences", ())
    }
