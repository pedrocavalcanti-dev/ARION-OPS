from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test.utils import CaptureQueriesContext
from django.db import connection
from openpyxl import Workbook
from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.models import GroupDataScope

from .classification import detect_operator, detect_project
from .models import CMDBImport, CMDBLink, CMDBLinkChange, Operadora, Projeto, Provider
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem
from .services.cmdb.excel_provider import ExcelCMDBProvider, FIELD_MAP, SENSITIVE_HEADERS, normalize_provider
from .services.cmdb.sync import inspect_items, sync_items


class SpreadsheetExportSafetyTests(SimpleTestCase):
    def test_base_csv_values_neutralize_formula_prefixes_after_whitespace(self):
        from .views import _safe_csv_value

        self.assertEqual(_safe_csv_value("=1+1"), "'=1+1")
        self.assertEqual(_safe_csv_value("  @SUM(1,1)"), "'  @SUM(1,1)")
        self.assertEqual(_safe_csv_value("\t-1+1"), "'\t-1+1")
        self.assertEqual(_safe_csv_value("Operadora segura"), "Operadora segura")

    def test_cockpit_spreadsheet_values_neutralize_formula_prefixes_after_whitespace(self):
        from aplicativos.painel.views import _safe_spreadsheet_text

        self.assertEqual(_safe_spreadsheet_text("  =1+1"), "'  =1+1")
        self.assertEqual(_safe_spreadsheet_text("\t@SUM(1,1)"), "'\t@SUM(1,1)")
        self.assertEqual(_safe_spreadsheet_text("Operadora segura"), "Operadora segura")


def workbook_bytes(rows=None, headers=None):
    workbook = Workbook()
    sheet = workbook.active
    headers = headers or [*FIELD_MAP, *SENSITIVE_HEADERS]
    sheet.append(headers)
    rows = rows or [default_row()]
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    content = BytesIO()
    workbook.save(content)
    return content.getvalue()


def default_row(**changes):
    row = {
        "Classe": "Links de internet", "Estado de Incidente": "Operational",
        "Nome": "VIVO - BRADESCO", "ConfigItem#": "36131003500",
        "Estado de Implantação": "Operacional", "Versão": "31098",
        "Hora de Criação": "2026-10-22 08:51:57", "Designação ARION": "VIVO.SP.BLIVR000001",
        "CNPJ ARION": "N/A", "Endereço": "Rua Um, 10", "Cidade": "São Paulo",
        "Estado": "SP", "Cliente": "37", "Circuito": "CIR-01",
        "ISP": "MINAS TELECOM (36132001342)", "Velocidade": "100 MB",
        "Range IP": "192.168.1.1", "Tipo": "BANDA LARGA", "Número de Contrato": "C-01",
        "Designação da Operadora": "N/A", "Identificação (BRAD, SIGLA, AG)": "N/A",
        "Horário de Funcionamento": "Comercial", "Usuário PPOE": "usuario-secreto",
        "Senha PPOE": "senha-secreta", "Observação (Particularidade da ISP)": "Observação original",
    }
    row.update(changes)
    return row


class ClassificationTests(TestCase):
    def test_explicit_operator_and_project_are_detected(self):
        self.assertEqual(detect_operator({"name": "FUST UBS - CLARO.PR.BLIVR001045"}), "CLARO")
        self.assertEqual(detect_project({"name": "REMANEJAMENTO DE CIRCUITO - CAIXA ECONOMICA"}), "Caixa Econômica")
        self.assertEqual(detect_operator({"name": "VIVO - ARCOS DOURADOS"}), "VIVO")
        self.assertEqual(detect_project({"name": "VIVO - ARCOS DOURADOS"}), "Arcos Dourados")

    def test_unknown_or_conflicting_classification_is_not_guessed(self):
        self.assertIsNone(detect_operator({"name": "Circuito metropolitano sem operadora"}))
        self.assertIsNone(detect_project({"name": "Projeto sem nome explícito"}))
        self.assertIsNone(detect_operator({"name": "CLARO / VIVO - BRADESCO"}))

    def test_provider_normalization_keeps_business_variants_separate(self):
        name, normalized, config_id = normalize_provider("MINAS TELECOM (36132001342)")
        self.assertEqual((name, normalized, config_id), ("MINAS TELECOM", "minas telecom", "36132001342"))
        self.assertNotEqual(normalize_provider("VERO / AMERICANET")[1], normalize_provider("VERO")[1])
        self.assertEqual(normalize_provider("Tower Connection(WKE)")[0], "Tower Connection(WKE)")


class CMDBImportServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="cmdb-importer", password="Senha-segura-123")

    def parsed(self, rows=None, headers=None):
        provider = ExcelCMDBProvider(workbook_bytes(rows, headers), "fixture.xlsx")
        provider.load_items()
        self.assertTrue(provider.validate())
        items = provider.normalize()
        return provider, items

    def sync(self, provider, items, errors=None):
        return sync_items(items=items, filename=provider.filename, sha256=provider.sha256, imported_by=self.user, errors=errors or [])

    def test_valid_import_preserves_original_fields_but_drops_ppoe_credentials(self):
        provider, items = self.parsed()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["config_item_id"], "36131003500")
        self.assertEqual(items[0]["provider_name"], "MINAS TELECOM")
        self.assertEqual(items[0]["provider_cmdb_config_item_id"], "36132001342")
        self.assertEqual(items[0]["client_raw"], "37")
        self.assertEqual(items[0]["operadora_name"], "VIVO")
        self.assertEqual(items[0]["projeto_name"], "Bradesco")
        self.assertEqual(items[0]["source_created_at"].year, 2026)
        self.assertNotIn("Usuário PPOE", items[0]["source_payload"])
        self.assertNotIn("Senha PPOE", items[0]["source_payload"])
        preview = inspect_items(items)
        self.assertEqual(preview["new"][0]["config_item_id"], "36131003500")
        self.assertEqual(Provider.objects.count(), 0)
        batch = self.sync(provider, items)
        self.assertEqual(batch.new_count, 1)
        link = CMDBLink.objects.get(config_item_id="36131003500")
        self.assertNotIn("senha-secreta", str(link.source_payload))
        self.assertNotIn("usuario-secreto", str(link.source_payload))

    def test_config_item_is_unique_and_repeated_designations_are_allowed(self):
        second = default_row(**{"ConfigItem#": "36131003501"})
        provider, items = self.parsed([default_row(), second])
        self.sync(provider, items)
        self.assertEqual(CMDBLink.objects.count(), 2)
        self.assertEqual(CMDBLink.objects.values("designation_arion").distinct().count(), 1)
        with self.assertRaises(IntegrityError):
            CMDBLink.objects.create(config_item_id="36131003500")

    def test_reimport_is_unchanged_and_update_records_field_history(self):
        provider, items = self.parsed()
        self.sync(provider, items)
        same_provider, same_items = self.parsed()
        self.assertEqual(inspect_items(same_items)["updated"], [])
        second_batch = self.sync(same_provider, same_items)
        self.assertEqual(second_batch.unchanged_count, 1)
        self.assertEqual(CMDBLink.objects.count(), 1)

        changed_provider, changed_items = self.parsed([default_row(**{"Velocidade": "200 MB", "Versão": "31099"})])
        changed_batch = self.sync(changed_provider, changed_items)
        self.assertEqual(changed_batch.updated_count, 1)
        self.assertEqual(CMDBLink.objects.get().speed_raw, "200 MB")
        self.assertTrue(CMDBLinkChange.objects.filter(field_name="speed_raw", old_value="100 MB", new_value="200 MB").exists())

    def test_missing_config_item_header_blocks_file(self):
        headers = [header for header in FIELD_MAP if header != "ConfigItem#"]
        provider = ExcelCMDBProvider(workbook_bytes(headers=headers), "invalid.xlsx")
        provider.load_items()
        self.assertFalse(provider.validate())
        self.assertTrue(any("ConfigItem#" in error["message"] for error in provider.errors))
        self.assertEqual(provider.normalize(), [])

    def test_duplicate_config_item_in_one_file_is_reported_and_not_repeated(self):
        provider, items = self.parsed([default_row(), default_row(**{"Designação ARION": "OUTRO NOME"})])
        self.assertEqual(len(items), 1)
        self.assertEqual(len(provider.errors), 1)

    def test_non_link_classes_are_not_imported(self):
        provider, items = self.parsed([default_row(**{"Classe": "Computer"})])
        self.assertEqual(items, [])
        self.assertIn("Classe não importada", provider.errors[0]["message"])

    def test_invalid_state_is_kept_raw_and_full_state_name_is_normalized(self):
        _provider, items = self.parsed([default_row(**{"Estado": "São Paulo"})])
        self.assertEqual(items[0]["uf"], "SP")
        _provider, items = self.parsed([default_row(**{"Estado": "VCP"})])
        self.assertEqual(items[0]["uf"], "")
        self.assertEqual(items[0]["uf_raw"], "VCP")


class CMDBViewsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="base-user", password="safe-password-123")
        group = Group.objects.get(name="Analista NOC")
        self.user.groups.set([group])
        scope, _ = GroupDataScope.objects.get_or_create(group=group)
        scope.mode = GroupDataScope.Mode.ALL
        scope.save()
        self.operator = CMDBOperatorMirror.objects.create(source_config_item_id="base-op", name="Operadora CMDB")
        self.link = CMDBLinkMirror.objects.create(source_config_item_id="base-ci", source_number="BASE-1", name="Link Base", designation="LINK-001", state="SP", city="São Paulo", operator=self.operator, legacy_project="Projeto congelado", contracting_client_raw="CLARO", contracting_client="Claro", business_project="Projeto congelado", business_identity_confidence="HIGH", isp_raw="6804", deployment_state="Operacional")

    def test_base_and_import_require_login(self):
        response = self.client.get(reverse("base_links:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
        self.assertEqual(self.client.get(reverse("base_links:map-aggregates")).status_code, 401)

    def test_import_is_disabled_and_legacy_route_not_available_to_regular_users(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("base_links:import")).status_code, 403)
        self.assertEqual(self.client.post(reverse("base_links:confirm")).status_code, 403)
        admin = get_user_model().objects.create_superuser(username="base-admin", email="", password="safe-password-123")
        self.client.force_login(admin)
        page = self.client.get(reverse("base_links:import"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Importacao XLSX desativada")
        self.assertNotContains(page, "type=" + chr(34) + "file")
        self.assertEqual(self.client.post(reverse("base_links:confirm")).status_code, 410)

    def test_base_filters_and_kpis_use_present_mirror_rows(self):
        CMDBLinkMirror.objects.create(source_config_item_id="base-ci-2", name="Other", state="RJ", operator=self.operator, is_present_in_source=False)
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {"q": "LINK-001", "uf": "SP", "projeto": "Projeto congelado"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_links"], 1)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "Operadora CMDB")
        self.assertContains(response, "Projeto congelado")
        self.assertNotContains(response, "6804")
        self.assertNotContains(response, "Other")

    def test_config_item_navigation_ignores_stale_dimensions_but_still_enforces_data_scope(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {
            "config_item_id": "base-ci", "q": "not-the-link", "cliente": "Vivo",
            "projeto": "Outro projeto", "uf": "BA", "provider_id": "999999",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["links"][0].source_config_item_id, "base-ci")

        outside_operator = CMDBOperatorMirror.objects.create(source_config_item_id="base-op-out", name="Outside")
        CMDBLinkMirror.objects.create(source_config_item_id="outside-ci", name="Outside link", operator=outside_operator)
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name="Claro", normalized_name="claro")])
        denied = self.client.get(reverse("base_links:index"), {
            "config_item_id": "outside-ci", "cliente": "Vivo", "uf": "BA",
        })
        self.assertEqual(denied.context["page_obj"].paginator.count, 0)

    def test_map_base_link_filters_accept_other_client_and_provider_id(self):
        CMDBLinkMirror.objects.create(
            source_config_item_id="base-ci-telmex", name="TELMEX - BRADESCO - BD123",
            state="SP", operator=self.operator, contracting_client="TELMEX",
            business_project="BRADESCO",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {
            "uf": "SP", "cliente": "other", "projeto": "BRADESCO", "provider_id": self.operator.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["links"][0].contracting_client, "TELMEX")

    def test_search_normalizes_accents_spacing_and_case(self):
        self.client.force_login(self.user)
        self.link.address = "AVENIDA ERNESTO GEISEL, 370 - CENTRO - SÃO FÉLIX DO CORIBE - BA"
        self.link.city = "São Félix do Coribe"
        self.link.circuit = "CIRCUITO-ABC-123"
        self.link.save()
        for query in ("sao felix", "SAO   FELIX", "ernesto geisel", "370", "centro", "av ernesto", "base-ci", "BASE-1", "ABC-123"):
            response = self.client.get(reverse("base_links:index"), {"q": query})
            self.assertEqual(response.status_code, 200, query)
            self.assertEqual(response.context["page_obj"].paginator.count, 1, query)

    def test_base_semantics_provider_project_and_contracted_operator(self):
        self.operator.name = "GIGA+"
        self.operator.source_number = "PROVIDER-NUMBER-9"
        self.operator.save()
        unresolved = CMDBLinkMirror.objects.create(source_config_item_id="base-ci-unresolved", name="Link sem provedor", designation="", source_number="LINK-NO-DESIGNATION", legacy_project="", isp_raw="6746")
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"))
        self.assertContains(response, "Cliente")
        self.assertContains(response, "N\u00e3o classificado")
        self.assertContains(response, "GIGA+")
        self.assertContains(response, "Projeto congelado")
        self.assertNotContains(response, 'name="operadora"')
        self.assertEqual(response.context["provider_count"], 1)
        detail = self.client.get(reverse("base_links:detail", args=[self.link.pk])).json()
        self.assertEqual(detail["provider"], "GIGA+")
        self.assertEqual(detail["provider_cmdb_config_item_id"], "base-op")
        self.assertEqual(detail["provider_cmdb_number"], "PROVIDER-NUMBER-9")
        self.assertEqual(detail["provider_resolution_method"], CMDBLinkMirror.OperatorResolution.EMPTY)
        self.assertEqual(detail["config_item_number"], "BASE-1")
        self.assertEqual(detail["project_legacy"], "Projeto congelado")
        self.assertEqual(detail["operadora"], "Claro")
        self.assertNotIn("password", detail)
        self.assertEqual(unresolved.operator_id, None)
        catalog = self.client.get(reverse("base_links:providers"))
        self.assertEqual(catalog.status_code, 200)
        self.assertContains(catalog, "GIGA+")
        self.assertContains(catalog, "Provedores / ISP")
        provider_detail = self.client.get(reverse("base_links:provider-detail", args=[self.operator.pk])).json()
        self.assertEqual(provider_detail["number"], "PROVIDER-NUMBER-9")
        self.assertEqual(provider_detail["phone"], "")

    def test_provider_view_and_detail_are_scoped_and_secret_safe(self):
        self.operator.phone = "+55 11 3333-0000"
        self.operator.whatsapp = "+55 11 99999-0000"
        self.operator.business_hours = "24 horas"
        self.operator.opening_email = "noc@example.com"
        self.operator.portal_url = "https://portal.example.com"
        self.operator.save()
        CMDBLinkMirror.objects.create(source_config_item_id="base-ci-extra", name="Link adicional", operator=self.operator, state="RJ", business_project="Outro projeto")
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {"view": "providers", "provider_id": self.operator.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["provider_count"], 1)
        self.assertContains(response, "Telefone")
        detail = self.client.get(reverse("base_links:provider-detail", args=[self.operator.pk]))
        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["link_count"], 2)
        self.assertEqual(len(payload["links"]), 2)
        self.assertEqual(payload["phone"], "+55 11 3333-0000")
        self.assertEqual({row["config_item_id"] for row in payload["links"]}, {"base-ci", "base-ci-extra"})
        self.assertFalse({"portal_username", "password", "senha", "secret", "token"} & set(payload))
        self.assertNotIn("UsuarioPortalOperadora", str(payload))

        outside = CMDBOperatorMirror.objects.create(source_config_item_id="base-op-out", name="Outside")
        CMDBLinkMirror.objects.create(source_config_item_id="outside-ci", name="Outside link", operator=outside, business_project="Outside")
        scope = self.user.groups.get(name="Analista NOC").data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name="Claro", normalized_name="claro")])
        scope.allowed_projects.set([Projeto.objects.create(name="Projeto congelado", normalized_name="projeto congelado")])
        self.assertEqual(self.client.get(reverse("base_links:provider-detail", args=[outside.pk])).status_code, 404)

    def test_page_size_300_and_link_history_are_lazy_deduplicated_and_scoped(self):
        from django.utils import timezone
        from datetime import timedelta
        ticket = OTRSTicket.objects.create(external_id=99001, ticket_number="202610240000001", queue_external_id=107,
            queue_name="NOC - ATACADO", external_state_id=1, external_state_name="open", business_status="OPEN",
            is_business_open=True, is_present_in_queue=True, source_created_at=timezone.now() - timedelta(hours=30))
        for relation_type in ("DependsOn", "ConnectedTo"):
            OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id="base-ci", cmdb_link=self.link,
                relation_type=relation_type, relation_state="Valid")
        for index in range(301):
            CMDBLinkMirror.objects.create(source_config_item_id=f"page-ci-{index:03d}", name=f"Page link {index:03d}", operator=self.operator)
        self.client.force_login(self.user)
        page = self.client.get(reverse("base_links:index"), {"page_size": 300})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["page_size"], 300)
        self.assertEqual(len(page.context["links"]), 300)
        self.assertContains(page, 'value="300" selected')
        detail = self.client.get(reverse("base_links:detail", args=[self.link.pk])).json()
        self.assertEqual(detail["history"]["tickets_period"], 1)
        self.assertEqual(len(detail["history"]["tickets"]), 1)
        self.assertEqual(detail["history"]["backlog"], 1)
        self.assertEqual(detail["history"]["over_24h"], 1)
        empty = CMDBLinkMirror.objects.get(source_config_item_id="page-ci-000")
        self.assertEqual(self.client.get(reverse("base_links:detail", args=[empty.pk])).json()["history"]["total"], 0)

    def test_link_history_duration_uses_resolution_and_keeps_otrs_terminal_date(self):
        from datetime import timedelta
        from django.utils import timezone

        now = timezone.now()
        created = now - timedelta(hours=40)
        resolved = created + timedelta(hours=7)
        terminal = created + timedelta(hours=31)
        ticket = OTRSTicket.objects.create(
            external_id=99002, ticket_number="202610240000002", queue_external_id=107,
            queue_name="NOC - ATACADO", external_state_id=2, external_state_name="closed successful",
            business_status="CLOSED", is_business_open=False, is_present_in_queue=True,
            source_created_at=created, resolved_at=resolved, terminal_state_at=terminal,
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id="base-ci", cmdb_link=self.link,
            relation_type="DependsOn", relation_state="Valid",
        )
        canceled_created = created - timedelta(hours=5)
        canceled_at = canceled_created + timedelta(hours=2)
        canceled = OTRSTicket.objects.create(
            external_id=99003, ticket_number="202610240000003", queue_external_id=107,
            queue_name="NOC - ATACADO", external_state_id=3, external_state_name="cancelled",
            business_status="CANCELED", is_business_open=False, is_present_in_queue=True,
            source_created_at=canceled_created, terminal_state_at=canceled_at, canceled_at=canceled_at,
        )
        self.client.force_login(self.user)

        detail = self.client.get(reverse("base_links:detail", args=[self.link.pk])).json()
        row = next(item for item in detail["history"]["tickets"] if item["id"] == ticket.pk)

        self.assertEqual(row["duration_hours"], 7)
        self.assertFalse(row["over_24h"])
        self.assertEqual(row["resolved_at"], resolved.isoformat())
        self.assertEqual(row["terminal_at"], terminal.isoformat())
        from aplicativos.base_links.views import _ticket_duration_hours
        self.assertEqual(_ticket_duration_hours(canceled, now), 2)

    def test_detail_endpoint_uses_one_local_mirror_query(self):
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("base_links:detail", args=[self.link.pk]))
        self.assertEqual(response.status_code, 200)
        mirror_queries = [query for query in queries if "integracoes_cmdblinkmirror" in query["sql"].lower()]
        self.assertEqual(len(mirror_queries), 1)
        detail = response.json()
        for key in ("project_legacy", "operadora", "provider", "provider_cmdb_config_item_id", "provider_cmdb_number", "isp_raw", "config_item_id", "config_item_number"):
            self.assertIn(key, detail)
        self.assertNotIn("SenhaPPOE", detail)

    def test_list_query_count_does_not_grow_with_rows_on_page(self):
        for index in range(20):
            CMDBLinkMirror.objects.create(source_config_item_id=f"query-ci-{index:02d}", name=f"Link {index:02d}", operator=self.operator, legacy_project="Projeto congelado", business_project="Projeto congelado")
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse("base_links:index"))
        for index in range(20, 45):
            CMDBLinkMirror.objects.create(source_config_item_id=f"query-ci-{index:02d}", name=f"Link {index:02d}", operator=self.operator, legacy_project="Projeto congelado", business_project="Projeto congelado")
        with CaptureQueriesContext(connection) as larger:
            self.client.get(reverse("base_links:index"))
        self.assertLessEqual(len(larger), len(small) + 1)

    def test_search_keeps_data_scope_applied_before_text_matching(self):
        outside_operator = CMDBOperatorMirror.objects.create(source_config_item_id="base-op-out", name="FIND-ME-OUTSIDE")
        outside = CMDBLinkMirror.objects.create(source_config_item_id="base-ci-out", name="FIND-ME-OUTSIDE", operator=outside_operator, legacy_project="Outside")
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name="Claro", normalized_name="claro")])
        scope.allowed_projects.set([Projeto.objects.create(name="Projeto congelado", normalized_name="projeto congelado")])
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {"q": "FIND-ME-OUTSIDE"})
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_pagination_keeps_filter_query_parameters(self):
        for index in range(30):
            CMDBLinkMirror.objects.create(source_config_item_id=f"base-ci-{index:02d}", source_number=f"BASE-{index:02d}", name=f"Link {index:02d}", state="SP", operator=self.operator, legacy_project="Projeto congelado", business_project="Projeto congelado")
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"), {"uf": "SP", "projeto": "Projeto congelado", "page_size": 25, "page": 2})
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "uf=SP")
        self.assertContains(response, "projeto=Projeto+congelado")
        self.assertContains(response, "page=1")

    def test_export_uses_mirrors_and_current_scope_with_safe_semantic_columns(self):
        permission = Permission.objects.get(codename="export_base")
        self.user.user_permissions.add(permission)
        outside_operator = CMDBOperatorMirror.objects.create(source_config_item_id="export-outside-op", name="EXPORT-OUTSIDE-MUST-NOT-APPEAR")
        CMDBLinkMirror.objects.create(source_config_item_id="export-outside-link", name="EXPORT-OUTSIDE-MUST-NOT-APPEAR", operator=outside_operator, legacy_project="Outside")
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name="Claro", normalized_name="claro")])
        scope.allowed_projects.set([Projeto.objects.create(name="Projeto congelado", normalized_name="projeto congelado")])
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:export"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8-sig")
        self.assertIn("Projeto", content)
        self.assertIn("Cliente", content)
        self.assertIn("Operadora CMDB", content)
        self.assertNotIn("Senha PPOE", content)
        self.assertNotIn("senha-secreta", content)
        self.assertNotIn("EXPORT-OUTSIDE-MUST-NOT-APPEAR", content)

    def test_custom_scope_is_restrictive_and_project_uses_frozen_legacy_value(self):
        outside_operator = CMDBOperatorMirror.objects.create(source_config_item_id="base-op-out", name="Outside")
        CMDBLinkMirror.objects.create(source_config_item_id="base-ci-out", state="RJ", operator=outside_operator, legacy_project="Outside")
        group = self.user.groups.get(name="Analista NOC")
        scope = group.data_scope
        scope.mode = GroupDataScope.Mode.CUSTOM
        scope.save()
        scope.allowed_operators.set([Operadora.objects.create(name="Claro", normalized_name="claro")])
        scope.allowed_projects.set([Projeto.objects.create(name="Projeto congelado", normalized_name="projeto congelado")])
        self.client.force_login(self.user)
        response = self.client.get(reverse("base_links:index"))
        self.assertEqual(response.context["total_links"], 1)
        self.assertContains(response, "LINK-001")
        detail = self.client.get(reverse("base_links:detail", args=[self.link.pk]))
        self.assertEqual(detail.status_code, 200)
        denied = self.client.get(reverse("base_links:detail", args=[CMDBLinkMirror.objects.get(source_config_item_id="base-ci-out").pk]))
        self.assertEqual(denied.status_code, 404)

    def test_map_aggregate_counts_real_links_and_open_tickets(self):
        ticket = OTRSTicket.objects.create(external_id=500, ticket_number="T-500", queue_external_id=107, queue_name="NOC - ATACADO", external_state_id=1, external_state_name="em atendimento", business_status="OPEN", is_business_open=True, is_present_in_queue=True, source_created_at=__import__("django.utils.timezone", fromlist=["now"]).now())
        OTRSTicketConfigItem.objects.create(ticket=ticket, config_item_id="base-ci", cmdb_link=self.link, relation_type="DependsOn", relation_state="Valid")
        self.client.force_login(self.user)
        data = self.client.get(reverse("base_links:map-aggregates"), {"mode": "calls", "period": "last_30"}).json()
        self.assertEqual(data["filtered_source_total"], 1)
        self.assertEqual(data["states"]["SP"]["total_links"], 1)
        self.assertEqual(data["states"]["SP"]["active_tickets"], 1)
        self.assertEqual(data["affected_link_count"], 1)
