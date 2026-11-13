import json
import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import reverse

from config.releases import release_catalog
from aplicativos.integracoes.services.project_names import canonical_project_name


class PwaDeliveryTests(SimpleTestCase):
    def test_login_shell_links_manifest_and_has_no_version_notice(self):
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="manifest" href="/manifest.webmanifest"')
        self.assertContains(response, "apple-mobile-web-app-capable")
        self.assertNotContains(response, "data-pwa-update")
        self.assertNotContains(response, "pwa-update-indicator")

    def test_manifest_is_public_with_installable_scope_and_icons(self):
        response = self.client.get(reverse("pwa-manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"].split(";", 1)[0], "application/manifest+json")
        self.assertIn("public", response["Cache-Control"])
        manifest = json.loads(response.content)
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["short_name"], "ARION")
        self.assertIn({"src": "/static/pwa/arion-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}, manifest["icons"])

    def test_worker_has_root_scope_and_is_never_cached(self):
        response = self.client.get(reverse("pwa-service-worker"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn(b"__ARION_CACHE_REVISION__", response.content)
        self.assertIn(b"arion-shell-", response.content)
        self.assertIn(b"request.mode === \"navigate\"", response.content)
        self.assertIn(b"url.pathname.startsWith(\"/static/\")", response.content)
        self.assertIn(b"Cache-Control", response.content)

    def test_version_api_uses_the_release_catalog_and_is_never_cached(self):
        response = self.client.get(reverse("api-version"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        payload = response.json()
        catalog = release_catalog()
        self.assertEqual(catalog["version"], "1.4.1")
        self.assertEqual(payload["version"], catalog["version"])
        self.assertEqual(payload["released_at"], catalog["current_release"]["released_at"])
        self.assertEqual(payload["release_title"], catalog["current_release"]["title"])
        self.assertNotIn("commit", payload)
        self.assertNotIn("secret", payload)

    def test_worker_injects_canonical_version_into_arion_cache_names(self):
        response = self.client.get(reverse("pwa-service-worker"))
        source = Path(finders.find("pwa/service-worker.js")).read_text(encoding="utf-8")
        version = release_catalog()["version"]

        self.assertIn(f"arion-shell-{version}-", response.content.decode("utf-8"))
        self.assertIn(f"arion-static-{version}-", response.content.decode("utf-8"))
        self.assertIn("clients.claim()", source)
        self.assertIn("SKIP_WAITING", source)
        self.assertIn("name.startsWith(\"arion-shell-\")", source)
        self.assertIn("name.startsWith(\"arion-static-\")", source)

    def test_production_build_covers_template_entrypoints_and_minifies_worker(self):
        templates_root = Path("arion_geo/templates")
        entrypoints = set()
        for template in templates_root.rglob("*.html"):
            content = template.read_text(encoding="utf-8")
            entrypoints.update(re.findall(r"\{%\s*arion_js\s+['\"](js/[A-Za-z0-9_./-]+\.js)['\"]\s*%\}", content))

        build = Path("frontend_src/build.mjs").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(entrypoints), 15)
        for entrypoint in entrypoints:
            with self.subTest(entrypoint=entrypoint):
                self.assertTrue((Path("frontend_src") / entrypoint).is_file())
        self.assertTrue({
            "js/pwa/pwa.js", "js/pwa/launch.js", "js/configuracoes/settings.js",
            "js/mapa/mapa-real.js", "js/chamados/chamados.js",
        }.issubset(entrypoints))
        self.assertIn("const tagPattern", build)
        self.assertIn("references.add(match[1])", build)
        self.assertIn("JavaScriptObfuscator.obfuscate", build)
        self.assertIn("minify: true", build)
        self.assertIn("sourcemap: false", build)
        self.assertIn('manifest.__service_worker__', build)
        self.assertIn('esbuild.transform(workerSource', build)
        self.assertIn('path.join(staticRoot, "pwa", "service-worker.js")', build)

    def test_global_update_notice_is_responsive_and_waits_for_user_action(self):
        partial = Path("arion_geo/templates/partials/pwa_update.html").read_text(encoding="utf-8")
        script = Path("frontend_src/js/pwa/pwa.js").read_text(encoding="utf-8")
        styles = Path("arion_geo/static/css/pwa.css").read_text(encoding="utf-8")

        self.assertIn("data-pwa-version-endpoint", partial)
        self.assertIn("data-pwa-update-news", partial)
        self.assertIn("data-pwa-update-accept", partial)
        self.assertIn("data-pwa-update-dismiss", partial)
        self.assertIn('cache: "no-store"', script)
        self.assertIn("15 * 60 * 1000", script)
        self.assertIn("20 * 60 * 1000", script)
        self.assertIn('type: "SKIP_WAITING"', script)
        self.assertIn('addEventListener("controllerchange"', script)
        self.assertIn("reloadStarted", script)
        self.assertIn("window.location.reload()", script)
        self.assertIn("env(safe-area-inset-bottom)", styles)
        self.assertIn("@media (max-width:900px)", styles)
        self.assertIn(".pwa-update__actions { display:grid;", styles)
        self.assertIn("repeat(auto-fit,minmax(82px,1fr))", styles)

    def test_update_indicator_persists_after_dismissal_and_update_errors_can_retry(self):
        script = Path("frontend_src/js/pwa/pwa.js").read_text(encoding="utf-8")
        base_template = Path("arion_geo/templates/base.html").read_text(encoding="utf-8")
        mobile_navigation = Path("arion_geo/templates/partials/mobile_navigation.html").read_text(encoding="utf-8")
        documentation_base = Path("arion_geo/templates/documentacao/base_documentacao.html").read_text(encoding="utf-8")
        overview = Path("arion_geo/templates/painel/index.html").read_text(encoding="utf-8")
        overview_styles = Path("arion_geo/static/css/painel/painel.css").read_text(encoding="utf-8")
        indicator = Path("arion_geo/templates/partials/pwa_update_indicator.html").read_text(encoding="utf-8")
        map_styles = Path("arion_geo/static/css/mapa/mapa.css").read_text(encoding="utf-8")

        self.assertIn("window.localStorage.getItem", script)
        self.assertIn("arionOpsUpdateVersion", script)
        self.assertIn("arionOpsUpdateDismissedVersion", script)
        self.assertIn("function syncUpdateState(available, version)", script)
        self.assertIn("Tentar novamente", script)
        self.assertIn("waitForWaitingWorker", script)
        self.assertIn("Atualizando...", script)
        self.assertIn("if (!await controllerChange) throw new Error", script)
        self.assertIn('data-pwa-update-menu', indicator)
        self.assertIn('data-pwa-update-indicator', indicator)
        self.assertIn('data-pwa-update-popover', indicator)
        self.assertIn('partials/pwa_update_indicator.html', base_template)
        self.assertNotIn('partials/pwa_update_indicator.html', mobile_navigation)
        self.assertIn('partials/pwa_update_indicator.html', documentation_base)
        self.assertNotIn('partials/pwa_update_indicator.html', overview)
        self.assertIn('placement="global"', base_template)
        self.assertIn('.pwa-update-menu--global', Path("arion_geo/static/css/pwa.css").read_text(encoding="utf-8"))
        self.assertNotIn('.overview-hero .pwa-update-menu--overview', overview_styles)
        self.assertIn('documentation-releases', mobile_navigation)
        self.assertIn('.geo-map-stage .state-panel { position:fixed; top:var(--arion-global-header-height,60px); right:0; bottom:0;', map_styles)
        self.assertIn('.geo-map-stage .map-backdrop { position:fixed; inset:0; }', map_styles)
        self.assertIn('.geo-map-stage.has-selection { z-index:var(--map-z-drawer); }', map_styles)
        self.assertIn('body.geo-body:has(.geo-map-stage.has-selection) .pwa-update', map_styles)

    def test_pwa_icons_are_real_pngs_with_expected_dimensions(self):
        expected = {
            "pwa/arion-192.png": 192,
            "pwa/arion-512.png": 512,
            "pwa/arion-512-maskable.png": 512,
            "pwa/arion-apple-touch-icon.png": 180,
        }
        for relative_path, size in expected.items():
            with self.subTest(icon=relative_path):
                icon_path = Path(finders.find(relative_path))
                contents = icon_path.read_bytes()
                self.assertTrue(contents.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertGreater(len(contents), 100)
                self.assertEqual((contents[16] << 24) + (contents[17] << 16) + (contents[18] << 8) + contents[19], size)
                self.assertEqual((contents[20] << 24) + (contents[21] << 16) + (contents[22] << 8) + contents[23], size)

    def test_operational_responses_are_never_added_to_the_worker_cache(self):
        worker = Path(finders.find("pwa/service-worker.js")).read_text(encoding="utf-8")
        static_guard = worker.index('if (!url.pathname.startsWith("/static/")')
        cache_write = worker.index("await cache.put(request, response.clone())")
        self.assertLess(static_guard, cache_write)
        self.assertLess(worker.index('if (request.mode === "navigate")'), static_guard)
        self.assertIn("fetch(request).catch", worker)
        self.assertIn("request.method !== \"GET\"", worker)
        self.assertIn('const OFFLINE_URL = "/static/pwa/offline.html"', worker)

    def test_mobile_navigation_is_permission_gated_and_ticket_modal_keeps_header_visible(self):
        template = Path("arion_geo/templates/base.html").read_text(encoding="utf-8")
        navigation = Path("arion_geo/templates/partials/mobile_navigation.html").read_text(encoding="utf-8")
        modal_css = Path("arion_geo/static/css/base-links.css").read_text(encoding="utf-8")

        self.assertIn('include "partials/mobile_navigation.html"', template)
        self.assertIn("data-mobile-navigation", navigation)
        self.assertIn("perms.configuracoes.view_operational_tickets", navigation)
        self.assertIn("perms.configuracoes.manage_groups", navigation)
        self.assertIn("perms.configuracoes.view_integrations", navigation)
        self.assertIn("perms.configuracoes.view_system_info", navigation)
        self.assertIn("{% csrf_token %}", navigation)
        self.assertIn(".base-ticket-dialog[open] { display:flex; flex-direction:column; }", modal_css)
        self.assertIn("position:sticky; top:0", modal_css)
        self.assertIn("overflow-y:auto", modal_css)

    def test_mobile_settings_sections_and_admin_card_templates_are_present(self):
        for template_name in (
            "configuracoes/base.html", "configuracoes/admin/users.html",
            "configuracoes/admin/groups.html", "configuracoes/admin/scopes.html",
            "configuracoes/admin/audit.html", "integracoes/base_integracoes.html",
            "documentacao/base_documentacao.html",
        ):
            get_template(template_name)
        settings = Path("arion_geo/templates/configuracoes/base.html").read_text(encoding="utf-8")
        users = Path("arion_geo/templates/configuracoes/admin/users.html").read_text(encoding="utf-8")
        audit = Path("arion_geo/templates/configuracoes/admin/audit.html").read_text(encoding="utf-8")
        css = Path("arion_geo/static/css/configuracoes/settings.css").read_text(encoding="utf-8")
        self.assertIn('class="settings-mobile-sections"', settings)
        self.assertRegex(users, r'class="admin-table admin-table--cards(?:\s[^\"]*)?"')
        self.assertIn('class="admin-table audit-table admin-table--cards"', audit)
        self.assertIn('.settings-mobile-sections nav a[aria-current="page"]', css)
        self.assertIn('.audit-table--cards tr', css)

    def test_map_controls_hide_internal_provenance_and_keep_project_aliases_explicit(self):
        template = Path("arion_geo/templates/mapa/_cmdb.html").read_text(encoding="utf-8")
        script = Path("frontend_src/js/mapa/mapa-real.js").read_text(encoding="utf-8")
        self.assertNotIn("data-provenance-filter", template)
        self.assertNotIn("Procedência", template)
        self.assertIn('chips.push(["uf","UF: "+state.selectedUF])', script)
        self.assertEqual(canonical_project_name("BDN BRADESCO"), "BRADESCO")
        self.assertEqual(canonical_project_name("PROJETO BB"), "BANCO DO BRASIL")
        self.assertEqual(canonical_project_name("BOTICARIO"), "O BOTICÁRIO")
        self.assertEqual(canonical_project_name("BRADESCO SEGUROS"), "BRADESCO SEGUROS")
        self.assertEqual(canonical_project_name("BB SEGUROS"), "BB SEGUROS")
        self.assertIn('<div class="map-backdrop" data-panel-backdrop aria-hidden="true"></div>', template)
        self.assertNotIn('panelBackdrop.addEventListener("click",clearSelection)', script)
        self.assertNotIn('event.key==="Escape"&&state.selectedUF', script)

    def test_operational_ticket_template_compiles_with_mobile_filter_and_card_layout(self):
        get_template("chamados/index.html")
        template = Path("arion_geo/templates/chamados/index.html").read_text(encoding="utf-8")
        styles = Path("arion_geo/static/css/chamados/chamados.css").read_text(encoding="utf-8")
        self.assertIn('data-mobile-filter-sheet', template)
        self.assertIn('data-mobile-filters-open', template)
        self.assertIn('data-label="Chamado"', template)
        self.assertIn('data-label="Acompanhamento ARION"', template)
        self.assertIn('name="assignee"', template[template.index('class="ticket-filter-secondary"'):])
        self.assertIn(".ticket-mobile-filter-heading { display:none; }", styles)
        self.assertIn(".ticket-more-filters[open] .ticket-filter-secondary { display:grid; }", styles)
        self.assertIn(".ticket-filter-sheet { display:none; min-width:0;", styles)
        self.assertIn('grid-template-areas:"ticket age" "client client"', styles)
        self.assertIn('data-mobile-filters-close><svg', template)
