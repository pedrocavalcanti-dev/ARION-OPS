"""Tests for FRENTE 4: Historical CMDB plant snapshots vs canonical OTRS tickets."""
from datetime import date, datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.access_control import GROUP_ANALYST, GROUP_SYSTEM_ADMIN, bootstrap_access
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    CMDBPlantMonthlySlice,
    CMDBPlantMonthlySnapshot,
    OTRSStateMapping,
    OTRSTicket,
    OTRSTicketConfigItem,
)
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME
from aplicativos.integracoes.services.cmdb_plant_snapshot import capture_current_cmdb_plant_snapshot
from aplicativos.painel.services.plant_history import PlantHistoryService

User = get_user_model()


class PlantHistoryTests(TestCase):
    def setUp(self):
        bootstrap_access()
        self.admin = User.objects.create_user(username="plant-admin", password="Password123!")
        self.admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])

    def _create_link(self, ci, *, client="Vivo", project="Bradesco", uf="SP", deployment_state="Operacional"):
        return CMDBLinkMirror.objects.create(
            source_config_item_id=ci,
            source_number=f"N-{ci}",
            designation=f"BL-{ci}",
            contracting_client=client,
            business_project=project,
            state=uf,
            deployment_state=deployment_state,
            is_present_in_source=True,
        )

    def _create_ticket(self, *, link, external_id, created_at):
        ticket = OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=f"2026{external_id:012d}",
            title=f"Incidente {link.designation}",
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=2,
            external_state_name="Resolvido",
            business_status=OTRSStateMapping.BusinessStatus.RESOLVED,
            responsibility_normalized="PROCEDENTE",
            source_created_at=created_at,
            is_present_in_queue=True,
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket,
            config_item_id=link.source_config_item_id,
            cmdb_link=link,
            relation_type="DependsOn",
            relation_state="Valid",
            is_present_in_source=True,
        )
        return ticket

    def test_snapshot_capture_is_idempotent_and_never_fabricates_earlier_months(self):
        link1 = self._create_link("P1", client="Vivo", project="Bradesco", uf="SP")
        link2 = self._create_link("P2", client="Vivo", project="Bradesco", uf="SP")
        self._create_link("P3", client="Vivo", project="Bradesco", uf="SP", deployment_state="Cancelado")
        link4 = self._create_link("P4", client="Claro", project="PROJETO BB", uf="RJ")

        now = datetime(2026, 11, 19, 15, 0, tzinfo=dt_timezone.utc)
        snap1 = capture_current_cmdb_plant_snapshot(now=now)
        snap2 = capture_current_cmdb_plant_snapshot(now=now)
        self.assertEqual(snap1.pk, snap2.pk)
        self.assertEqual(snap2.total_links, 4)
        self.assertEqual(snap2.active_links, 3)  # P3 is canceled
        self.assertEqual(snap2.canceled_links, 1)
        self.assertTrue(snap2.is_partial_month)

        # Create tickets in Sep/2026 (no snapshot!) and Nov/2026 (snapshot exists!)
        self._create_ticket(link=link1, external_id=88001, created_at=datetime(2026, 9, 10, 14, 0, tzinfo=dt_timezone.utc))
        self._create_ticket(link=link1, external_id=88002, created_at=datetime(2026, 11, 5, 14, 0, tzinfo=dt_timezone.utc))
        self._create_ticket(link=link1, external_id=88003, created_at=datetime(2026, 11, 10, 14, 0, tzinfo=dt_timezone.utc))
        self._create_ticket(link=link4, external_id=88004, created_at=datetime(2026, 11, 12, 14, 0, tzinfo=dt_timezone.utc))

        result = PlantHistoryService.build(
            self.admin,
            {"start": "2026-09-01", "end": "2026-11-19"},
            now=now,
        )
        by_month = {row["month"]: row for row in result["series"]}

        # September has tickets, NO CMDB snapshot -> UNAVAILABLE, never backfilled!
        sep = by_month["2026-09"]
        self.assertEqual(sep["tickets"], 1)
        self.assertIsNone(sep["active_links"])
        self.assertIsNone(sep["tickets_per_100_links"])
        self.assertIsNone(sep["affected_links_pct"])
        self.assertEqual(sep["coverage_status"], "UNAVAILABLE")
        self.assertEqual(sep["coverage_label"], "Base histórica indisponível")

        # November has snapshot (3 active links), 3 tickets, 2 affected links (link1 recurrent + link4)
        nov = by_month["2026-11"]
        self.assertEqual(nov["active_links"], 3)
        self.assertEqual(nov["tickets"], 3)
        self.assertEqual(nov["affected_links"], 2)
        self.assertEqual(nov["recurrent_links"], 1)
        self.assertEqual(nov["tickets_per_100_links"], 100.0)  # (3/3)*100
        self.assertEqual(nov["affected_links_pct"], 66.67)     # (2/3)*100
        self.assertTrue(nov["is_partial_month"])

        # Filter by operator=Claro & project=Banco do Brasil (canonical alias of PROJETO BB)
        filtered = PlantHistoryService.build(
            self.admin,
            {"start": "2026-11-01", "end": "2026-11-19", "operator": "Claro", "project": "Banco do Brasil"},
            now=now,
        )
        nov_claro = filtered["series"][0]
        self.assertEqual(nov_claro["active_links"], 1)
        self.assertEqual(nov_claro["tickets"], 1)
        self.assertEqual(nov_claro["tickets_per_100_links"], 100.0)
        self.assertEqual(nov_claro["affected_links_pct"], 100.0)

        # Overview strictly separates registered vs active vs canceled vs unidentified
        self._create_link("P5", client="", project="", uf="MG")
        full = PlantHistoryService.build(
            self.admin,
            {"start": "2026-11-01", "end": "2026-11-19"},
            now=now,
        )
        ov = full["overview"]
        self.assertEqual(ov["total_registered"], 5)
        self.assertEqual(ov["active_links"], 4)
        self.assertEqual(ov["canceled_links"], 1)
        self.assertEqual(ov["unidentified_links"], 1)
        self.assertEqual(ov["unidentified_operator_links"], 1)
        self.assertEqual(ov["unidentified_project_links"], 1)
        by_op = {row["key"]: row for row in ov["operator_distribution"]}
        self.assertEqual(by_op["vivo"]["total"], 3)
        self.assertEqual(by_op["vivo"]["active"], 2)
        self.assertEqual(by_op["claro"]["total"], 1)
        self.assertEqual(by_op["algar"]["total"], 0)
        self.assertEqual(by_op["unidentified"]["total"], 1)
        self.assertEqual(ov["period_totals"]["tickets"], 3)
        self.assertEqual(ov["period_totals"]["affected_links"], 2)
        self.assertEqual(ov["period_totals"]["recurrent_links"], 1)

        # Region filter (SE = SP, RJ, MG, ES) matches all 5 links; NE returns 0
        se_res = PlantHistoryService.build(
            self.admin,
            {"start": "2026-11-01", "end": "2026-11-19", "region": "SE"},
            now=now,
        )
        self.assertEqual(se_res["selected"]["region"], "SE")
        self.assertEqual(se_res["overview"]["total_registered"], 5)
        self.assertEqual(se_res["series"][0]["active_links"], 3)

    def test_plant_history_enforces_data_scope_and_zero_denominator_safety(self):
        op_claro = Operadora.objects.create(name="Claro", normalized_name="claro")
        op_vivo = Operadora.objects.create(name="Vivo", normalized_name="vivo")
        proj_bb = Projeto.objects.create(name="Banco do Brasil", normalized_name="banco do brasil")
        proj_brad = Projeto.objects.create(name="Bradesco", normalized_name="bradesco")

        claro_link = self._create_link("SC1", client="Claro", project="Banco do Brasil", uf="SP")
        vivo_link = self._create_link("SC2", client="Vivo", project="Bradesco", uf="SP")
        now = datetime(2026, 11, 19, 15, 0, tzinfo=dt_timezone.utc)
        capture_current_cmdb_plant_snapshot(now=now)
        self._create_ticket(link=claro_link, external_id=88101, created_at=datetime(2026, 11, 5, 14, 0, tzinfo=dt_timezone.utc))
        self._create_ticket(link=vivo_link, external_id=88102, created_at=datetime(2026, 11, 6, 14, 0, tzinfo=dt_timezone.utc))

        scoped_group = Group.objects.create(name="Escopo Claro BB")
        scoped_group.permissions.set(Group.objects.get(name=GROUP_ANALYST).permissions.all())
        scope = GroupDataScope.objects.create(group=scoped_group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.set([op_claro])
        scope.allowed_projects.set([proj_bb])
        scoped_user = User.objects.create_user(username="scoped-plant-user", password="Password123!")
        scoped_user.groups.set([scoped_group])

        scoped_result = PlantHistoryService.build(
            scoped_user,
            {"start": "2026-11-01", "end": "2026-11-19"},
            now=now,
        )
        nov = scoped_result["series"][0]
        # Only Claro/BB link & ticket visible — Vivo/Bradesco never leaks!
        self.assertEqual(nov["active_links"], 1)
        self.assertEqual(nov["tickets"], 1)

        # Manual request for out-of-scope operator=Vivo cannot leak Vivo data
        tampered = PlantHistoryService.build(
            scoped_user,
            {"start": "2026-11-01", "end": "2026-11-19", "operator": "Vivo"},
            now=now,
        )
        self.assertEqual(tampered["series"][0]["active_links"], 1)
        self.assertEqual(tampered["series"][0]["tickets"], 1)
        self.assertEqual(tampered["overview"]["total_registered"], 1)
        self.assertEqual(tampered["overview"]["active_links"], 1)

        # Zero denominator snapshot (all links canceled) never divides by zero
        zero_snap = CMDBPlantMonthlySnapshot.objects.create(
            month=date(2026, 10, 1),
            captured_at=datetime(2026, 10, 31, 12, 0, tzinfo=dt_timezone.utc),
            total_links=1,
            active_links=0,
            canceled_links=1,
        )
        CMDBPlantMonthlySlice.objects.create(
            snapshot=zero_snap,
            contracting_client="Claro",
            canonical_project="BANCO DO BRASIL",
            uf="SP",
            total_links=1,
            active_links=0,
            canceled_links=1,
        )
        zero_res = PlantHistoryService.build(
            scoped_user,
            {"start": "2026-10-01", "end": "2026-10-31"},
            now=now,
        )
        self.assertEqual(zero_res["series"][0]["active_links"], 0)
        self.assertIsNone(zero_res["series"][0]["tickets_per_100_links"])
        self.assertIsNone(zero_res["series"][0]["affected_links_pct"])

    def test_dashboard_renders_plant_history_panel_and_api_enforces_auth(self):
        unauth = self.client.get(reverse("analytics-plant-history"))
        self.assertEqual(unauth.status_code, 401)

        no_perm_user = User.objects.create_user(username="no-perm-plant", password="Password123!")
        self.client.force_login(no_perm_user)
        forbidden = self.client.get(reverse("analytics-plant-history"))
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.admin)
        api_resp = self.client.get(reverse("analytics-plant-history"))
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertIn("series", payload)
        self.assertIn("summary", payload)
        self.assertIn("overview", payload)

        dash_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "Base CMDB")
        self.assertContains(dash_resp, "Analisar planta")
        self.assertContains(dash_resp, "Evolução da planta × Volume de chamados")
        self.assertContains(dash_resp, "plant-history-initial-data")
        self.assertContains(dash_resp, 'id="plant-history-modal"')

        cockpit_resp = self.client.get(reverse("cockpit"))
        self.assertEqual(cockpit_resp.status_code, 200)
        self.assertContains(cockpit_resp, "data-plant-sync-cockpit")
        self.assertContains(cockpit_resp, 'id="plant-history-modal"')
        self.assertContains(cockpit_resp, "Comparação por período")
