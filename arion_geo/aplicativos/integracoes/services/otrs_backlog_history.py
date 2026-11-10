"""Read-only lifecycle extraction. Never infer a past state from today's mirror."""
from collections import Counter, defaultdict
from datetime import timezone as utc_timezone
import re

from django.db import transaction
from django.utils import timezone

from ..models import OTRSBacklogHistory, OTRSBacklogInterval
from ..otrs_queues import NOC_QUEUE_NAME
from .otrs_status import BACKLOG_STATUSES, status_for_external_state
from .otrs_sync import normalize_responsibility
from .sync_common import source_datetime

FIELD = "RvalidacaoProcedenteImprocedente"
CHANGE = re.compile(r"^%%FieldName%%" + FIELD + r"%%Value%%(.*?)%%OldValue%%(.*)$", re.DOTALL)


def coverage_datetime(value):
    """UTC_TIMESTAMP is UTC; lifecycle DATETIME fields use OTRS local time."""
    return timezone.make_aware(value, utc_timezone.utc) if timezone.is_naive(value) else value


def classification_change(value):
    match = CHANGE.fullmatch(value or "")
    if not match:
        raise ValueError("Formato de procedência histórica desconhecido; cobertura anterior preservada.")
    new, old = match.groups()
    if any(v.strip().casefold() not in {"", "procedente", "improcedente"} for v in (new, old)):
        raise ValueError("Procedência histórica desconhecida; cobertura anterior preservada.")
    return normalize_responsibility(old), normalize_responsibility(new)


def reconstruct_ticket(ticket, events, *, queue_id, through):
    """Build half-open intervals; gaps before first evidence remain unknown.

    All transition IDs are deduplicated. The old classification in the first
    dynamic-field change establishes its baseline, never its present-day value.
    """
    created = ticket["created_at"]
    unique = {}
    for event in events:
        if event["id"] in unique and unique[event["id"]] != event:
            raise ValueError("Eventos históricos conflitantes para o mesmo identificador.")
        unique[event["id"]] = event
    events = sorted((e for e in unique.values() if created <= e["at"] <= through),
                    key=lambda e: (e["at"], e["id"]))
    changes = [e["classification"] for e in events if e.get("classification") is not None]
    responsibility = changes[0][0] if changes else ticket.get("responsibility", "INDETERMINADO")
    # A filled current field with no transition cannot establish when it began.
    classification_known = bool(changes) or responsibility == "INDETERMINADO"
    result = []

    def append(start, end, *, event=None, known=False):
        if end <= start:
            return
        state = event["status"] if event else ""
        if known and (event["queue_id"] != queue_id or state not in BACKLOG_STATUSES
                      or responsibility == "IMPROCEDENTE"):
            return
        result.append({"ticket_external_id": ticket["id"], "source_event_id": event["id"] if event else 0,
                       "source_created_at": created, "started_at": start, "ended_at": end,
                       "known": known, "business_status": state,
                       "external_state_name": event.get("state_name", "") if event else "",
                       "responsibility": responsibility if known else ""})

    append(created, events[0]["at"] if events else through)
    for index, event in enumerate(events):
        change = event.get("classification")
        if change:
            if change[0] != responsibility:
                # Do not silently bridge a missing classification transition.
                raise ValueError("Histórico de procedência descontínuo; cobertura anterior preservada.")
            responsibility = change[1]
            classification_known = True
        end = events[index + 1]["at"] if index + 1 < len(events) else through
        known = classification_known
        if index == len(events) - 1:
            known = known and event["queue_id"] == ticket["queue_id"] and event["state_id"] == ticket["state_id"]
            known = known and responsibility == ticket.get("responsibility", "INDETERMINADO")
        append(event["at"], end, event=event, known=known)
    return result


def read_history(client):
    """Capture a consistent source transaction, including tickets that left NOC.

    Only IDs, state/queue snapshots, dates and the one classification field are
    selected. No titles, articles, authors, attachments or other dynamic values.
    """
    intervals, diagnostics = [], Counter()
    with client.read_only_snapshot():
        tables = ("ticket", "ticket_history", "ticket_history_type", "ticket_state", "queue", "dynamic_field", "dynamic_field_value")
        engines = client.execute_select(
            "SELECT TABLE_NAME AS table_name, ENGINE AS engine FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s,%s,%s,%s,%s,%s)", tables)
        if len(engines) != len(tables) or any(str(r["engine"]).casefold() != "innodb" for r in engines):
            raise ValueError("Origem sem tabelas transacionais verificáveis; cobertura anterior preservada.")
        through = coverage_datetime(client.execute_select("SELECT UTC_TIMESTAMP() AS observed_at")[0]["observed_at"])
        queues = client.execute_select("SELECT id, name FROM queue WHERE name=%s", (NOC_QUEUE_NAME,))
        if len(queues) != 1:
            raise ValueError("Fila NOC exata não encontrada de forma única.")
        queue_id = int(queues[0]["id"])
        states = {int(r["id"]): r["name"] for r in client.execute_select("SELECT id, name FROM ticket_state")}
        types = {r["name"]: int(r["id"]) for r in client.execute_select("SELECT id, name FROM ticket_history_type")}
        if not {"NewTicket", "Move", "StateUpdate", "TicketDynamicFieldUpdate", "Merged"}.issubset(types):
            raise ValueError("Tipos necessários de histórico OTRS ausentes.")
        fields = client.execute_select("SELECT id FROM dynamic_field WHERE name=%s AND object_type=%s", (FIELD, "Ticket"))
        if len(fields) != 1:
            raise ValueError("Campo de procedência OTRS ausente ou ambíguo.")
        candidates = client.execute_select(
            "SELECT DISTINCT ticket_id AS id FROM ticket_history WHERE queue_id=%s "
            "UNION SELECT id FROM ticket WHERE queue_id=%s", (queue_id, queue_id))
        ids = sorted({int(r["id"]) for r in candidates})
        if not ids:
            raise ValueError("População histórica NOC vazia; cobertura anterior preservada.")
        earliest = through
        for offset in range(0, len(ids), 300):
            batch = ids[offset:offset + 300]
            marks = ",".join(["%s"] * len(batch))
            tickets = client.execute_select(
                f"SELECT t.id, t.create_time, t.queue_id, t.ticket_state_id, d.value_text AS responsibility "
                f"FROM ticket t LEFT JOIN dynamic_field_value d ON d.object_id=t.id AND d.field_id=%s "
                f"WHERE t.id IN ({marks})", (fields[0]["id"], *batch))
            bounds = client.execute_select(
                f"SELECT ticket_id, MIN(id) AS first_id, MAX(id) AS last_id FROM ticket_history "
                f"WHERE ticket_id IN ({marks}) GROUP BY ticket_id", batch)
            edge_ids = sorted({int(r[k]) for r in bounds for k in ("first_id", "last_id")})
            if not edge_ids:
                edge_ids = [0]
            edge_marks = ",".join(["%s"] * len(edge_ids))
            transitions = [types[k] for k in ("NewTicket", "Move", "StateUpdate", "Merged")]
            dynamic = types["TicketDynamicFieldUpdate"]
            raw_events = client.execute_select(
                "SELECT id, ticket_id, queue_id, state_id, create_time, "
                "CASE WHEN history_type_id=%s AND name LIKE %s THEN name ELSE NULL END AS classification_change "
                f"FROM ticket_history WHERE ticket_id IN ({marks}) AND "
                f"(history_type_id IN (%s,%s,%s,%s) OR id IN ({edge_marks}) "
                "OR (history_type_id=%s AND name LIKE %s)) ORDER BY ticket_id, create_time, id",
                (dynamic, f"%%FieldName%%{FIELD}%%Value%%" + "%", *batch, *transitions, *edge_ids,
                 dynamic, f"%%FieldName%%{FIELD}%%Value%%" + "%"))
            events_by_ticket = defaultdict(list)
            for row in raw_events:
                at = source_datetime(row["create_time"])
                if not at or int(row["state_id"]) not in states:
                    raise ValueError("Evento sem timestamp/estado verificável.")
                state_name = states[int(row["state_id"])]
                event = {"id": int(row["id"]), "at": at, "queue_id": int(row["queue_id"]),
                         "state_id": int(row["state_id"]), "state_name": state_name,
                         "status": status_for_external_state(state_name)}
                if row["classification_change"] is not None:
                    event["classification"] = classification_change(row["classification_change"])
                events_by_ticket[int(row["ticket_id"])].append(event)
            for row in tickets:
                created = source_datetime(row["create_time"])
                if not created or created > through:
                    raise ValueError("Criação de ticket fora da cobertura histórica.")
                earliest = min(earliest, created)
                ticket = {"id": int(row["id"]), "created_at": created, "queue_id": int(row["queue_id"]),
                          "state_id": int(row["ticket_state_id"]),
                          "responsibility": normalize_responsibility(row["responsibility"])}
                events = events_by_ticket[ticket["id"]]
                ticket_intervals = reconstruct_ticket(ticket, events, queue_id=queue_id, through=through)
                intervals.extend(ticket_intervals)
                diagnostics["tickets"] += 1
                diagnostics["events"] += len(events)
                diagnostics["tickets_without_events"] += int(not events)
                diagnostics["unknown_intervals"] += sum(not i["known"] for i in ticket_intervals)
            diagnostics["missing_source_tickets"] += len(batch) - len(tickets)
        # A dangling history identity could be a purged ticket: don't publish
        # an apparently complete population by silently dropping it.
        if diagnostics["missing_source_tickets"]:
            raise ValueError("Histórico com tickets ausentes na origem; cobertura anterior preservada.")
    return {"through": through, "earliest_at": earliest, "intervals": intervals, "diagnostics": dict(diagnostics)}


def sync_history(client):
    data = read_history(client)
    with transaction.atomic():
        coverage, _ = OTRSBacklogHistory.objects.select_for_update().get_or_create(
            queue_name=NOC_QUEUE_NAME, defaults={"earliest_at": data["earliest_at"], "through": data["through"]})
        coverage.intervals.all().delete()
        OTRSBacklogInterval.objects.bulk_create([
            OTRSBacklogInterval(coverage=coverage, **interval) for interval in data["intervals"]
        ], batch_size=300)
        coverage.earliest_at, coverage.through = data["earliest_at"], data["through"]
        coverage.ticket_count = data["diagnostics"]["tickets"]
        coverage.event_count = data["diagnostics"]["events"]
        coverage.diagnostics = data["diagnostics"]
        coverage.save()
    return {**data["diagnostics"], "intervals": len(data["intervals"]), "through": data["through"].isoformat()}
