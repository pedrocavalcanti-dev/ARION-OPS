from datetime import timedelta
import importlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem,
)
from .models import TicketQueueView, TicketTracking, TicketTrackingEvent, TicketTrackingNote
from .services import TicketQueueService, otrs_queue_url, otrs_ticket_url, sanitize_queue_filters


class TicketQueueTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="ticket-admin", email="ticket@example.test", password="safe-password-1",
        )
        self.client.force_login(self.user)
        self.operator = CMDBOperatorMirror.objects.create(
            source_config_item_id="ticket-provider-1", name="ISP de teste",
        )
        self.link = self.make_link("ticket-link-1", "Projeto A")

    def make_link(self, ci, project, state="SP"):
        return CMDBLinkMirror.objects.create(
            source_config_item_id=ci, source_number=f"N-{ci}", designation=f"Link {ci}",
            name=f"CMDB {ci}", state=state, city="São Paulo", contracting_client="Cliente A",
            contracting_client_raw="Cliente A", business_project=project, operator=self.operator,
        )

    def make_ticket(self, external_id, status="OPEN", *, hours_old=4, link=None, number=None):
        created = timezone.now() - timedelta(hours=hours_old)
        ticket = OTRSTicket.objects.create(
            external_id=external_id, ticket_number=number or f"T-{external_id}",
            title=f"Falha de conectividade {external_id}", queue_external_id=107,
            queue_name="NOC - ATACADO", external_state_id=external_id,
            external_state_name=status, business_status=status,
            is_business_open=status in {"OPEN", "PENDING", "SCHEDULED"},
            priority_name="3 normal", source_created_at=created,
            is_present_in_queue=True,
        )
        if link:
            OTRSTicketConfigItem.objects.create(
                ticket=ticket, config_item_id=link.source_config_item_id,
                cmdb_link=link, relation_type="DependsOn", relation_state="Valid",
            )
        return ticket

    def test_active_universe_excludes_terminal_and_unknown_states(self):
        for idx, status in enumerate(("OPEN", "PENDING", "SCHEDULED", "RESOLVED", "CLOSED", "CANCELED", "UNKNOWN"), 1):
            self.make_ticket(idx, status, link=self.link)
        response = self.client.get(reverse("chamados:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_count"], 3)
        self.assertEqual(response.context["status_counts"], {"OPEN": 1, "PENDING": 1, "SCHEDULED": 1})
        self.assertEqual(len(response.context["rows"]), 3)

    def test_arion_status_catalog_and_lazy_default_are_final(self):
        self.assertEqual(list(TicketTracking.InternalStatus.choices), [
            ("OPEN", "Em Aberto"),
            ("RESOLVED", "Resolvido"),
            ("ISP_IN_PROGRESS", "Em atendimento pela ISP"),
            ("FINANCIAL_PENDING", "Pendente financeira"),
            ("TECH_VISIT", "AG. Visita Técnica"),
            ("POST_SALES_ANALYSIS", "Em análise pelo Pós-Vendas"),
            ("OWNERSHIP_CHANGE", "Troca de titularidade"),
            ("LINK_CANCELLATION", "Cancelamento do Link"),
            ("WAITING_VALIDATION", "Aguardando validação"),
            ("CGNAT", "CGNAT"),
            ("TUNNELS", "Túneis"),
            ("MASS_OUTAGE", "Massiva"),
        ])
        self.assertEqual(TicketTracking._meta.get_field("internal_status").default, "OPEN")

    def test_legacy_status_migration_maps_old_values_and_keeps_them_in_events(self):
        legacy_statuses = (
            "NEW", "IN_ANALYSIS", "WAITING_PROVIDER", "WAITING_CUSTOMER",
            "MONITORING", "WAITING_VALIDATION", "FINISHED_INTERNAL", "UNMAPPED_OLD",
        )
        tracks = [TicketTracking.objects.create(
            ticket_external_id=300 + index, internal_status=status,
        ) for index, status in enumerate(legacy_statuses)]
        migration = importlib.import_module(
            "aplicativos.chamados.migrations.0004_operational_status_catalog",
        )
        migration.migrate_legacy_tracking_statuses(apps, SimpleNamespace(connection=connection))
        expected = (
            "OPEN", "OPEN", "ISP_IN_PROGRESS", "OPEN", "OPEN",
            "WAITING_VALIDATION", "RESOLVED", "OPEN",
        )
        for tracking, status in zip(tracks, expected):
            tracking.refresh_from_db()
            self.assertEqual(tracking.internal_status, status)
            self.assertEqual(tracking.revision, 0 if tracking.internal_status == "WAITING_VALIDATION" else 1)
        self.assertEqual(TicketTrackingEvent.objects.filter(
            tracking__in=tracks, field_name="internal_status",
        ).count(), 7)
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking__ticket_external_id=300, old_value="Novo", new_value="Em Aberto",
        ).exists())
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking__ticket_external_id=302, old_value="Aguardando operadora",
            new_value="Em atendimento pela ISP",
        ).exists())
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking__ticket_external_id=306, old_value="Finalizado no ARION", new_value="Resolvido",
        ).exists())
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking__ticket_external_id=307, old_value="UNMAPPED_OLD", new_value="Em Aberto",
        ).exists())

    def test_queue_table_hides_otrs_status_and_drawer_keeps_official_summary(self):
        ticket = self.make_ticket(31, "PENDING", hours_old=50, link=self.link)
        ticket.closing_code_raw = "Teste de encerramento"
        ticket.save(update_fields=("closing_code_raw",))
        response = self.client.get(reverse("chamados:index"))
        html = response.content.decode()
        self.assertNotIn("<th>Status OTRS</th>", html)
        self.assertContains(response, "Acompanhamento ARION")
        self.assertContains(response, "Mais filtros")
        self.assertContains(response, 'name="otrs_status"')
        self.assertContains(response, "Sem responsável")

        detail = self.client.get(reverse("chamados:detail", args=(ticket.external_id,))).json()
        self.assertEqual(detail["ticket"]["business_status"], "PENDING")
        self.assertEqual(detail["ticket"]["status_label"], "Pendente")
        self.assertEqual(detail["ticket"]["priority"], "3 normal")
        self.assertTrue(detail["ticket"]["created_at"])
        self.assertTrue(detail["ticket"]["age_hours"] > 24)
        self.assertTrue(detail["ticket"]["closing_reason_label"])
        self.assertEqual(detail["tracking"]["internal_status_label"], "Em Aberto")

    def test_historical_ticket_detail_is_read_only_and_queue_updates_stay_active_only(self):
        ticket = self.make_ticket(559, "CLOSED", link=self.link)
        detail = self.client.get(reverse("chamados:detail", args=(ticket.external_id,)))
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json()["ticket"]["operational"])
        update = self.client.post(reverse("chamados:tracking_update", args=(ticket.external_id,)), {
            "action": "drawer", "internal_status": TicketTracking.InternalStatus.OPEN,
            "internal_priority": "", "assigned_to": "", "next_action_at": "", "revision": 0,
        })
        self.assertEqual(update.status_code, 404)

    def test_assignee_options_include_one_option_for_a_user_with_multiple_tickets(self):
        pedro = get_user_model().objects.create_user(
            username="pedro.cavalcanti", first_name="Pedro", last_name="Cavalcanti",
            password="safe-password-6",
        )
        for ticket_id in (501, 502, 503):
            ticket = self.make_ticket(ticket_id, link=self.link)
            TicketTracking.objects.create(ticket_external_id=ticket.external_id, assigned_to=pedro)

        response = self.client.get(reverse("chamados:index"))

        self.assertEqual(response.context["filter_options"]["assignees"], [
            {"value": pedro.pk, "label": "Pedro Cavalcanti"},
        ])

    def test_assignee_options_are_unique_and_alphabetical_by_friendly_name(self):
        users = [
            get_user_model().objects.create_user(
                username=username, first_name=first_name, last_name=last_name,
                password="safe-password-7",
            )
            for username, first_name, last_name in (
                ("tiago.borges", "Tiago", "Borges"),
                ("pedro.cavalcanti", "Pedro", "Cavalcanti"),
                ("allan.carmo", "Allan", "Carmo"),
            )
        ]
        for user, count, first_ticket in zip(users, (5, 3, 2), (510, 520, 530)):
            for ticket_id in range(first_ticket, first_ticket + count):
                ticket = self.make_ticket(ticket_id, link=self.link)
                TicketTracking.objects.create(ticket_external_id=ticket.external_id, assigned_to=user)

        options = TicketQueueService.build(self.user, {})["filter_options"]["assignees"]

        self.assertEqual([option["label"] for option in options], [
            "Allan Carmo", "Pedro Cavalcanti", "Tiago Borges",
        ])
        self.assertEqual([option["value"] for option in options], [
            users[2].pk, users[1].pk, users[0].pk,
        ])

    def test_unassigned_filter_option_is_single_and_returns_all_unassigned_tickets(self):
        tickets = [self.make_ticket(ticket_id, link=self.link) for ticket_id in (540, 541, 542)]

        response = self.client.get(reverse("chamados:index"))
        assignee_select = response.content.decode().partition('<select name="assignee">')[2].partition("</select>")[0]
        self.assertEqual(assignee_select.count('value="unassigned"'), 1)

        filtered = self.client.get(reverse("chamados:index"), {"assignee": "unassigned"})
        self.assertEqual(
            {row["external_id"] for row in filtered.context["rows"]},
            {ticket.external_id for ticket in tickets},
        )

    def test_assignee_filter_returns_every_ticket_assigned_to_selected_user(self):
        pedro = get_user_model().objects.create_user(username="pedro", password="safe-password-8")
        allan = get_user_model().objects.create_user(username="allan", password="safe-password-9")
        pedro_tickets = [self.make_ticket(ticket_id, link=self.link) for ticket_id in (550, 551, 552, 553)]
        allan_ticket = self.make_ticket(554, link=self.link)
        for ticket in pedro_tickets:
            TicketTracking.objects.create(ticket_external_id=ticket.external_id, assigned_to=pedro)
        TicketTracking.objects.create(ticket_external_id=allan_ticket.external_id, assigned_to=allan)

        response = self.client.get(reverse("chamados:index"), {"assignee": str(pedro.pk)})

        self.assertEqual(
            {row["external_id"] for row in response.context["rows"]},
            {ticket.external_id for ticket in pedro_tickets},
        )
        self.assertEqual(response.context["page"].paginator.count, 4)

    def test_assignee_filter_combines_with_client_project_state_and_tab(self):
        pedro = get_user_model().objects.create_user(username="pedro", password="safe-password-10")
        matching_link = self.make_link("ticket-claro-project-a", "Projeto A", "SP")
        matching_link.contracting_client = "Claro"
        matching_link.save(update_fields=("contracting_client",))
        other_project_link = self.make_link("ticket-claro-project-b", "Projeto B", "SP")
        other_project_link.contracting_client = "Claro"
        other_project_link.save(update_fields=("contracting_client",))
        other_state_link = self.make_link("ticket-claro-project-a-rj", "Projeto A", "RJ")
        other_state_link.contracting_client = "Claro"
        other_state_link.save(update_fields=("contracting_client",))
        matching = self.make_ticket(560, "PENDING", link=matching_link)
        wrong_project = self.make_ticket(561, "PENDING", link=other_project_link)
        wrong_state = self.make_ticket(562, "PENDING", link=other_state_link)
        wrong_status = self.make_ticket(563, "OPEN", link=matching_link)
        for ticket in (matching, wrong_project, wrong_state, wrong_status):
            TicketTracking.objects.create(
                ticket_external_id=ticket.external_id, assigned_to=pedro,
                internal_status=TicketTracking.InternalStatus.FINANCIAL_PENDING,
            )

        response = self.client.get(reverse("chamados:index"), {
            "assignee": str(pedro.pk), "client": "Claro", "project": "Projeto A", "uf": "SP",
            "arion_status": TicketTracking.InternalStatus.FINANCIAL_PENDING,
            "otrs_status": "PENDING", "tab": "PENDING",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["external_id"] for row in response.context["rows"]], [matching.external_id])

    def test_assignee_filter_intersects_all_queue_tabs(self):
        assigned_tickets = [
            self.make_ticket(570, "OPEN", link=self.link),
            self.make_ticket(571, "PENDING", link=self.link),
            self.make_ticket(572, "SCHEDULED", link=self.link),
        ]
        unassigned = self.make_ticket(573, "PENDING", link=self.link)
        for ticket in assigned_tickets:
            TicketTracking.objects.create(ticket_external_id=ticket.external_id, assigned_to=self.user)

        cases = (
            ("all", str(self.user.pk), {570, 571, 572}),
            ("OPEN", str(self.user.pk), {570}),
            ("PENDING", str(self.user.pk), {571}),
            ("SCHEDULED", str(self.user.pk), {572}),
            ("mine", str(self.user.pk), {570, 571, 572}),
            ("unassigned", "unassigned", {573}),
            ("mine", "unassigned", set()),
        )
        for tab, assignee, expected_ids in cases:
            with self.subTest(tab=tab, assignee=assignee):
                response = self.client.get(reverse("chamados:index"), {"tab": tab, "assignee": assignee})
                self.assertEqual(
                    {row["external_id"] for row in response.context["rows"]},
                    expected_ids,
                )

    def test_assignee_filter_and_page_size_are_preserved_in_pagination_and_tab_urls(self):
        pedro = get_user_model().objects.create_user(username="pedro", password="safe-password-11")
        for ticket_id in range(580, 606):
            ticket = self.make_ticket(ticket_id, "PENDING", link=self.link)
            TicketTracking.objects.create(ticket_external_id=ticket.external_id, assigned_to=pedro)

        response = self.client.get(reverse("chamados:index"), {
            "assignee": str(pedro.pk), "client": "Cliente A", "tab": "PENDING", "page_size": "25",
        })
        pagination_params = parse_qs(urlsplit(response.context["pagination_urls"]["next"]).query)
        tab_params = parse_qs(urlsplit(response.context["tab_urls"]["PENDING"]).query)

        self.assertEqual(pagination_params["assignee"], [str(pedro.pk)])
        self.assertEqual(pagination_params["client"], ["Cliente A"])
        self.assertEqual(pagination_params["page_size"], ["25"])
        self.assertEqual(pagination_params["page"], ["2"])
        self.assertEqual(tab_params["assignee"], [str(pedro.pk)])
        self.assertEqual(tab_params["client"], ["Cliente A"])
        self.assertEqual(tab_params["page_size"], ["25"])
        page_two = self.client.get(reverse("chamados:index"), pagination_params)
        self.assertEqual(page_two.context["page"].number, 2)
        self.assertEqual(page_two.context["page"].paginator.count, 26)

    def test_queue_defaults_to_fifty_and_keeps_filters_on_next_page(self):
        for ticket_id in range(710, 761):
            self.make_ticket(ticket_id, "PENDING", link=self.link)
        response = self.client.get(reverse("chamados:index"), {"q": "ticket", "tab": "PENDING"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_size"], 50)
        self.assertEqual(len(response.context["rows"]), 50)
        next_params = parse_qs(urlsplit(response.context["pagination_urls"]["next"]).query)
        self.assertEqual(next_params["page_size"], ["50"])
        self.assertEqual(next_params["q"], ["ticket"])
        self.assertEqual(next_params["tab"], ["PENDING"])
        page_two = self.client.get(reverse("chamados:index"), next_params)
        self.assertEqual(page_two.context["page"].number, 2)
        self.assertEqual(len(page_two.context["rows"]), 1)

    def test_ticket_with_multiple_links_has_one_queue_row_and_all_scoped_links_in_detail(self):
        second_link = self.make_link("ticket-link-2", "Projeto A", "RJ")
        ticket = self.make_ticket(20, link=self.link)
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id=second_link.source_config_item_id,
            cmdb_link=second_link, relation_type="DependsOn", relation_state="Valid",
        )
        response = self.client.get(reverse("chamados:index"))
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["links_count"], 2)
        detail = self.client.get(reverse("chamados:detail", args=(ticket.external_id,)))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["links"]), 2)

    def test_provider_and_project_filters_do_not_multiply_ticket_counts(self):
        second_link = self.make_link("ticket-link-filter-2", "Projeto B", "RJ")
        first_ticket = self.make_ticket(21, link=self.link)
        OTRSTicketConfigItem.objects.create(
            ticket=first_ticket, config_item_id=second_link.source_config_item_id,
            cmdb_link=second_link, relation_type="DependsOn-second", relation_state="Valid",
        )
        second_provider = CMDBOperatorMirror.objects.create(
            source_config_item_id="ticket-provider-2", name="ISP alternativo",
        )
        other_provider_link = CMDBLinkMirror.objects.create(
            source_config_item_id="ticket-link-filter-3", source_number="N-3",
            designation="Link N-3", state="AM", contracting_client="Cliente A",
            business_project="Projeto B", operator=second_provider,
        )
        self.make_ticket(22, link=other_provider_link)

        provider_result = TicketQueueService.build(self.user, {"provider": self.operator.source_config_item_id})
        project_result = TicketQueueService.build(self.user, {"project": "Projeto A"})

        self.assertEqual(provider_result["active_count"], 1)
        self.assertEqual(provider_result["affected_count"], 2)
        self.assertEqual(project_result["active_count"], 1)
        # The Project filter selects the Ticket through a matching Link; the card
        # then counts every distinct scoped Link attached to that selected Ticket.
        self.assertEqual(project_result["affected_count"], 2)
        self.assertEqual(len(provider_result["rows"]), 1)
        self.assertEqual(len(project_result["rows"]), 1)

    def test_tracking_is_virtual_until_first_change_then_status_assignment_note_and_events_persist(self):
        ticket = self.make_ticket(30, hours_old=30, link=self.link)
        response = self.client.get(reverse("chamados:index"))
        self.assertEqual(response.context["rows"][0]["tracking"]["internal_status"], "OPEN")
        self.assertEqual(response.context["rows"][0]["tracking"]["internal_status_label"], "Em Aberto")
        self.assertTrue(response.context["rows"][0]["over_24h"])
        self.assertFalse(TicketTracking.objects.exists())

        url = reverse("chamados:tracking_update", args=(ticket.external_id,))
        update = self.client.post(url, {
            "internal_status": "ISP_IN_PROGRESS", "assigned_to": self.user.pk,
            "internal_priority": "HIGH", "next_action_at": "", "revision": 0,
        })
        self.assertEqual(update.status_code, 200, update.content)
        tracking = TicketTracking.objects.get(ticket_external_id=ticket.external_id)
        self.assertEqual(tracking.internal_status, TicketTracking.InternalStatus.ISP_IN_PROGRESS)
        self.assertEqual(tracking.assigned_to, self.user)
        self.assertEqual(tracking.revision, 1)
        self.assertEqual(TicketTrackingEvent.objects.filter(tracking=tracking).count(), 3)
        self.assertEqual(TicketTrackingEvent.objects.filter(
            tracking=tracking, field_name="internal_status", old_value="Em Aberto",
            new_value="Em atendimento pela ISP",
        ).count(), 1)

        note_url = reverse("chamados:note_create", args=(ticket.external_id,))
        note_response = self.client.post(note_url, {"body": "Operadora acionada.", "revision": 1})
        self.assertEqual(note_response.status_code, 201)
        stale_note = self.client.post(note_url, {"body": "Nota desatualizada.", "revision": 1})
        self.assertEqual(stale_note.status_code, 409)
        self.assertEqual(TicketTrackingNote.objects.get(tracking=tracking).body, "Operadora acionada.")
        self.assertTrue(TicketTrackingEvent.objects.filter(tracking=tracking, event_type="NOTE_ADDED").exists())
        self.assertNotEqual(self.client.post(url, {
            "internal_status": "MASS_OUTAGE", "assigned_to": self.user.pk,
            "internal_priority": "HIGH", "next_action_at": "", "revision": 0,
        }).status_code, 200)

    def test_recurrence_uses_two_distinct_valid_tickets_in_last_30_days(self):
        self.make_ticket(60, "OPEN", hours_old=2, link=self.link)
        self.make_ticket(61, "RESOLVED", hours_old=24 * 8, link=self.link)
        result = TicketQueueService.build(self.user, {})
        self.assertEqual(result["recurrent_link_count"], 1)
        self.assertTrue(result["rows"][0]["recurrent"])

    def test_data_scope_hides_tickets_and_related_links_outside_allowed_project(self):
        allowed_project = Projeto.objects.create(name="Projeto A", normalized_name="projeto-a")
        allowed_client = Operadora.objects.create(name="Cliente A", normalized_name="cliente-a")
        denied_project = Projeto.objects.create(name="Projeto B", normalized_name="projeto-b")
        denied_link = self.make_link("ticket-link-hidden", "Projeto B", "AM")
        allowed_ticket = self.make_ticket(40, link=self.link)
        hidden_ticket = self.make_ticket(41, link=denied_link)
        visible_assignee = get_user_model().objects.create_user(
            username="visible-assignee", password="safe-password-12",
        )
        hidden_assignee = get_user_model().objects.create_user(
            username="hidden-assignee", password="safe-password-13",
        )
        TicketTracking.objects.create(ticket_external_id=allowed_ticket.external_id, assigned_to=visible_assignee)
        TicketTracking.objects.create(ticket_external_id=hidden_ticket.external_id, assigned_to=hidden_assignee)

        analyst_group = Group.objects.get(name="Analista NOC")
        scope = GroupDataScope.objects.get(group=analyst_group)
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.allowed_operators.set([allowed_client])
        scope.allowed_projects.set([allowed_project])
        scope.save()
        analyst = get_user_model().objects.create_user(username="scoped-analyst", password="safe-password-2")
        analyst.groups.set([analyst_group])
        self.client.force_login(analyst)

        response = self.client.get(reverse("chamados:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["external_id"] for row in response.context["rows"]], [allowed_ticket.external_id])
        self.assertEqual(
            [option["value"] for option in response.context["filter_options"]["assignees"]],
            [visible_assignee.pk],
        )
        filtered_hidden = self.client.get(reverse("chamados:index"), {"assignee": str(hidden_assignee.pk)})
        self.assertEqual(filtered_hidden.status_code, 200)
        self.assertEqual(filtered_hidden.context["rows"], [])
        self.assertEqual(
            filtered_hidden.context["filter_options"]["assignees"],
            response.context["filter_options"]["assignees"],
        )
        allowed_note = self.client.post(
            reverse("chamados:note_create", args=(allowed_ticket.external_id,)),
            {"body": "Acompanhamento dentro do escopo.", "revision": 0},
        )
        self.assertEqual(allowed_note.status_code, 201)
        self.assertEqual(self.client.get(reverse("chamados:detail", args=(hidden_ticket.external_id,))).status_code, 404)
        self.assertEqual(self.client.post(reverse("chamados:tracking_update", args=(hidden_ticket.external_id,)), {
            "action": "assume", "revision": 0,
        }).status_code, 404)
        self.assertEqual(self.client.post(reverse("chamados:note_create", args=(hidden_ticket.external_id,)), {"body": "não deve entrar"}).status_code, 404)

    def test_queue_query_count_stays_bounded_for_a_page_of_25(self):
        for ticket_id in range(100, 125):
            self.make_ticket(ticket_id, link=self.link)
        with CaptureQueriesContext(connection) as captured:
            TicketQueueService.build(self.user, {})
        self.assertLessEqual(len(captured), 24, f"Fila executou {len(captured)} consultas para 25 linhas")

    def test_dashboard_status_links_are_available_to_authorized_users(self):
        self.make_ticket(50, "PENDING", link=self.link)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/chamados/?tab=OPEN')
        self.assertContains(response, '/chamados/?tab=PENDING')
        self.assertContains(response, '/chamados/?tab=SCHEDULED')

    def test_quick_tabs_filter_active_status_and_keep_other_filters_in_their_urls(self):
        open_ticket = self.make_ticket(201, "OPEN", link=self.link)
        self.make_ticket(202, "PENDING", link=self.link)
        self.make_ticket(203, "SCHEDULED", link=self.link)
        self.make_ticket(204, "CLOSED", link=self.link)

        response = self.client.get(reverse("chamados:index"), {"project": "Projeto A"})
        self.assertEqual(response.status_code, 200)
        parsed = urlsplit(response.context["tab_urls"]["OPEN"])
        params = parse_qs(parsed.query)
        self.assertEqual(params["project"], ["Projeto A"])
        self.assertEqual(params["tab"], ["OPEN"])
        self.assertEqual(response.context["tab_counts"], {
            "all": 3, "OPEN": 1, "PENDING": 1, "SCHEDULED": 1, "mine": 0, "unassigned": 3,
        })

        response = self.client.get(reverse("chamados:index"), {"project": "Projeto A", "tab": "open"})
        self.assertEqual([row["external_id"] for row in response.context["rows"]], [open_ticket.external_id])
        response = self.client.get(reverse("chamados:index"), {"tab": "PENDING"})
        self.assertEqual([row["business_status"] for row in response.context["rows"]], ["PENDING"])
        response = self.client.get(reverse("chamados:index"), {"tab": "SCHEDULED"})
        self.assertEqual([row["business_status"] for row in response.context["rows"]], ["SCHEDULED"])
        response = self.client.get(reverse("chamados:index"), {"otrs_status": "OPEN", "tab": "all"})
        self.assertEqual([row["business_status"] for row in response.context["rows"]], ["OPEN"])
        self.assertEqual(response.context["tab_counts"]["all"], 3)

    def test_my_and_unassigned_tabs_use_arion_assignment(self):
        mine = self.make_ticket(211, link=self.link)
        unassigned = self.make_ticket(212, link=self.link)
        other_ticket = self.make_ticket(213, link=self.link)
        other = get_user_model().objects.create_user(username="other-assignee", password="safe-password-3")
        TicketTracking.objects.create(ticket_external_id=mine.external_id, assigned_to=self.user)
        TicketTracking.objects.create(ticket_external_id=other_ticket.external_id, assigned_to=other)

        response = self.client.get(reverse("chamados:index"), {"tab": "mine"})
        self.assertEqual([row["external_id"] for row in response.context["rows"]], [mine.external_id])
        response = self.client.get(reverse("chamados:index"), {"tab": "unassigned"})
        self.assertEqual([row["external_id"] for row in response.context["rows"]], [unassigned.external_id])

    @override_settings(OTRS_WEB_BASE_URL="https://suporte.example.test/otrs/index.pl")
    def test_ticket_link_uses_mirrored_internal_id_and_missing_ids_are_safe(self):
        self.assertEqual(
            otrs_ticket_url(101231),
            "https://suporte.example.test/otrs/index.pl?Action=AgentTicketZoom;TicketID=101231",
        )
        self.assertEqual(otrs_ticket_url(None), "")
        self.assertEqual(otrs_ticket_url(0), "")
        self.make_ticket(101231, link=self.link, number="2026102036000917")
        response = self.client.get(reverse("chamados:index"))
        self.assertContains(response, "TicketID=101231")
        self.assertContains(response, "2026102036000917")
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')
        self.make_ticket(101455, link=self.link, number="2026102336000644")
        response = self.client.get(reverse("chamados:index"))
        self.assertContains(response, "TicketID=101455")
        self.assertContains(response, "2026102336000644")

    @override_settings(OTRS_NOC_ATACADO_QUEUE_URL="https://suporte.example.test/otrs/index.pl?Action=AgentKPISearch")
    def test_official_queue_link_is_configured_and_external(self):
        self.assertEqual(otrs_queue_url(), "https://suporte.example.test/otrs/index.pl?Action=AgentKPISearch")
        response = self.client.get(reverse("chamados:index"))
        self.assertContains(response, "Abrir fila no OTRS")
        self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')

    def test_queue_filter_whitelist_and_case_normalization(self):
        self.assertEqual(sanitize_queue_filters({"tab": "open", "per_page": "50", "aging": True, "secret": "x"}), {
            "tab": "OPEN", "over_24h": True, "page_size": 50,
        })
        self.assertEqual(sanitize_queue_filters({"tab": "pending", "page_size": "100"}), {
            "tab": "PENDING", "page_size": 100,
        })

    def test_saved_views_are_private_whitelisted_and_default_can_be_cleared(self):
        save_url = reverse("chamados:queue_view_action")
        save = self.client.post(save_url, {
            "action": "save", "name": "Projeto A", "filters": json.dumps({
                "tab": "OPEN", "project": "Projeto A", "aging": True, "per_page": 50,
            }),
        })
        self.assertEqual(save.status_code, 200, save.content)
        view_id = save.json()["id"]
        view = TicketQueueView.objects.get(pk=view_id)
        self.assertEqual(view.filters["project"], "Projeto A")
        self.assertEqual(view.filters["per_page"], 50)
        self.assertTrue(view.filters["aging"])
        self.assertEqual(self.client.post(save_url, {
            "action": "save", "name": "Inválida", "filters": '{"project":"Projeto A","admin":true}',
        }).status_code, 400)

        applied = self.client.get(reverse("chamados:index"), {"saved_view": view_id})
        self.assertEqual(applied.context["filters"]["project"], "Projeto A")
        self.assertEqual(applied.context["page_size"], 50)

        self.client.post(save_url, {"action": "default", "id": view_id})
        self.assertEqual(self.client.get(reverse("chamados:index")).context["filters"]["project"], "Projeto A")
        explicit = self.client.get(reverse("chamados:index"), {"tab": "all"})
        self.assertEqual(explicit.context["filters"]["project"], "")
        self.assertEqual(explicit.context["page_size"], 50)
        cleared = self.client.get(reverse("chamados:index") + "?ignore_default=1")
        self.assertEqual(cleared.context["filters"]["project"], "")
        self.assertEqual(cleared.context["filters"]["tab"], "all")
        self.assertEqual(cleared.context["filters"]["arion_status"], "")
        self.assertEqual(cleared.context["filters"]["otrs_status"], "")
        self.assertEqual(cleared.context["filters"]["sort"], "aging")
        self.assertEqual(cleared.context["page_size"], 50)

        other_user = get_user_model().objects.create_user(username="queue-owner", password="safe-password-4")
        foreign = TicketQueueView.objects.create(user=other_user, name="Privada", filters={"project": "Projeto A"})
        self.assertEqual(self.client.post(save_url, {"action": "rename", "id": foreign.pk, "name": "Alterada"}).status_code, 404)
        foreign_view = self.client.get(reverse("chamados:index"), {"saved_view": foreign.pk})
        self.assertEqual(foreign_view.context["filters"]["project"], "")
        self.assertEqual(self.client.post(save_url, {"action": "rename", "id": view_id, "name": "Projeto A atualizado"}).status_code, 200)
        self.assertEqual(self.client.post(save_url, {"action": "delete", "id": view_id}).status_code, 200)
        self.assertTrue(TicketQueueView.objects.filter(pk=foreign.pk).exists())

    def test_inline_status_and_assume_use_local_tracking_service_with_revision(self):
        ticket = self.make_ticket(221, link=self.link)
        official_status = ticket.business_status
        url = reverse("chamados:tracking_update", args=(ticket.external_id,))
        response = self.client.post(url, {
            "action": "inline_status", "internal_status": "MASS_OUTAGE", "revision": 0,
        })
        self.assertEqual(response.status_code, 200, response.content)
        tracking = TicketTracking.objects.get(ticket_external_id=ticket.external_id)
        self.assertEqual(tracking.internal_status, "MASS_OUTAGE")
        self.assertEqual(ticket.__class__.objects.get(pk=ticket.pk).business_status, official_status)
        self.assertEqual(tracking.revision, 1)
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking=tracking, field_name="internal_status", old_value="Em Aberto", new_value="Massiva",
        ).exists())
        stale = self.client.post(url, {
            "action": "inline_status", "internal_status": "OPEN", "revision": 0,
        })
        self.assertEqual(stale.status_code, 409)
        assume = self.client.post(url, {"action": "assume", "revision": 1})
        self.assertEqual(assume.status_code, 200, assume.content)
        tracking.refresh_from_db()
        self.assertEqual(tracking.assigned_to, self.user)
        self.assertEqual(tracking.revision, 2)
        unassign = self.client.post(url, {"action": "unassign", "revision": 2})
        self.assertEqual(unassign.status_code, 200, unassign.content)
        tracking.refresh_from_db()
        self.assertIsNone(tracking.assigned_to)
        self.assertEqual(tracking.revision, 3)
        self.assertTrue(TicketTrackingEvent.objects.filter(
            tracking=tracking, field_name="assigned_to", old_value=self.user.get_full_name() or self.user.username,
            new_value="Responsável removido",
        ).exists())
        self.assertTrue(AuditEvent.objects.filter(
            action="tracking_unassigned", module="chamados", metadata__ticket_external_id=ticket.external_id,
        ).exists())
        not_owner = self.client.post(url, {"action": "unassign", "revision": 3})
        self.assertEqual(not_owner.status_code, 409)

    def test_tracking_post_requires_csrf_for_browser_clients(self):
        ticket = self.make_ticket(222, link=self.link)
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        url = reverse("chamados:index")
        browser.get(url)
        token = browser.cookies["csrftoken"].value
        update_url = reverse("chamados:tracking_update", args=(ticket.external_id,))
        denied = browser.post(update_url, {"action": "assume", "revision": 0})
        self.assertEqual(denied.status_code, 403)
        accepted = browser.post(update_url, {
            "action": "assume", "revision": 0, "csrfmiddlewaretoken": token,
        }, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200, accepted.content)

    def test_view_only_permission_cannot_change_tracking_or_personal_views(self):
        ticket = self.make_ticket(223, link=self.link)
        reader_group = Group.objects.get(name="Consulta / Auditoria")
        reader = get_user_model().objects.create_user(username="ticket-reader", password="safe-password-5")
        reader.groups.set([reader_group])
        self.client.force_login(reader)
        tracking = self.client.post(reverse("chamados:tracking_update", args=(ticket.external_id,)), {
            "action": "assume", "revision": 0,
        })
        status = self.client.post(reverse("chamados:tracking_update", args=(ticket.external_id,)), {
            "action": "inline_status", "internal_status": "RESOLVED", "revision": 0,
        })
        views = self.client.post(reverse("chamados:queue_view_action"), {
            "action": "save", "name": "Minha fila", "filters": "{}",
        })
        self.assertEqual(tracking.status_code, 403)
        self.assertEqual(status.status_code, 403)
        self.assertEqual(views.status_code, 403)
