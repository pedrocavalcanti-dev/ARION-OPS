from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    CMDBOperatorMirror,
    OTRSTicket,
    OTRSTicketConfigItem,
    OperationalIdentityRevision,
    OperationalLinkIdentity,
)
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME
from aplicativos.integracoes.services.cmdb_sync import CMDBSyncService
from aplicativos.integracoes.services.link_business_identity import LinkBusinessIdentityService
from aplicativos.integracoes.services.operational_identity import (
    OperationalIdentityService,
    build_compact_ticket_title,
)
from aplicativos.integracoes.services.ticket_business_identity import TicketBusinessIdentityService
from aplicativos.painel.services.analytics import DashboardAnalyticsService


class FakeCMDBClient:
    requests_count = 1
    batch_size = 50

    def __init__(self, operators, links):
        self._operators = operators
        self._links = links

    def search_item_ids(self, *, class_name, limit=None):
        rows = self._operators if class_name == "Operadoras" else self._links
        return [row["ConfigItemID"] for row in rows]

    def get_items(self, ids):
        by_id = {row["ConfigItemID"]: row for row in (*self._operators, *self._links)}
        return [by_id[item_id] for item_id in ids]


class OperationalIdentityRulesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin_id", "admin@arion.local", "StrongPass123!")
        self.isp_fierce = CMDBOperatorMirror.objects.create(
            source_config_item_id="ISP-1", name="FIERCE TELECOM", is_present_in_source=True,
        )
        self.isp_gamanet = CMDBOperatorMirror.objects.create(
            source_config_item_id="ISP-2", name="GamaNet Telecom", is_present_in_source=True,
        )
        self.isp_arion = CMDBOperatorMirror.objects.create(
            source_config_item_id="ISP-3", name="ARION", is_present_in_source=True,
        )

    def _make_ticket(self, external_id, number, title, *, customer_id="", days_ago=1):
        return OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=number,
            title=title,
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=1,
            external_state_name="open",
            business_status="OPEN",
            is_business_open=True,
            customer_id=customer_id,
            source_created_at=timezone.now() - timedelta(days=days_ago),
        )

    def test_example_a_vivo_bradesco_preserves_third_party_isp(self):
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="6058",
            name="BRADESCO - BL50000080775",
            designation="BL50000080775",
            operator=self.isp_fierce,
            isp_raw="ISP-1",
            state="SP",
        )
        ticket = self._make_ticket(
            527, "2026111836000527",
            "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL50000080775 - BRAD: 668",
            customer_id="joaol.santos@telefonica.com",
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id="6058", cmdb_link=link,
            relation_type="DependsOn", relation_state="Valid", is_present_in_source=True,
        )

        summary = OperationalIdentityService.reconcile_all(apply=True)
        link.refresh_from_db()
        identity = link.operational_identity

        self.assertEqual(link.contracting_client, "Vivo")
        self.assertEqual(link.business_project, "Bradesco")
        self.assertEqual(link.business_id, "BL50000080775")
        self.assertEqual(link.operator.name, "FIERCE TELECOM")
        self.assertEqual(identity.final_client, "Bradesco")
        self.assertEqual(identity.source, "OTRS_DIRECT_RELATION")
        self.assertEqual(identity.issue_type, OperationalLinkIdentity.IssueType.NONE)
        self.assertGreaterEqual(summary["reduction"]["missing_operator"], 1)
        self.assertEqual(
            build_compact_ticket_title(ticket, [link]),
            "Vivo · Bradesco · BL50000080775",
        )

    def test_example_b_bradesco_telmex_preserves_raw_reference(self):
        parsed = TicketBusinessIdentityService.parse(
            "BRAD - TELMEX - RESTAURAÇÃO - INTERNET - BRADESCO - BL10000078874 - CI: 3429920689"
        )
        self.assertEqual(parsed.client, "Claro")
        self.assertEqual(parsed.client_raw, "TELMEX")
        self.assertEqual(parsed.project, "Bradesco")
        self.assertEqual(parsed.final_client, "Bradesco")
        self.assertEqual(parsed.business_id, "BL10000078874")
        self.assertEqual(parsed.ci_reference, "3429920689")

        link = CMDBLinkMirror.objects.create(
            source_config_item_id="78874",
            name="BRADESCO - BL10000078874",
            designation="BL10000078874",
            operator=self.isp_fierce,
        )
        ticket = self._make_ticket(
            1133, "2026111836001133",
            "BRAD - TELMEX - RESTAURAÇÃO - INTERNET - BRADESCO - BL10000078874 - CI: 3429920689",
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id="78874", cmdb_link=link,
            relation_type="DependsOn", relation_state="Valid", is_present_in_source=True,
        )
        OperationalIdentityService.reconcile_all(apply=True)
        link.refresh_from_db()
        self.assertEqual(link.contracting_client, "Claro")
        self.assertEqual(link.contracting_client_raw, "TELMEX")
        self.assertEqual(link.business_project, "Bradesco")

    def test_example_c_claro_banco_csf_carrefour_never_generalizes_diversos(self):
        link_csf = CMDBLinkMirror.objects.create(
            source_config_item_id="6391",
            name="BANCO CSF - BL150000094228",
            designation="BL150000094228",
            operator=self.isp_gamanet,
        )
        link_generic = CMDBLinkMirror.objects.create(
            source_config_item_id="9999",
            name="DIVERSOS - BL999000111",
            designation="BL999000111",
            operator=self.isp_gamanet,
        )
        ticket_csf = self._make_ticket(
            1161, "2026111836001161",
            "Não Geográficos (Urbanos) - CLARO S.A. - RESTAURAÇÃO - INTERNET PROJETO DIVERSOS - BL150000094228 - CI:734259",
        )
        ticket_generic = self._make_ticket(
            1162, "2026111836001162",
            "Não Geográficos (Urbanos) - CLARO S.A. - RESTAURAÇÃO - INTERNET PROJETO DIVERSOS - BL999000111 - CI:734260",
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket_csf, config_item_id="6391", cmdb_link=link_csf,
            relation_type="DependsOn", relation_state="Valid", is_present_in_source=True,
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket_generic, config_item_id="9999", cmdb_link=link_generic,
            relation_type="DependsOn", relation_state="Valid", is_present_in_source=True,
        )

        OperationalIdentityService.reconcile_all(apply=True)
        link_csf.refresh_from_db()
        link_generic.refresh_from_db()

        self.assertEqual(link_csf.contracting_client, "Claro")
        self.assertEqual(link_csf.business_project, "Carrefour")
        self.assertEqual(link_csf.operational_identity.final_client, "Banco CSF")
        self.assertEqual(link_csf.business_account, "Banco CSF")
        self.assertEqual(link_csf.operator.name, "GamaNet Telecom")
        self.assertEqual(
            build_compact_ticket_title(ticket_csf, [link_csf]),
            "Claro · Banco CSF (Carrefour) · BL150000094228",
        )

        # Generic "PROJETO DIVERSOS" must NOT be promoted to Carrefour.
        self.assertEqual(link_generic.contracting_client, "Claro")
        self.assertEqual(link_generic.business_project, "Diversos")
        self.assertNotEqual(link_generic.business_project, "Carrefour")

    def test_fust_ubs_and_regional_prefixes_resolve_actual_operator(self):
        claro_fust = LinkBusinessIdentityService.parse("FUST UBS - CLARO.SC.BLIVR001101")
        vivo_fust = LinkBusinessIdentityService.parse("FUST UBS - VIVO.PE.BLIVR000252")
        telmex_sa = LinkBusinessIdentityService.parse("TELMEX DO BRASIL S/A - BRADESCO - BL50000098680")
        self.assertEqual((claro_fust.contracting_client, claro_fust.business_project), ("Claro", "Fust"))
        self.assertEqual((vivo_fust.contracting_client, vivo_fust.business_project), ("Vivo", "Fust"))
        self.assertEqual((telmex_sa.contracting_client, telmex_sa.business_project), ("Claro", "Bradesco"))

    def test_internal_arion_pop_classified_as_other_without_inventing_client(self):
        pop_link = CMDBLinkMirror.objects.create(
            source_config_item_id="10173",
            name="CNF - POP ARION - MG.BLIVR000501",
            designation="MG.BLIVR000501",
            operator=self.isp_arion,
        )
        false_positive = CMDBLinkMirror.objects.create(
            source_config_item_id="7284",
            name="GRUPO WISH – MARUPIARA RESORT - PE",
            designation="JPJ 13829 (CNPJ ARION 20059578000120)",
            operator=self.isp_fierce,
        )
        OperationalIdentityService.reconcile_all(apply=True)
        pop_link.refresh_from_db()
        false_positive.refresh_from_db()

        self.assertTrue(pop_link.operational_identity.is_internal_arion)
        self.assertEqual(pop_link.contracting_client, "ARION")
        self.assertEqual(pop_link.operational_identity.final_client, "")
        self.assertEqual(pop_link.operational_identity.issue_type, OperationalLinkIdentity.IssueType.NONE)

        self.assertFalse(false_positive.operational_identity.is_internal_arion)
        self.assertEqual(false_positive.contracting_client, "")

        # Dashboard distribution groups ARION under "Outras" (not Claro/Vivo/Algar nor Unidentified)
        dist = {row["key"]: row["value"] for row in DashboardAnalyticsService._client_distribution(CMDBLinkMirror.objects.filter(pk=pop_link.pk))}
        self.assertEqual(dist["other"], 1)
        self.assertEqual(dist["unidentified"], 0)

    def test_history_consensus_requires_three_distinct_tickets_and_detects_conflict(self):
        link_three = CMDBLinkMirror.objects.create(
            source_config_item_id="H3",
            name="CIRCUITO SEM OPERADORA - BL880000111",
            designation="BL880000111",
        )
        link_two = CMDBLinkMirror.objects.create(
            source_config_item_id="H2",
            name="CIRCUITO SEM OPERADORA - BL880000222",
            designation="BL880000222",
        )
        link_conflict = CMDBLinkMirror.objects.create(
            source_config_item_id="HC",
            name="BRADESCO - BL880000333",
            designation="BL880000333",
        )
        for i in range(3):
            self._make_ticket(
                8000 + i, f"20261110000{i}",
                f"BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL880000111 - CI: {i}",
                days_ago=i + 1,
            )
        for i in range(2):
            self._make_ticket(
                8100 + i, f"20261110010{i}",
                f"BRAD - CLARO S.A. - RESTAURAÇÃO - INTERNET - BRADESCO - BL880000222 - CI: {i}",
                days_ago=i + 1,
            )
        t_c1 = self._make_ticket(8201, "202611100201", "BRAD - CLARO S.A. - RESTAURAÇÃO - BRADESCO - BL880000333")
        t_c2 = self._make_ticket(8202, "202611100202", "BRAD - TELEFONICA BRASIL S.A. - RESTAURAÇÃO - BRADESCO - BL880000333")
        OTRSTicketConfigItem.objects.create(ticket=t_c1, config_item_id="HC", cmdb_link=link_conflict, relation_type="DependsOn", relation_state="Valid")
        OTRSTicketConfigItem.objects.create(ticket=t_c2, config_item_id="HC", cmdb_link=link_conflict, relation_type="DependsOn", relation_state="Valid")

        OperationalIdentityService.reconcile_all(apply=True)
        link_three.refresh_from_db()
        link_two.refresh_from_db()
        link_conflict.refresh_from_db()

        self.assertEqual(link_three.contracting_client, "Vivo")
        self.assertEqual(link_three.business_project, "Bradesco")
        self.assertEqual(link_three.operational_identity.source, "OTRS_HISTORY_CONSENSUS")

        # Only 2 indirect tickets -> suggestion only, not auto-promoted.
        self.assertEqual(link_two.contracting_client, "")
        self.assertTrue(link_two.operational_identity.has_suggestion)
        self.assertEqual(link_two.operational_identity.suggested_operator, "Claro")
        self.assertEqual(link_two.operational_identity.suggested_project, "Bradesco")

        # Conflict -> stays unresolved and flags conflict.
        self.assertEqual(link_conflict.contracting_client, "")
        self.assertTrue(link_conflict.operational_identity.has_conflict)
        self.assertEqual(link_conflict.operational_identity.issue_type, OperationalLinkIdentity.IssueType.CONFLICT)

    def test_manual_override_survives_cmdb_sync_and_flags_contradictory_source(self):
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="M1",
            source_number="3613100001",
            name="BRADESCO - BL770000111",
            designation="BL770000111",
        )
        req = RequestFactory().post("/")
        req.user = self.user
        OperationalIdentityService.apply_manual_resolution(
            request=req,
            link=link,
            action=OperationalIdentityRevision.Action.MANUAL_UPDATE,
            operator="Vivo",
            final_client="Bradesco",
            project="Bradesco",
            justification="Confirmado em contrato Vivo.",
        )

        # Simulate a subsequent CMDB sync whose name contradicts the manual classification.
        client = FakeCMDBClient(
            operators=[{
                "ConfigItemID": "ISP-1", "Number": "3610001", "Name": "FIERCE TELECOM", "Class": "Operadoras",
            }],
            links=[{
                "ConfigItemID": "M1", "Number": "3613100001", "Name": "CLARO - BRADESCO - BL770000111",
                "Class": "Links de internet", "CIXMLData": {"Designacao": "BL770000111", "ISP": "ISP-1"},
            }],
        )
        CMDBSyncService(client).run(apply=True)
        link.refresh_from_db()
        identity = link.operational_identity

        # Manual classification is preserved, never overwritten!
        self.assertEqual(link.contracting_client, "Vivo")
        self.assertTrue(identity.is_manual_override)
        self.assertEqual(identity.contracting_client, "Vivo")
        # And the contradictory CMDB update flags a conflict for human review.
        self.assertTrue(identity.has_conflict)
        self.assertIn("divergindo da classificação manual", identity.conflict_reason)
