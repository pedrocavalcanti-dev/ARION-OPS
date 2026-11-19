from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.access_control import bootstrap_access
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    CMDBOperatorMirror,
    OTRSTicket,
    OTRSTicketConfigItem,
    OperationalIdentityRevision,
    OperationalLinkIdentity,
)
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME
from aplicativos.integracoes.services.operational_identity import OperationalIdentityService


class UnidentifiedIdentityCenterTests(TestCase):
    def setUp(self):
        bootstrap_access()
        User = get_user_model()
        self.admin = User.objects.create_superuser("id_admin", "id_admin@arion.local", "StrongPass123!")

        self.viewer = User.objects.create_user("id_viewer", "viewer@arion.local", "StrongPass123!")
        viewer_group = Group.objects.create(name="Visualizador Base Teste")
        viewer_group.permissions.add(
            Permission.objects.get(codename="view_base"),
            Permission.objects.get(codename="export_base"),
        )
        GroupDataScope.objects.create(group=viewer_group, mode=GroupDataScope.Mode.ALL)
        self.viewer.groups.add(viewer_group)

        self.scoped_user = User.objects.create_user("id_scoped", "scoped@arion.local", "StrongPass123!")
        scoped_group = Group.objects.create(name="Escopo Vivo Bradesco")
        scoped_group.permissions.add(
            Permission.objects.get(codename="view_base"),
            Permission.objects.get(codename="manage_operational_identity"),
        )
        op_vivo, _ = Operadora.objects.get_or_create(name="Vivo")
        proj_brad, _ = Projeto.objects.get_or_create(name="Bradesco")
        scope = GroupDataScope.objects.create(group=scoped_group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(op_vivo)
        scope.allowed_projects.add(proj_brad)
        self.scoped_user.groups.add(scoped_group)

        self.isp = CMDBOperatorMirror.objects.create(source_config_item_id="ISP-10", name="ISP TESTE")
        self.pending_link = CMDBLinkMirror.objects.create(
            source_config_item_id="CI-PEND-1",
            name="BRADESCO - BL440000100",
            designation="BL440000100",
            operator=self.isp,
            state="SP",
            city="São Paulo",
        )
        self.vivo_link = CMDBLinkMirror.objects.create(
            source_config_item_id="CI-VIVO-1",
            name="VIVO - BRADESCO - BL440000200",
            designation="BL440000200",
            operator=self.isp,
            state="RJ",
        )
        self.arion_link = CMDBLinkMirror.objects.create(
            source_config_item_id="CI-POP-1",
            name="CNF - POP ARION - MG.BLIVR000501",
            designation="MG.BLIVR000501",
            operator=self.isp,
            state="MG",
        )
        # Attach two tickets to pending_link to prove no duplicate rows in queue
        self.t1 = OTRSTicket.objects.create(
            external_id=9001, ticket_number="202611190001",
            title="RESTAURAÇÃO - INTERNET - BL440000100",
            queue_external_id=1, queue_name=NOC_QUEUE_NAME,
            external_state_id=1, external_state_name="open",
            business_status="OPEN", is_business_open=True,
            source_created_at=timezone.now(),
        )
        self.t2 = OTRSTicket.objects.create(
            external_id=9002, ticket_number="202611190002",
            title="ROMPIMENTO - INTERNET - BL440000100",
            queue_external_id=1, queue_name=NOC_QUEUE_NAME,
            external_state_id=1, external_state_name="open",
            business_status="OPEN", is_business_open=True,
            source_created_at=timezone.now(),
        )
        self.unlinked_ticket = OTRSTicket.objects.create(
            external_id=9003, ticket_number="202611190003",
            title="Projeto Correios - CLARO S.A. - RESTAURAÇÃO - BL990000300",
            queue_external_id=1, queue_name=NOC_QUEUE_NAME,
            external_state_id=1, external_state_name="open",
            business_status="OPEN", is_business_open=True,
            source_created_at=timezone.now(),
        )
        OTRSTicketConfigItem.objects.create(ticket=self.t1, config_item_id="CI-PEND-1", cmdb_link=self.pending_link, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=self.t2, config_item_id="CI-PEND-1", cmdb_link=self.pending_link, relation_type="DependsOn", relation_state="Valid")

        OperationalIdentityService.reconcile_all(apply=True)

    def test_queue_separates_arion_pop_and_does_not_duplicate_links(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("base_links:unidentified"))
        self.assertEqual(resp.status_code, 200)
        rows = resp.context["identity_rows"]
        link_ids = [row["link_id"] for row in rows]
        self.assertEqual(link_ids, [self.pending_link.pk])
        self.assertNotIn(self.arion_link.pk, link_ids)
        self.assertEqual(resp.context["kpis"]["internal_arion"], 1)
        self.assertEqual(resp.context["kpis"]["unlinked_tickets"], 1)

        # ARION/POP tab shows internal links explicitly
        resp_arion = self.client.get(reverse("base_links:unidentified"), {"tab": "arion", "review_status": "all"})
        self.assertEqual(resp_arion.status_code, 200)
        self.assertEqual([row["link_id"] for row in resp_arion.context["identity_rows"]], [self.arion_link.pk])

        # Unlinked tickets tab shows unlinked ticket
        resp_tickets = self.client.get(reverse("base_links:unidentified"), {"tab": "tickets"})
        self.assertEqual(resp_tickets.status_code, 200)
        self.assertEqual([row["id"] for row in resp_tickets.context["ticket_rows"]], [self.unlinked_ticket.pk])

    def test_detail_and_manual_correction_create_revision_and_audit(self):
        self.client.force_login(self.admin)
        detail = self.client.get(reverse("base_links:unidentified-link-detail", args=[self.pending_link.pk]))
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["link"]["config_item_id"], "CI-PEND-1")
        self.assertEqual(len(payload["tickets"]), 2)

        resolve = self.client.post(
            reverse("base_links:unidentified-link-resolve", args=[self.pending_link.pk]),
            {
                "action": OperationalIdentityRevision.Action.MANUAL_UPDATE,
                "operator": "Vivo",
                "final_client": "Bradesco",
                "project": "Bradesco",
                "justification": "Confirmado na planilha contratual.",
            },
        )
        self.assertEqual(resolve.status_code, 200)
        self.assertTrue(resolve.json()["ok"])

        self.pending_link.refresh_from_db()
        self.assertEqual(self.pending_link.contracting_client, "Vivo")
        self.assertEqual(self.pending_link.business_project, "Bradesco")
        self.assertTrue(self.pending_link.operational_identity.is_manual_override)
        self.assertEqual(OperationalIdentityRevision.objects.filter(link=self.pending_link).count(), 1)
        self.assertTrue(AuditEvent.objects.filter(category="operational_identity").exists())

    def test_rbac_and_data_scope_enforced_on_review_endpoints(self):
        # Viewer without manage_operational_identity cannot resolve
        self.client.force_login(self.viewer)
        forbidden = self.client.post(
            reverse("base_links:unidentified-link-resolve", args=[self.pending_link.pk]),
            {"action": "MARK_UNKNOWN"},
        )
        self.assertEqual(forbidden.status_code, 403)

        # CSV export works for user with export_base
        csv_resp = self.client.get(reverse("base_links:unidentified-export"))
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("CI-PEND-1", csv_resp.content.decode("utf-8"))

        # Scoped user cannot see or modify links outside Vivo/Bradesco scope, nor unlinked tickets
        self.client.force_login(self.scoped_user)
        resp = self.client.get(reverse("base_links:unidentified"), {"review_status": "all"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row["link_id"] for row in resp.context["identity_rows"]], [self.vivo_link.pk])
        self.assertEqual(resp.context["kpis"]["unlinked_tickets"], 0)

        out_of_scope = self.client.post(
            reverse("base_links:unidentified-link-resolve", args=[self.pending_link.pk]),
            {"action": "MARK_UNKNOWN"},
        )
        self.assertEqual(out_of_scope.status_code, 404)

    def test_chamados_drawer_returns_compact_and_original_title(self):
        self.client.force_login(self.admin)
        # Re-point t1 to vivo_link to verify compact title format
        OTRSTicketConfigItem.objects.create(
            ticket=self.t1, config_item_id="CI-VIVO-1", cmdb_link=self.vivo_link,
            relation_type="DependsOn", relation_state="Valid",
        )
        OTRSTicketConfigItem.objects.filter(ticket=self.t1, config_item_id="CI-PEND-1").delete()
        detail = self.client.get(reverse("chamados:detail", args=[self.t1.external_id]))
        self.assertEqual(detail.status_code, 200)
        data = detail.json()
        self.assertEqual(data["ticket"]["compact_title"], "Vivo · Bradesco · BL440000200")
        self.assertEqual(data["ticket"]["title"], "RESTAURAÇÃO - INTERNET - BL440000100")
