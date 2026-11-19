"""Auditable operational recurrence level classification (R1, R2, R3, R4, R5+) for OQs."""
from django.utils import timezone

from ..models import QualityOrder


LEVEL_DISPLAY_LABELS = dict(QualityOrder.RecurrenceLevel.choices)


def level_code_from_recurrence_count(recurrence_count):
    """Map distinct valid recurrence count (occurrences - 1) to R1..R5+."""
    try:
        count = int(recurrence_count or 0)
    except (TypeError, ValueError):
        return ""
    if count <= 0:
        return ""
    if count == 1:
        return QualityOrder.RecurrenceLevel.R1
    if count == 2:
        return QualityOrder.RecurrenceLevel.R2
    if count == 3:
        return QualityOrder.RecurrenceLevel.R3
    if count == 4:
        return QualityOrder.RecurrenceLevel.R4
    return QualityOrder.RecurrenceLevel.R5_PLUS


def _fmt_date(value):
    if not value:
        return "—"
    return timezone.localtime(value).strftime("%d/%m/%Y")


def resolve_order_recurrence_level(order, recurrence_summary=None, *, has_link_in_scope=True):
    """Resolve the effective, auditable recurrence level for a QualityOrder.

    Rules:
    - Manually confirmed ``order.recurrence_level`` is always preserved.
    - Otherwise, the level is derived deterministically from distinct valid OTRS
      occurrences grouped into operational episodes (R1..R5+).
    - When history is unavailable or shows no confirmed recurrence (0 or 1 valid
      occurrence), returns ``A confirmar`` with an explicit reason instead of
      fabricating a level.
    """
    summary = recurrence_summary or {}
    episodes = summary.get("episodes") or ()
    current_episode = summary.get("current_episode")

    # Select the episode that justifies the operational classification:
    # prefer current episode when it has recurrences; if current episode only
    # has a single new occurrence after a prior recurrent episode, fall back to
    # the most recent recurrent episode as historical context while flagging it.
    basis_episode = current_episode
    basis_note = ""
    if current_episode and current_episode.recurrence_count <= 0:
        prior_recurrent = [ep for ep in episodes if ep.recurrence_count > 0]
        if prior_recurrent:
            basis_episode = prior_recurrent[-1]
            basis_note = (
                f"Baseado no último episódio reincidente (Episódio {basis_episode.number:02d}); "
                f"episódio atual ainda possui apenas 1 ocorrência válida."
            )

    if basis_episode:
        episode_occurrences = list(basis_episode.occurrences)
        ep_valid_count = len(episode_occurrences)
        ep_recurrence_count = basis_episode.recurrence_count
        period_start = basis_episode.started_at
        period_end = basis_episode.last_at
        initial_ticket = episode_occurrences[0].get("ticket_number") or f"#{episode_occurrences[0].get('external_id', '')}"
        last_ticket = episode_occurrences[-1].get("ticket_number") or f"#{episode_occurrences[-1].get('external_id', '')}"
        last_reason = episode_occurrences[-1].get("closing_code_raw") or ""
        progression = [
            {
                "step_label": item.get("operational_recurrence_level") or ("Inicial" if idx == 0 else level_code_from_recurrence_count(idx)),
                "ticket_number": item.get("ticket_number") or f"#{item.get('external_id', '')}",
                "external_id": item.get("external_id"),
                "occurred_at": item.get("occurred_at"),
                "closing_code_raw": item.get("closing_code_raw") or "",
                "is_recurrence": bool(item.get("is_recurrence")),
                "episode_number": basis_episode.number,
            }
            for idx, item in enumerate(episode_occurrences)
        ]
    else:
        episode_occurrences = []
        ep_valid_count = 0
        ep_recurrence_count = 0
        period_start = None
        period_end = None
        initial_ticket = ""
        last_ticket = ""
        last_reason = ""
        progression = []

    calculated_code = level_code_from_recurrence_count(ep_recurrence_count)
    manual_code = str(getattr(order, "recurrence_level", "") or "").strip()

    total_valid_count = summary.get("valid_occurrence_count", ep_valid_count)
    total_recurrence_count = summary.get("total_recurrence_count", ep_recurrence_count)

    if manual_code:
        effective_code = manual_code
        source = "MANUAL"
        source_label = "Confirmado manualmente pela Qualidade"
        unconfirmed_reason = ""
    elif calculated_code:
        effective_code = calculated_code
        source = "CALCULATED"
        source_label = "Cálculo automático · OTRS/CMDB"
        unconfirmed_reason = ""
    else:
        effective_code = ""
        source = "UNCONFIRMED"
        source_label = "Histórico insuficiente"
        if not getattr(order, "link_id", None) and not getattr(order, "ci_snapshot", ""):
            unconfirmed_reason = "Ordem sem ConfigItem/link vinculado para cálculo automático."
        elif not has_link_in_scope:
            unconfirmed_reason = "Link não encontrado no espelho CMDB autorizado."
        elif total_valid_count == 0:
            unconfirmed_reason = "Nenhuma ocorrência operacional válida encontrada no espelho OTRS para este circuito."
        else:
            unconfirmed_reason = "Apenas 1 ocorrência válida no espelho OTRS; nível R1 exige ao menos 2 ocorrências válidas."

    label = effective_code or "A confirmar"
    full_label = LEVEL_DISPLAY_LABELS.get(effective_code, label)

    tooltip_parts = [f"Nível: {full_label} ({source_label})"]
    if ep_valid_count > 0:
        tooltip_parts.append(
            f"Ocorrências válidas no episódio: {ep_valid_count} · Reincidências: {ep_recurrence_count}"
        )
        if total_valid_count > ep_valid_count:
            tooltip_parts.append(
                f"Histórico total do circuito: {total_valid_count} ocorrências válidas · {total_recurrence_count} reincidências"
            )
        tooltip_parts.append(f"Período: {_fmt_date(period_start)} a {_fmt_date(period_end)}")
        if initial_ticket:
            tooltip_parts.append(f"Ticket inicial: {initial_ticket} · Última ocorrência: {last_ticket}")
    if basis_note:
        tooltip_parts.append(basis_note)
    if manual_code and calculated_code and manual_code != calculated_code:
        tooltip_parts.append(f"Cálculo operacional atual: {calculated_code} (nível manual preservado)")
    if unconfirmed_reason:
        tooltip_parts.append(f"Motivo: {unconfirmed_reason}")

    return {
        "code": effective_code,
        "label": label,
        "full_label": full_label,
        "source": source,
        "source_label": source_label,
        "is_manual": source == "MANUAL",
        "is_calculated": source == "CALCULATED",
        "is_unconfirmed": source == "UNCONFIRMED",
        "unconfirmed_reason": unconfirmed_reason,
        "calculated_code": calculated_code,
        "has_manual_divergence": bool(manual_code and calculated_code and manual_code != calculated_code),
        "valid_occurrence_count": ep_valid_count,
        "recurrence_count": ep_recurrence_count,
        "total_valid_occurrence_count": total_valid_count,
        "total_recurrence_count": total_recurrence_count,
        "episode_number": basis_episode.number if basis_episode else None,
        "period_start": period_start,
        "period_end": period_end,
        "initial_ticket_number": initial_ticket,
        "last_ticket_number": last_ticket,
        "last_reason": last_reason,
        "basis_note": basis_note,
        "progression": progression,
        "tooltip": " | ".join(tooltip_parts),
    }
