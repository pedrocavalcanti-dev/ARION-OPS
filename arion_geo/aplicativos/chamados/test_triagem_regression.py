"""Focused production-shaped investigation of the shared Triagem ticket scope."""
from datetime import timedelta
import json
from pathlib import Path
import re
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME, TRIAGE_QUEUE_NAME

from .models import TicketQueueView, TicketTracking
from .services import TicketQueueService


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class TriageQueryRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.global_reader = users.objects.create_user("scope-global-reader")
        cls.scoped_reader = users.objects.create_user("scope-custom-reader")
        cls.no_permission = users.objects.create_user("scope-no-permission")
        permission = Permission.objects.get(content_type__app_label="configuracoes", codename="view_operational_tickets")
        client = Operadora.objects.create(name="Cliente auditoria", normalized_name="cliente auditoria")
        project = Projeto.objects.create(name="Projeto auditoria", normalized_name="projeto auditoria")
        other_project = Projeto.objects.create(name="Outro projeto auditoria", normalized_name="outro projeto auditoria")
        global_group = Group.objects.create(name="auditoria chamados global")
        global_group.permissions.add(permission)
        GroupDataScope.objects.create(group=global_group, mode=GroupDataScope.Mode.ALL)
        cls.global_reader.groups.add(global_group)
        # Multiple groups must retain the operator/project intersection.
        cls.scoped_reader.groups.add(global_group)
        for index in range(2):
            group = Group.objects.create(name=f"auditoria chamados custom {index}")
            scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
            scope.allowed_operators.add(client)
            scope.allowed_projects.add(project)
            if index == 0:
                scope.allowed_projects.add(other_project)
            cls.scoped_reader.groups.add(group)
        provider = CMDBOperatorMirror.objects.create(source_config_item_id="audit-provider", name="ISP auditoria")
        cls.link = CMDBLinkMirror.objects.create(source_config_item_id="audit-link", designation="BL-AUDIT",
            contracting_client=client.name, business_project=project.name, operator=provider, state="SP")
        cls.second_link = CMDBLinkMirror.objects.create(source_config_item_id="audit-link-2", designation="BL-AUDIT-2",
            contracting_client=client.name, business_project=project.name, operator=None)
        denied = CMDBLinkMirror.objects.create(source_config_item_id="audit-denied", designation="BL-DENIED",
            contracting_client=client.name, business_project=other_project.name, operator=provider)
        now = timezone.now()
        for identifier, queue, status, present, link, created in (
            (8001, NOC_QUEUE_NAME, "OPEN", True, cls.link, now - timedelta(hours=26)),
            (8002, TRIAGE_QUEUE_NAME, "PENDING", True, cls.link, None),
            (8003, TRIAGE_QUEUE_NAME, "SCHEDULED", True, cls.second_link, now),
            (8004, TRIAGE_QUEUE_NAME, "RESOLVED", True, cls.link, now),
            (8005, "NOC - CANCELAMENTOS NGO", "OPEN", True, cls.link, now),
            (8006, NOC_QUEUE_NAME, "OPEN", False, cls.link, now),
            (8007, TRIAGE_QUEUE_NAME, "OPEN", True, denied, now),
            (8008, TRIAGE_QUEUE_NAME, "OPEN", True, None, now),
        ):
            ticket = OTRSTicket.objects.create(external_id=identifier, ticket_number=f"AUDIT-{identifier}",
                queue_external_id=181 if queue == TRIAGE_QUEUE_NAME else 107 if queue == NOC_QUEUE_NAME else 164,
                queue_name=queue, business_status=status, external_state_id=10, external_state_name=status,
                is_present_in_queue=present, has_support_history=True, source_created_at=created)
            if link:
                OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id=link.source_config_item_id,
                    cmdb_link=link, relation_type="DependsOn", relation_state="Valid")
            if identifier == 8002:
                OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id=cls.second_link.source_config_item_id,
                    cmdb_link=cls.second_link, relation_type="DependsOn", relation_state="Valid")
        TicketTracking.objects.create(ticket_external_id=8001, assigned_to=cls.global_reader)
        TicketTracking.objects.create(ticket_external_id=8002, assigned_to=cls.scoped_reader, internal_status="ISP_IN_PROGRESS")

    def test_multi_scope_two_queues_null_dates_and_history_keep_counts_consistent(self):
        data = TicketQueueService.build(self.scoped_reader, {})
        self.assertEqual({row["external_id"] for row in data["rows"]}, {8001, 8002, 8003})
        self.assertEqual(data["active_count"], 3)
        self.assertEqual(data["status_counts"], {"OPEN": 1, "PENDING": 1, "SCHEDULED": 1})
        self.assertEqual(data["mine_count"], 1)
        self.assertEqual(data["unassigned_count"], 1)
        pending = next(row for row in data["rows"] if row["external_id"] == 8002)
        self.assertEqual(pending["age_label"], "Sem data de criação")
        self.assertEqual(pending["links_count"], 2)
        self.assertEqual(data["aged_count"], 1)

    def test_mysql_queries_and_rendering_for_non_superusers_with_filters(self):
        for user in (self.global_reader, self.scoped_reader):
            self.client.force_login(user)
            for filters in ({}, {"sort": "provider"}, {"sort": "project"}, {"sort": "uf"},
                            {"arion_status": "OPEN"}, {"assignee": str(user.pk)},
                            {"q": "BL-AUDIT"}, {"recurrent": "1"}, {"tab": "PENDING"}):
                with self.subTest(user=user.pk, filters=filters):
                    response = self.client.get(reverse("chamados:index"), filters)
                    self.assertEqual(response.status_code, 200)
                    identifiers = [row["external_id"] for row in response.context["rows"]]
                    self.assertEqual(len(identifiers), len(set(identifiers)))
                    if user == self.scoped_reader:
                        self.assertTrue(set(identifiers) <= {8001, 8002, 8003})

    def test_saved_filter_from_triage_release_still_renders_without_level_division(self):
        TicketQueueView.objects.create(user=self.scoped_reader, name="Legado", is_default=True,
            filters={"queue": TRIAGE_QUEUE_NAME, "sort": "provider", "page_size": 25})
        self.client.force_login(self.scoped_reader)
        response = self.client.get(reverse("chamados:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["external_id"] for row in response.context["rows"]}, {8001, 8002, 8003})
        self.assertNotContains(response, 'name="queue"')

    def test_permission_is_not_implied_by_having_tickets_or_a_scope(self):
        self.client.force_login(self.no_permission)
        self.assertEqual(self.client.get(reverse("chamados:index")).status_code, 403)

    def test_production_bundle_context_uses_existing_chamados_entry(self):
        from config.templatetags.arion_assets import _production_manifest
        self.client.force_login(self.scoped_reader)
        baseline = self.client.get(reverse("chamados:index")).content.decode()
        paths = set(re.findall(r'["\']/static/([^"\']+)', baseline))
        paths.update({"css/errors.css", "js/errors.js", "branding/arion/arion-logo-light.png",
                      "branding/arion/arion-logo-primary.png"})
        sources = {path for path in paths if path.startswith("js/") and path.endswith(".js")}
        frontend = {source: f"arion_build/{source[3:-3]}.test.js" for source in sources}
        storages = dict(settings.STORAGES)
        storages["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"}
        with tempfile.TemporaryDirectory(prefix="arion-chamados-assets-") as directory:
            root = Path(directory)
            manifest = root / "frontend.json"
            manifest.write_text(json.dumps(frontend), encoding="utf-8")
            collected = {path: path for path in paths}
            collected.update({path: path for path in frontend.values()})
            (root / "staticfiles.json").write_text(json.dumps({"paths": collected, "version": "1.1"}), encoding="utf-8")
            _production_manifest.cache_clear()
            try:
                with override_settings(ARION_PRODUCTION=True, STORAGES=storages,
                        STATIC_ROOT=root, ARION_FRONTEND_MANIFEST_PATH=manifest):
                    response = self.client.get(reverse("chamados:index"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "arion_build/")
                # A stale collectstatic manifest fails while rendering HTML,
                # before any JavaScript can run, even with valid ORM results.
                collected.pop(frontend["js/chamados/chamados.js"])
                (root / "staticfiles.json").write_text(json.dumps({"paths": collected, "version": "1.1"}), encoding="utf-8")
                with override_settings(ARION_PRODUCTION=True, STORAGES=storages,
                        STATIC_ROOT=root, ARION_FRONTEND_MANIFEST_PATH=manifest):
                    with self.assertLogs("config.errors", level="ERROR") as logged:
                        with self.assertRaisesMessage(ValueError, "chamados/chamados.test.js"):
                            self.client.get(reverse("chamados:index"))
                    self.assertIn("exception_type=ValueError", " ".join(logged.output))
            finally:
                _production_manifest.cache_clear()
