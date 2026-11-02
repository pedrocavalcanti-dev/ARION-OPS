from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

import re
from pathlib import Path

from .documentation import DOCS_ROOT, DOCUMENTS, DocumentationService, _document_match, render_markdown
from .models import GroupDataScope, UserPreference


class DocumentationPortalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="docs-admin", password="secret")
        content_type = ContentType.objects.get_for_model(GroupDataScope)
        permission = Permission.objects.get(content_type=content_type, codename="manage_groups")
        cls.admin.user_permissions.add(permission)
        cls.regular_user = get_user_model().objects.create_user(username="docs-user", password="secret")

    def test_authorized_user_can_open_portal_and_page(self):
        self.client.force_login(self.admin)
        home = self.client.get(reverse("configuracoes:documentation"))
        page = self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": "arquitetura"}))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Como podemos ajudar?")
        self.assertContains(home, "Segurança e operação")
        self.assertContains(home, "Dados e Integrações")
        self.assertContains(home, "9 documentos")
        self.assertContains(home, "7 documentos")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Caminho da request")

    def test_access_requires_login_and_existing_permission(self):
        url = reverse("configuracoes:documentation")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get(reverse("configuracoes:documentation-releases")).status_code, 302)
        self.client.force_login(self.regular_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": "arquitetura"})).status_code, 403)
        self.assertEqual(self.client.get(reverse("configuracoes:documentation-search"), {"q": "OTRS"}).status_code, 403)
        releases = self.client.get(reverse("configuracoes:documentation-releases"))
        self.assertEqual(releases.status_code, 200)
        self.assertNotContains(releases, reverse("configuracoes:documentation-page", kwargs={"slug": "cmdb"}))
        self.assertNotContains(releases, 'class="docs-search docs-search--header"')

    def test_releases_page_uses_the_single_catalog_and_appears_in_documentation_navigation(self):
        self.client.force_login(self.admin)
        home = self.client.get(reverse("configuracoes:documentation"))
        response = self.client.get(reverse("configuracoes:documentation-releases"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(home, "Atualizações e versões")
        self.assertContains(response, "Atualiza&ccedil;&otilde;es e vers&otilde;es")
        self.assertContains(response, "ARION OPS 1.3.0 — Visão Operacional, Qualidade e Segurança")
        self.assertContains(response, "ARION OPS 1.2.0 — Identidade, Cockpit e Inteligência Operacional")
        self.assertContains(response, "data-pwa-update-version")
        self.assertContains(response, "Sobre o ARION OPS")
        self.assertContains(response, 'id="release-v1.3.0"')
        self.assertContains(response, "Vers&atilde;o carregada nesta p&aacute;gina: v1.3.0")
        self.assertEqual(response.context["version"], "1.3.0")
        self.assertEqual(response.context["current_release"]["version"], response.context["version"])

    def test_portal_receives_the_existing_saved_theme(self):
        UserPreference.objects.create(user=self.admin, theme=UserPreference.Theme.LIGHT)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("configuracoes:documentation"))
        self.assertContains(response, 'data-user-theme="light"')
        self.assertContains(response, 'data-theme-toggle')

    def test_unknown_and_arbitrary_slugs_are_not_resolved(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": "arquivo-secreto"})).status_code, 404)
        self.assertIsNone(DocumentationService.get(".."))

    def test_path_traversal_mapping_is_rejected(self):
        malicious = dict(DOCUMENTS)
        malicious["fora"] = ("Fora", "", "Padrões", "../settings.py")
        with patch("aplicativos.configuracoes.documentation.DOCUMENTS", malicious):
            self.assertIsNone(DocumentationService.get("fora"))

    def test_renderer_covers_headings_tables_code_quotes_and_links(self):
        body, headings = render_markdown(
            "# Seção Árvore\n## Outro título\n\n| Coluna | Valor |\n| --- | --- |\n| Nome | **ARION** |\n\n> **Segurança:** Nota técnica\n\n```python\nprint('ok')\n```\n\n```mermaid\nflowchart LR\nA[Browser] --> B[Django]\nB --> C[Banco local]\n```\n\n[Segurança](../SEGURANCA.md)"
        )
        self.assertIn('id="secao-arvore"', body)
        self.assertIn("<thead>", body)
        self.assertIn("<blockquote>", render_markdown("> Nota técnica")[0])
        self.assertIn('class="language-python"', body)
        self.assertIn('class="docs-diagram-svg', body)
        self.assertIn('class="docs-callout docs-callout--security"', body)
        self.assertIn(reverse("configuracoes:documentation-page", kwargs={"slug": "seguranca"}), body)
        self.assertIn(reverse("configuracoes:documentation") + "#home-docs-modulos", render_markdown("[Funcionalidades](funcionalidades/)")[0])
        self.assertIn(">Arquitetura</a>", render_markdown("[ARQUITETURA.md](../ARQUITETURA.md)")[0])
        self.assertEqual(headings[1][1], "outro-titulo")

    def test_search_finds_document_content(self):
        results = DocumentationService.search("CMDB")
        self.assertTrue(results)
        self.assertTrue(any(result["slug"] == "base" for result in results))

    def test_every_registered_document_renders(self):
        self.client.force_login(self.admin)
        navigation = list(DOCUMENTS)
        for index, slug in enumerate(navigation):
            with self.subTest(slug=slug):
                response = self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": slug}))
                self.assertEqual(response.status_code, 200)
                content = response.content.decode("utf-8")
                sequence = content.split('<nav class="docs-sequence"', 1)[1].split("</nav>", 1)[0]
                if index:
                    previous_url = reverse("configuracoes:documentation-page", kwargs={"slug": navigation[index - 1]})
                    self.assertIn(previous_url, sequence)
                if index + 1 < len(navigation):
                    next_url = reverse("configuracoes:documentation-page", kwargs={"slug": navigation[index + 1]})
                    self.assertIn(next_url, sequence)

    def test_registry_has_search_metadata_and_expected_technical_pages(self):
        expected = {"chamados", "banco-de-dados", "otrs", "cmdb", "worker", "dicionario-de-dados", "qualidade"}
        self.assertTrue(expected.issubset(DOCUMENTS))
        for item in DocumentationService.navigation():
            with self.subTest(slug=item["slug"]):
                self.assertTrue(item["title"])
                self.assertTrue(item["category"])
                self.assertTrue(item["description"])
                self.assertTrue(item["search_terms"])
        project = DocumentationService.get("projeto")
        self.assertEqual(project["title"], "Projeto — ARION OPS")
        self.assertNotIn("<h1", project["body"])
        self.assertEqual(len(DOCUMENTS), 32)

    def test_search_terms_find_current_operational_documents(self):
        cases = {
            "OTRS": {"otrs", "fontes-de-dados", "integracoes"},
            "CMDB": {"cmdb", "base", "fontes-de-dados"},
            "TicketTracking": {"chamados", "dicionario-de-dados"},
            "Data Scope": {"usuarios-grupos-escopos", "seguranca"},
            "ConfigItemID": {"otrs", "cmdb", "banco-de-dados"},
            "Falha Massiva": {"otrs", "chamados"},
            "source_config_item_id": {"cmdb", "banco-de-dados", "dicionario-de-dados"},
            "closing_code_raw": {"otrs", "dicionario-de-dados"},
            "GroupDataScope": {"banco-de-dados", "usuarios-grupos-escopos"},
            "IntegrationLease": {"banco-de-dados", "worker"},
            "CodigoFechamento": {"otrs", "dicionario-de-dados"},
            "TELMEX": {"cmdb"},
            "DPAPI": {"banco-de-dados", "implantacao-e-producao"},
        }
        for query, expected in cases.items():
            found = {result["slug"] for result in DocumentationService.search(query)}
            with self.subTest(query=query):
                self.assertTrue(expected.issubset(found), f"{query}: {found}")

    def test_internal_links_from_portal_pages_resolve_to_registered_documents_and_anchors(self):
        for slug, metadata in DOCUMENTS.items():
            path = DOCS_ROOT / metadata[3]
            source = path.read_text(encoding="utf-8")
            _, headings = render_markdown(source)
            heading_ids = {heading[1] for heading in headings}
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source):
                if target.startswith(("http://", "https://", "#")):
                    continue
                target_path, separator, fragment = target.partition("#")
                with self.subTest(source=slug, target=target):
                    match = _document_match(target_path) if target_path else (slug, metadata)
                    self.assertIsNotNone(match, f"Link interno não registrado: {target}")
                    if separator and fragment:
                        target_source = (DOCS_ROOT / DOCUMENTS[match[0]][3]).read_text(encoding="utf-8")
                        _, target_headings = render_markdown(target_source)
                        self.assertIn(fragment, {item[1] for item in target_headings})

    def test_every_registered_mermaid_block_has_a_visual_rendering(self):
        for slug, metadata in DOCUMENTS.items():
            source = (DOCS_ROOT / metadata[3]).read_text(encoding="utf-8-sig")
            for diagram in re.findall(r"```mermaid\s*\n(.*?)```", source, re.S):
                with self.subTest(slug=slug, diagram=diagram[:45]):
                    body, _ = render_markdown(f"```mermaid\n{diagram}\n```")
                    self.assertIn('class="docs-diagram-svg', body)
                    self.assertIn('viewBox="0 0 ', body)
                    self.assertIn('preserveAspectRatio="xMidYMid meet"', body)
                    self.assertIn('data-docs-diagram-expand', body)
                    self.assertIn('details class="docs-diagram-source"><summary>Ver código do diagrama</summary>', body)
                    self.assertNotIn("textLength=", body)
                    self.assertNotIn("lengthAdjust=", body)
                    viewbox = re.search(r'viewBox="0 0 (\d+) (\d+)"', body)
                    self.assertIsNotNone(viewbox)
                    width, height = map(int, viewbox.groups())
                    self.assertLessEqual(width, 900)
                    self.assertLessEqual(height, 650)

    def test_badges_and_dictionary_filters_render_for_their_document(self):
        body, _ = render_markdown("{{badge:CMDB}} {{badge:MIRROR}} {{badge:READ-ONLY}} {{badge:DERIVADO}} {{badge:REMOTE}} {{badge:RECONSTRUÍVEL}} {{badge:PERSISTENTE}} {{badge:SEGURANÇA}}")
        self.assertIn("docs-badge--source", body)
        self.assertIn("docs-badge--mirror", body)
        self.assertIn("docs-badge--readonly", body)
        self.assertIn("docs-badge--derived", body)
        self.assertIn("docs-badge--remote", body)
        self.assertIn("docs-badge--reconstructible", body)
        self.assertIn("docs-badge--persistent", body)
        self.assertIn("docs-badge--security", body)
        source_callout, _ = render_markdown("> **Fonte da verdade:** OTRS permanece oficial")
        self.assertIn("docs-callout--source-of-truth", source_callout)

        self.client.force_login(self.admin)
        response = self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": "dicionario-de-dados"}))
        self.assertContains(response, 'data-doc-dictionary-search')
        self.assertContains(response, 'data-doc-dictionary-source')
        self.assertContains(response, 'data-doc-dictionary-count')
        self.assertContains(response, 'class="doc-table-wrap docs-dictionary-index"')
        self.assertContains(response, 'data-doc-dictionary-row=""')
        database_page = self.client.get(reverse("configuracoes:documentation-page", kwargs={"slug": "banco-de-dados"}))
        self.assertContains(database_page, "Ampliar")
        self.assertContains(database_page, "Ver código do diagrama")

    def test_cmdb_page_has_restored_technical_reference(self):
        source = (DOCS_ROOT / DOCUMENTS["cmdb"][3]).read_text(encoding="utf-8-sig")
        page = DocumentationService.get("cmdb")
        words = re.findall(r"\w+", re.sub(r"<[^>]+>", " ", page["body"]))
        self.assertGreater(len(words), 450)
        self.assertGreater((DOCS_ROOT / DOCUMENTS["cmdb"][3]).stat().st_size, 5000)
        for term in ("ConfigItemSearch", "ConfigItemGet", "CMDBLinkMirror", "CMDBOperatorMirror", "TELMEX", "UsuarioPPOE"):
            with self.subTest(term=term):
                self.assertIn(term, source)

    def test_database_reference_documents_physical_schema_and_relationships(self):
        source = (DOCS_ROOT / DOCUMENTS["banco-de-dados"][3]).read_text(encoding="utf-8-sig")
        for term in (
            "integracoes_otrsticket", "integracoes_cmdblinkmirror", "chamados_tickettracking",
            "UniqueConstraint", "CASCADE", "SET_NULL", "PROTECT", "SQLite → MySQL",
            "IntegrationSyncState", "AriConversation", "password_protected",
        ):
            with self.subTest(term=term):
                self.assertIn(term, source)

    def test_registered_documents_are_populated_and_rendered_without_truncation(self):
        for slug, metadata in DOCUMENTS.items():
            source = (DOCS_ROOT / metadata[3]).read_text(encoding="utf-8-sig")
            page = DocumentationService.get(slug)
            with self.subTest(slug=slug):
                self.assertGreater(len(source.strip()), 100)
                self.assertIsNotNone(page)
                self.assertGreater(len(page["body"].strip()), 100)
                self.assertTrue(page["headings"])

    def test_documentation_diagram_and_table_styles_cover_modal_mobile_and_themes(self):
        static_root = DOCS_ROOT.parent / "arion_geo" / "static"
        frontend_root = DOCS_ROOT.parent / "frontend_src"
        css = (static_root / "css" / "documentacao.css").read_text(encoding="utf-8")
        js = (frontend_root / "js" / "documentacao.js").read_text(encoding="utf-8")
        self.assertIn(".docs-diagram-dialog", css)
        self.assertIn("100dvh", css)
        self.assertIn(".docs-diagram-dialog__track", css)
        self.assertIn("@media (max-width: 820px)", css)
        self.assertIn(".docs-toc { display: none; }", css)
        self.assertIn(".doc-table-wrap { max-width: 100%; overflow-x: auto;", css)
        self.assertIn("--docs-bg: var(--bg-primary)", css)
        self.assertIn("showModal()", js)
        self.assertIn("setDiagramScale", js)
        self.assertIn("Redefinir zoom", js)
        self.assertIn('addEventListener("close"', js)
        self.assertIn("diagramOpener?.focus()", js)

