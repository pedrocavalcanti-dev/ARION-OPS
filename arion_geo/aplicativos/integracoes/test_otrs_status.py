import importlib
from types import SimpleNamespace

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from aplicativos.integracoes.models import OTRSStateMapping, OTRSTicket
from aplicativos.integracoes.services.otrs_status import (
    BACKLOG_STATUSES,
    OPERATIONAL_VALID_STATUSES,
    business_status_label,
    business_statuses_query,
    normalize_business_status,
    normalized_status_counts,
    status_for_external_state,
)


class OTRSOperationalStatusTests(TestCase):
    def test_external_terminal_aliases_share_the_resolved_category(self):
        for state in (
            "merged", "closed successful", "Encerrado com sucesso",
            "Encerrado com viabilidade", "Resolvido",
        ):
            with self.subTest(state=state):
                self.assertEqual(status_for_external_state(state), "RESOLVED")
        self.assertEqual(normalize_business_status("CLOSED"), "RESOLVED")
        self.assertEqual(business_status_label("CLOSED"), "Resolvido")
        self.assertEqual(
            normalized_status_counts([
                {"business_status": "RESOLVED", "total": 2},
                {"business_status": "CLOSED", "total": 3},
            ]),
            {"RESOLVED": 5},
        )

    def test_legacy_closed_rows_match_resolved_queries_without_entering_backlog(self):
        stamp = timezone.now()
        legacy = self._ticket(1, "CLOSED", external_state="Encerrado com viabilidade", stamp=stamp)
        self._ticket(2, "RESOLVED", external_state="Resolvido", stamp=stamp)
        self._ticket(3, "OPEN", external_state="Em atendimento", stamp=stamp)
        self._ticket(4, "PENDING", external_state="Pendente", stamp=stamp)
        self._ticket(5, "SCHEDULED", external_state="Agendado", stamp=stamp)
        self._ticket(6, "UNKNOWN", external_state="Estado novo", stamp=stamp)

        resolved = OTRSTicket.objects.filter(
            business_statuses_query("business_status", ("RESOLVED",)),
        ).order_by("external_id")
        backlog = OTRSTicket.objects.filter(
            business_statuses_query("business_status", BACKLOG_STATUSES),
        ).order_by("external_id")
        valid = OTRSTicket.objects.filter(
            business_statuses_query("business_status", OPERATIONAL_VALID_STATUSES),
        )

        self.assertEqual(list(resolved.values_list("external_id", flat=True)), [1, 2])
        self.assertEqual(list(backlog.values_list("external_id", flat=True)), [3, 4])
        self.assertEqual(valid.count(), 5)
        legacy.refresh_from_db()
        self.assertEqual(legacy.external_state_name, "Encerrado com viabilidade")
        self.assertEqual(legacy.business_status, "CLOSED")
        self.assertEqual(legacy.terminal_state_at, stamp)
        self.assertEqual(legacy.operational_status_label, "Resolvido")

    def test_data_migration_preserves_raw_state_and_all_terminal_dates(self):
        stamp = timezone.now()
        mapping = OTRSStateMapping.objects.create(
            external_state_id=90, external_name="Encerrado com Viabilidade",
            business_status="CLOSED",
        )
        ticket = self._ticket(
            90, "CLOSED", external_state="Encerrado com viabilidade", stamp=stamp,
        )
        ticket.canceled_at = stamp
        ticket.save(update_fields=("canceled_at",))
        apps = SimpleNamespace(get_model=lambda app, model: {
            "OTRSStateMapping": OTRSStateMapping,
            "OTRSTicket": OTRSTicket,
        }[model])
        migration = importlib.import_module(
            "aplicativos.integracoes.migrations.0014_normalize_closed_operational_status",
        )

        migration.normalize_closed_status(apps, SimpleNamespace(connection=connection))

        mapping.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(mapping.business_status, "RESOLVED")
        self.assertEqual(ticket.business_status, "RESOLVED")
        self.assertEqual(ticket.external_state_name, "Encerrado com viabilidade")
        self.assertEqual(ticket.resolved_at, stamp)
        self.assertEqual(ticket.terminal_state_at, stamp)
        self.assertEqual(ticket.canceled_at, stamp)

        migration.restore_closed_status(apps, SimpleNamespace(connection=connection))

        mapping.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(mapping.business_status, "CLOSED")
        self.assertEqual(ticket.business_status, "CLOSED")
        self.assertEqual(ticket.external_state_name, "Encerrado com viabilidade")

    @staticmethod
    def _ticket(external_id, status, *, external_state, stamp):
        return OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=f"202600000000{external_id:04d}",
            queue_external_id=1,
            queue_name="NOC - ATACADO",
            external_state_id=external_id,
            external_state_name=external_state,
            business_status=status,
            source_created_at=stamp,
            resolved_at=stamp if status in {"RESOLVED", "CLOSED"} else None,
            terminal_state_at=stamp if status in {"RESOLVED", "CLOSED"} else None,
        )
