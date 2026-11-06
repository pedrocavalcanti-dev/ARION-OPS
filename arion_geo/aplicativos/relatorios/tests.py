from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.access_control import GROUP_ANALYST, GROUP_MANAGER, GROUP_SYSTEM_ADMIN

from .models import PenaltyCompetence
from .validators import MAX_REPORT_FILE_SIZE, report_file_extension_validator, validate_report_file_size


class ReportsAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="reports-manager", password="secure-test-password")
        cls.manager.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        cls.analyst = User.objects.create_user(username="reports-analyst", password="secure-test-password")
        cls.analyst.groups.set([Group.objects.get(name=GROUP_ANALYST)])
        cls.system_admin = User.objects.create_user(username="reports-admin", password="secure-test-password")
        cls.system_admin.groups.set([Group.objects.get(name=GROUP_SYSTEM_ADMIN)])
        cls.superuser = User.objects.create_superuser(username="reports-root", email="", password="secure-test-password")

    def test_only_authorized_profiles_can_open_reports_module(self):
        dashboard_url = reverse("relatorios:dashboard")
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(dashboard_url).status_code, 200)

        self.client.force_login(self.analyst)
        self.assertEqual(self.client.get(dashboard_url).status_code, 403)
        self.assertEqual(self.client.get(reverse("relatorios:claro-penalidades")).status_code, 403)

    def test_system_admin_and_superuser_can_access_reports(self):
        for user in (self.system_admin, self.superuser):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(reverse("relatorios:dashboard")).status_code, 200)

    def test_reports_uses_specialized_topbar_without_global_operational_navigation(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("relatorios:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Voltar para Visão Geral ARION")
        self.assertContains(response, 'class="user-menu"')
        self.assertContains(response, 'data-theme-option="dark"')
        self.assertContains(response, 'class="reports-mobile-nav"')
        self.assertNotContains(response, 'class="header-nav"')
        self.assertNotContains(response, 'id="mobile-navigation"')

    def test_global_header_still_renders_on_primary_authenticated_pages(self):
        self.client.force_login(self.superuser)
        urls = (
            reverse("painel:index"), reverse("dashboard"), reverse("cockpit"),
            reverse("chamados:index"), reverse("mapa:index"), reverse("rankings:index"),
            reverse("base_links:index"), reverse("configuracoes:index"), reverse("ari:index"),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="header-nav"')

    def test_group_access_form_offers_the_module_permission(self):
        self.client.force_login(self.system_admin)
        response = self.client.get(reverse("configuracoes:group-create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visualizar relatórios")
        self.assertTrue(self.manager.has_perm("relatorios.view_reports"))
        self.assertFalse(self.analyst.has_perm("relatorios.view_reports"))

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("relatorios:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("autenticacao:login"), response.url)


class CompetenceModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reports-owner", password="secure-test-password")

    def create_competence(self, **overrides):
        values = {
            "report_type": PenaltyCompetence.ReportType.CLARO_PENALTIES,
            "competence_year": 2026,
            "competence_month": 10,
            "created_by": self.user,
            "updated_by": self.user,
        }
        values.update(overrides)
        return PenaltyCompetence.objects.create(**values)

    def test_competence_period_is_unique_for_report_type(self):
        self.create_competence()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_competence()

    def test_competence_month_has_database_range_constraint(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_competence(competence_month=13)

    def test_excel_extension_and_size_validators(self):
        report_file_extension_validator(SimpleUploadedFile("fatura.xlsx", b"ok"))
        with self.assertRaises(ValidationError):
            report_file_extension_validator(SimpleUploadedFile("fatura.csv", b"ok"))
        validate_report_file_size(SimpleUploadedFile("fatura.xls", b"ok"))
        large_file = SimpleUploadedFile("fatura.xlsx", b"x" * (MAX_REPORT_FILE_SIZE + 1))
        with self.assertRaises(ValidationError):
            validate_report_file_size(large_file)


class ReportsWorkflowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="reports-workflow", password="secure-test-password")
        self.user.groups.set([Group.objects.get(name=GROUP_MANAGER)])
        self.client.force_login(self.user)

    def test_dashboard_and_claro_list_show_truthful_empty_states(self):
        dashboard = self.client.get(reverse("relatorios:dashboard"))
        self.assertContains(dashboard, "Disponíveis quando houver dados reais")
        self.assertContains(dashboard, "Nenhuma competência criada")
        listing = self.client.get(reverse("relatorios:claro-penalidades"))
        self.assertContains(listing, "Nenhuma competência cadastrada")
        self.assertNotContains(listing, "R$ 0,00")

    def test_wizard_displays_real_upload_and_review_steps(self):
        response = self.client.get(reverse("relatorios:competence-create"))
        self.assertContains(response, "Competência e arquivo")
        self.assertContains(response, "Conferência")
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'type="file" name="original_file"')
        self.assertContains(response, "O arquivo será validado")

    def test_post_creates_empty_draft_without_upload_and_records_audit(self):
        response = self.client.post(reverse("relatorios:competence-create"), {
            "competence_month": 9,
            "competence_year": 2026,
        })
        competence = PenaltyCompetence.objects.get(competence_month=9, competence_year=2026)
        self.assertRedirects(response, reverse("relatorios:workspace", args=(competence.pk,)))
        self.assertEqual(competence.status, PenaltyCompetence.Status.DRAFT)
        self.assertFalse(competence.original_file)
        self.assertFalse(competence.original_filename)
        event = AuditEvent.objects.get(module="relatorios", object_id=str(competence.pk))
        self.assertEqual(event.action, "COMPETENCE_CREATED")

    def test_duplicate_period_returns_form_error_without_duplicate_record(self):
        PenaltyCompetence.objects.create(competence_year=2026, competence_month=8, created_by=self.user)
        response = self.client.post(reverse("relatorios:competence-create"), {
            "competence_month": 8,
            "competence_year": 2026,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe uma competência")
        self.assertEqual(PenaltyCompetence.objects.filter(competence_year=2026, competence_month=8).count(), 1)

    def test_workspace_tabs_render_without_fake_records(self):
        competence = PenaltyCompetence.objects.create(competence_year=2026, competence_month=7, created_by=self.user)
        for tab, phrase in (
            ("original", "Nenhum arquivo Original"),
            ("tratativa", "Tratativa ainda não criada"),
            ("resumo", "Totais financeiros da base persistida"),
            ("historico", "Sem eventos registrados"),
        ):
            with self.subTest(tab=tab):
                response = self.client.get(reverse("relatorios:workspace", args=(competence.pk,)), {"aba": tab})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, phrase)

    def test_invalid_workspace_tab_falls_back_to_original(self):
        competence = PenaltyCompetence.objects.create(competence_year=2026, competence_month=6, created_by=self.user)
        response = self.client.get(reverse("relatorios:workspace", args=(competence.pk,)), {"aba": "unknown"})
        self.assertContains(response, "Nenhum arquivo Original")
