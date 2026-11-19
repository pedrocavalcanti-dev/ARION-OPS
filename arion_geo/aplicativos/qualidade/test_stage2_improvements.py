"""Tests for Qualidade Stage 2/2: Stabilized Links, R1..R5+ OQ Levels, and Contacts RBAC/CRUD."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.access_control import (
    GROUP_ANALYST,
    GROUP_MANAGER,
    GROUP_SYSTEM_ADMIN,
    PERMISSION_LABELS,
    bootstrap_access,
    managed_permissions,
)
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import (
    CMDBLinkMirror,
    OTRSStateMapping,
    OTRSTicket,
    OTRSTicketConfigItem,
)
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME
from aplicativos.qualidade.models import (
    QualityContact,
    QualityMonitoringCycle,
    QualityOrder,
)
from aplicativos.qualidade.services.dashboard import get_dashboard_data
from aplicativos.qualidade.services.order_detail import get_quality_order_detail
from aplicativos.qualidade.services.orders_queue import get_quality_orders_queue
from aplicativos.qualidade.services.recurrence import build_quality_recurrence_analysis
from aplicativos.qualidade.services.stabilized_links import get_stabilized_links

User = get_user_model()


class QualityStage2Tests(TestCase):
    def setUp(self):
        bootstrap_access()
        self.admin = User.objects.create_user(username="stage2-admin", password="Password123!")
        self.admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])
        self.manager = User.objects.create_user(username="stage2-manager", password="Password123!")
        manager_group = Group.objects.get(name=GROUP_MANAGER)
        GroupDataScope.objects.update_or_create(
            group=manager_group, defaults={"mode": GroupDataScope.Mode.ALL},
        )
        self.manager.groups.set([manager_group])
        self.analyst = User.objects.create_user(username="stage2-analyst", password="Password123!")
        analyst_group = Group.objects.get(name=GROUP_ANALYST)
        GroupDataScope.objects.update_or_create(
            group=analyst_group, defaults={"mode": GroupDataScope.Mode.ALL},
        )
        self.analyst.groups.set([analyst_group])

    def _create_link(self, ci, *, designation="", client="Vivo", project="Bradesco", isp="ISP Teste", uf="SP", state="Operacional"):
        return CMDBLinkMirror.objects.create(
            source_config_item_id=ci,
            source_number=f"N-{ci}",
            designation=designation or f"LINK-{ci}",
            name=designation or f"LINK-{ci}",
            contracting_client=client,
            business_project=project,
            isp_raw=isp,
            state=uf,
            deployment_state=state,
            is_present_in_source=True,
        )

    def _create_ticket(
        self,
        *,
        link,
        external_id,
        created_at,
        status=OTRSStateMapping.BusinessStatus.RESOLVED,
        responsibility="PROCEDENTE",
        closing_reason="Rompimento de Fibra",
    ):
        ticket = OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=f"2026{external_id:012d}",
            title=f"Incidente {link.designation}",
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=2,
            external_state_name="Resolvido" if status == OTRSStateMapping.BusinessStatus.RESOLVED else "Aberto",
            business_status=status,
            is_business_open=status in (
                OTRSStateMapping.BusinessStatus.OPEN,
                OTRSStateMapping.BusinessStatus.PENDING,
                OTRSStateMapping.BusinessStatus.SCHEDULED,
            ),
            responsibility_normalized=responsibility,
            closing_code_raw=closing_reason,
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

    # --- FRENTE 1: Links estabilizados ---

    def test_stabilized_links_view_lists_all_deduplicated_and_excludes_active_incidents(self):
        now = timezone.now()
        # Create 7 stabilized links (more than Top 5)
        for idx in range(1, 8):
            link = self._create_link(
                f"STAB-{idx}",
                designation=f"BD50000{idx}",
                client="Claro" if idx % 2 else "Vivo",
                project="Banco do Brasil" if idx % 2 else "Bradesco",
                uf="SP" if idx <= 4 else "RJ",
            )
            self._create_ticket(link=link, external_id=91000 + idx * 10 + 1, created_at=now - timedelta(days=50 + idx))
            self._create_ticket(link=link, external_id=91000 + idx * 10 + 2, created_at=now - timedelta(days=34 + idx))

        # Circuit with last ticket >30d ago BUT still OPEN -> must NOT be stabilized!
        open_old_link = self._create_link("STAB-OPEN-OLD", designation="BD500999", client="Vivo", project="Bradesco")
        self._create_ticket(link=open_old_link, external_id=92001, created_at=now - timedelta(days=48))
        self._create_ticket(
            link=open_old_link,
            external_id=92002,
            created_at=now - timedelta(days=35),
            status=OTRSStateMapping.BusinessStatus.OPEN,
        )

        # Vivo circuit with 'Sem intervenção' as 2nd ticket -> excluded for Vivo (only 1 valid -> not stabilized)
        vivo_sem = self._create_link("STAB-VIVO-SEM", designation="BD500888", client="Vivo", project="Bradesco")
        self._create_ticket(link=vivo_sem, external_id=92011, created_at=now - timedelta(days=48))
        self._create_ticket(
            link=vivo_sem,
            external_id=92012,
            created_at=now - timedelta(days=35),
            closing_reason="Sem Intervenção",
        )

        # Claro circuit with 'Sem intervenção' as 2nd ticket -> KEPT for Claro (2 valid -> stabilized!)
        claro_sem = self._create_link("STAB-CLARO-SEM", designation="BD500777", client="Claro", project="Correios")
        self._create_ticket(link=claro_sem, external_id=92021, created_at=now - timedelta(days=48))
        self._create_ticket(
            link=claro_sem,
            external_id=92022,
            created_at=now - timedelta(days=35),
            closing_reason="Sem Intervenção",
        )

        # Link with OQ + active RREIP cycle: statistical stabilization must NOT auto-close OQ/RREIP
        link_with_oq = CMDBLinkMirror.objects.get(source_config_item_id="STAB-1")
        oq = QualityOrder.objects.create(link=link_with_oq, status=QualityOrder.Status.MONITORAMENTO)
        cycle = QualityMonitoringCycle.objects.create(
            quality_order=oq,
            cycle_number=1,
            started_at=now - timedelta(days=10),
            expected_end_at=now + timedelta(days=20),
            result=QualityMonitoringCycle.Result.IN_PROGRESS,
        )

        analysis = build_quality_recurrence_analysis(self.admin, now=now)
        self.assertFalse(analysis["links"]["STAB-OPEN-OLD"]["is_stabilized"])
        self.assertTrue(analysis["links"]["STAB-OPEN-OLD"]["is_active_recurrence"])
        self.assertFalse(analysis["links"]["STAB-VIVO-SEM"]["is_stabilized"])
        self.assertTrue(analysis["links"]["STAB-CLARO-SEM"]["is_stabilized"])

        # Dashboard Top 5 & link to full stabilized page
        dash = get_dashboard_data(self.admin)
        self.assertEqual(len(dash["stabilized_links"]), 5)
        self.assertEqual(dash["stabilized_total_count"], 8)  # 7 + claro_sem
        self.assertEqual(dash["kpi_urls"]["stabilized_links"], reverse("qualidade:links_estabilizados"))

        # Full stabilized view shows ALL 8 links
        full = get_stabilized_links(self.admin)
        self.assertEqual(full["summary"]["total"], 8)
        self.assertEqual(full["summary"]["rreip_active"], 1)
        self.assertEqual(full["summary"]["stat_only"], 7)

        # Ensure OQ & RREIP were NOT auto-closed
        oq.refresh_from_db()
        cycle.refresh_from_db()
        self.assertEqual(oq.status, QualityOrder.Status.MONITORAMENTO)
        self.assertEqual(cycle.result, QualityMonitoringCycle.Result.IN_PROGRESS)

        # HTTP view + filters + search + drill-down
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("qualidade:links_estabilizados"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "BD500001")
        self.assertContains(resp, "BD500777")
        self.assertNotContains(resp, "BD500999")
        self.assertNotContains(resp, "BD500888")

        filtered = self.client.get(reverse("qualidade:links_estabilizados"), {"client": "Claro", "q": "BD500777"})
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, "BD500777")
        self.assertNotContains(filtered, "BD500002")

        detail = self.client.get(
            reverse("qualidade:priority_link_detail", kwargs={"identificador": "STAB-1"}) + "?from=stabilized",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Links estabilizados")

    # --- FRENTE 2: Ordens de Qualidade R1, R2, R3... ---

    def test_quality_orders_calculate_r_levels_preserve_manual_and_handle_insufficient_history(self):
        now = timezone.now()
        # OQ 1: 3 valid occurrences -> R2 (auto-calculated) + 1 canceled (ignored)
        link_r2 = self._create_link("OQ-R2", designation="BL-R2", client="Vivo", project="Bradesco")
        self._create_ticket(link=link_r2, external_id=93001, created_at=now - timedelta(days=20))
        self._create_ticket(link=link_r2, external_id=93002, created_at=now - timedelta(days=10))
        self._create_ticket(link=link_r2, external_id=93003, created_at=now - timedelta(days=2))
        self._create_ticket(
            link=link_r2, external_id=93004, created_at=now - timedelta(days=1),
            status=OTRSStateMapping.BusinessStatus.CANCELED,
        )
        oq_r2 = QualityOrder.objects.create(link=link_r2, status=QualityOrder.Status.NOVA, priority=QualityOrder.Priority.ALTA)

        # OQ 2: manual R4 preserved even with 2 valid occurrences (R1)
        link_manual = self._create_link("OQ-MAN", designation="BL-MAN", client="Claro", project="Banco do Brasil")
        self._create_ticket(link=link_manual, external_id=93011, created_at=now - timedelta(days=15))
        self._create_ticket(link=link_manual, external_id=93012, created_at=now - timedelta(days=3))
        oq_manual = QualityOrder.objects.create(
            link=link_manual,
            status=QualityOrder.Status.TRATAMENTO,
            recurrence_level=QualityOrder.RecurrenceLevel.R4,
            priority=QualityOrder.Priority.CRITICA,
        )

        # OQ 3: only 1 valid occurrence -> 'A confirmar' (never fabricates R1)
        link_single = self._create_link("OQ-ONE", designation="BL-ONE", client="Vivo", project="Fust")
        self._create_ticket(link=link_single, external_id=93021, created_at=now - timedelta(days=4))
        oq_single = QualityOrder.objects.create(link=link_single, status=QualityOrder.Status.NOVA)

        queue = get_quality_orders_queue(self.admin, {})
        by_code = {row.code: row for row in queue["rows"]}
        self.assertEqual(by_code[oq_r2.code].effective_level, "R2")
        self.assertEqual(by_code[oq_r2.code].level_source, "CALCULATED")
        self.assertIn("3", by_code[oq_r2.code].level_tooltip)

        self.assertEqual(by_code[oq_manual.code].effective_level, "R4")
        self.assertEqual(by_code[oq_manual.code].level_source, "MANUAL")
        self.assertTrue(by_code[oq_manual.code].level_info["has_manual_divergence"])

        self.assertEqual(by_code[oq_single.code].effective_level, "")
        self.assertEqual(by_code[oq_single.code].level_short_label, "A confirmar")
        self.assertEqual(by_code[oq_single.code].level_source, "UNCONFIRMED")

        # Filter by calculated level R2 and UNCONFIRMED
        r2_only = get_quality_orders_queue(self.admin, {"level": "R2"})
        self.assertEqual([r.code for r in r2_only["rows"]], [oq_r2.code])
        unconf_only = get_quality_orders_queue(self.admin, {"level": "UNCONFIRMED"})
        self.assertEqual([r.code for r in unconf_only["rows"]], [oq_single.code])

        # Verify statuses/priorities were NEVER auto-modified
        oq_r2.refresh_from_db()
        oq_manual.refresh_from_db()
        self.assertEqual(oq_r2.status, QualityOrder.Status.NOVA)
        self.assertEqual(oq_manual.status, QualityOrder.Status.TRATAMENTO)
        self.assertEqual(oq_manual.recurrence_level, QualityOrder.RecurrenceLevel.R4)

        # Detail view includes progression Inicial -> R1 -> R2
        detail = get_quality_order_detail(self.admin, oq_r2)
        self.assertEqual(detail["level_info"]["code"], "R2")
        self.assertEqual(
            [step["step_label"] for step in detail["level_info"]["progression"]],
            ["Inicial", "R1", "R2"],
        )

        self.client.force_login(self.admin)
        list_resp = self.client.get(reverse("qualidade:ordens"))
        self.assertEqual(list_resp.status_code, 200)
        self.assertContains(list_resp, "R2")
        self.assertContains(list_resp, "A confirmar")
        self.assertNotContains(list_resp, "A classificar")

        detail_resp = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": oq_r2.code}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, "PROGRESSÃO DO EPISÓDIO")

    # --- FRENTE 3: Contatos e Escalonamentos ---

    def test_contacts_rbac_visibility_and_full_crud_flow(self):
        # Permission is managed in central access control and granted to Admin + Manager
        self.assertIn("manage_quality_contacts", PERMISSION_LABELS)
        self.assertTrue(managed_permissions().filter(codename="manage_quality_contacts").exists())
        self.assertTrue(self.admin.has_perm("qualidade.manage_quality_contacts"))
        self.assertTrue(self.manager.has_perm("qualidade.manage_quality_contacts"))
        self.assertFalse(self.analyst.has_perm("qualidade.manage_quality_contacts"))

        # Authorized manager sees '+ Novo contato' even when list is empty
        self.client.force_login(self.manager)
        empty_resp = self.client.get(reverse("qualidade:contatos"))
        self.assertEqual(empty_resp.status_code, 200)
        self.assertContains(empty_resp, "+ Novo contato")

        # Create contact with new operational fields
        create_resp = self.client.post(
            reverse("qualidade:contato_salvar"),
            {
                "name": "Maria Escalation",
                "role": "Coordenadora NOC",
                "contact_category": QualityContact.ContactCategory.OPERADORA,
                "company": "Vivo",
                "client": "Vivo",
                "project": "Bradesco",
                "region": "SP",
                "contact_type": "NOC",
                "escalation_level": "2",
                "email": "maria@vivo.example",
                "phone": "(11) 3000-1000",
                "whatsapp": "(11) 99999-1000",
                "teams": "maria.vivo",
                "service_hours": "24x7",
                "notes": "Escalonamento prioritário Bradesco",
                "active": "on",
                "next": reverse("qualidade:contatos"),
            },
        )
        self.assertEqual(create_resp.status_code, 302)
        contact = QualityContact.objects.get(name="Maria Escalation")
        self.assertEqual(contact.contact_category, QualityContact.ContactCategory.OPERADORA)
        self.assertEqual(contact.whatsapp, "(11) 99999-1000")
        self.assertEqual(contact.service_hours, "24x7")
        self.assertTrue(AuditEvent.objects.filter(action="QUALITY_CONTACT_CREATED", object_id=str(contact.pk)).exists())

        # Quick-copy buttons and fields rendered in list
        list_resp = self.client.get(reverse("qualidade:contatos"))
        self.assertContains(list_resp, "Maria Escalation")
        self.assertContains(list_resp, 'data-quality-copy="maria@vivo.example"')
        self.assertContains(list_resp, 'data-quality-copy="(11) 3000-1000"')
        self.assertContains(list_resp, 'data-quality-copy="(11) 99999-1000"')

        # Edit contact
        edit_resp = self.client.post(
            reverse("qualidade:contato_salvar"),
            {
                "contact_id": str(contact.pk),
                "name": "Maria Escalation Atualizada",
                "role": "Gerente NOC",
                "contact_category": QualityContact.ContactCategory.OPERADORA,
                "company": "Vivo",
                "client": "Vivo",
                "project": "Bradesco",
                "region": "SP",
                "contact_type": "GERENCIA",
                "escalation_level": "3",
                "email": "maria@vivo.example",
                "phone": "(11) 3000-1000",
                "whatsapp": "(11) 99999-1000",
                "service_hours": "Seg-Sex 08h-20h",
                "active": "on",
                "next": reverse("qualidade:contatos"),
            },
        )
        self.assertEqual(edit_resp.status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.name, "Maria Escalation Atualizada")
        self.assertEqual(contact.escalation_level, 3)

        # Deactivate & reactivate (never hard-deletes)
        deact = self.client.post(
            reverse("qualidade:contato_status", kwargs={"contact_id": contact.pk}),
            {"active": "false", "next": reverse("qualidade:contatos")},
        )
        self.assertEqual(deact.status_code, 302)
        contact.refresh_from_db()
        self.assertFalse(contact.active)

        # Analyst cannot see '+ Novo contato' and receives 403 on write endpoints
        self.client.force_login(self.analyst)
        analyst_get = self.client.get(reverse("qualidade:contatos"))
        self.assertEqual(analyst_get.status_code, 200)
        self.assertNotContains(analyst_get, "+ Novo contato")
        analyst_post = self.client.post(reverse("qualidade:contato_salvar"), {"name": "Bloqueado", "escalation_level": "1"})
        self.assertEqual(analyst_post.status_code, 403)
        analyst_toggle = self.client.post(
            reverse("qualidade:contato_status", kwargs={"contact_id": contact.pk}),
            {"active": "true"},
        )
        self.assertEqual(analyst_toggle.status_code, 403)
