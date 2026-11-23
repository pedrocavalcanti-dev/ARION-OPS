import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.frontend_checks import check_frontend_assets, template_js_references
from config.templatetags.arion_assets import _production_manifest, arion_js, arion_bundle_js
from config.views import pwa_service_worker


class FrontendAssetTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="arion-assets-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.static = self.root / "static"
        self.templates = self.root / "templates"
        self.templates.mkdir()
        self.manifest_path = self.root / "frontend.json"
        self.collected = self.root / "collected"
        self.manifest = {
            "js/page.js": "arion_build/page.123456789abc.js",
            "js/bundle.js": "arion_build/bundle.123456789abc.js",
            "__service_worker__": "arion_build/pwa/service-worker.123456789abc.js",
        }
        self.write(self.templates / "page.html", "{% arion_js 'js/page.js' %}{% arion_bundle_js 'js/bundle.js' %}")
        for value in self.manifest.values():
            self.write(self.static / value, "(()=>{})();")
        self.write(self.static / "vendor/echarts/echarts.min.js", "/* public vendor */")
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.override = override_settings(
            BASE_DIR=self.root, STATICFILES_DIRS=[self.static], STATIC_ROOT=self.collected,
            ARION_FRONTEND_MANIFEST_PATH=self.manifest_path, ARION_PRODUCTION=True,
            DEBUG=False, STATIC_URL="/static/",
            STORAGES={"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}},
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        _production_manifest.cache_clear()
        self.addCleanup(_production_manifest.cache_clear)

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def controls(self, *, collected=False):
        results = {}
        check_frontend_assets(settings, lambda ok, label, detail: results.update({label: (ok, detail)}), collected=collected)
        return results

    def test_both_tags_worker_and_only_authorized_vendor_are_accepted(self):
        self.assertTrue(all(ok for ok, detail in self.controls().values()))
        self.write(self.static / "vendor/internal.js", "readable source")
        self.assertFalse(self.controls()["fontes JS em STATICFILES_DIRS"][0])

    def test_missing_extra_and_legacy_references_fail_with_exact_names(self):
        del self.manifest["js/bundle.js"]
        self.manifest["js/obsolete.js"] = "arion_build/page.123456789abc.js"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.write(self.templates / "legacy.html", "{% static 'js/legacy.js' %}")
        ok, detail = self.controls()["referências JS"]
        self.assertFalse(ok)
        for name in ("js/bundle.js", "js/obsolete.js", "js/legacy.js"):
            self.assertIn(name, detail)

    def test_missing_worker_is_a_manifest_failure(self):
        del self.manifest["__service_worker__"]
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.assertFalse(self.controls()["referências JS"][0])

    def test_missing_bundle_and_build_source_map_fail(self):
        (self.static / self.manifest["js/page.js"]).unlink()
        self.assertFalse(self.controls()["bundles de produção"][0])
        self.write(self.static / self.manifest["js/page.js"], "ok")
        self.write(self.static / "arion_build/page.js.map", "{}")
        self.assertFalse(self.controls()["bundles de produção"][0])

    def test_readable_sources_in_static_dirs_are_never_accepted(self):
        for relative in ("js/qualidade/checklist.js", "pwa/service-worker.js"):
            self.write(self.static / relative, "readable source")
        ok, detail = self.controls()["fontes JS em STATICFILES_DIRS"]
        self.assertFalse(ok)
        self.assertIn("js/qualidade/checklist.js", detail)
        self.assertIn("pwa/service-worker.js", detail)

    def test_real_clean_and_incremental_collection_preserves_admin_and_vendor(self):
        call_command("collectstatic", interactive=False, clear=True, verbosity=0)
        self.assertTrue(all(ok for ok, detail in self.controls(collected=True).values()))
        self.assertTrue((self.collected / "admin/js/core.js").is_file())
        self.assertTrue((self.collected / "vendor/echarts/echarts.min.js").is_file())
        call_command("collectstatic", interactive=False, verbosity=0)
        self.assertTrue(all(ok for ok, detail in self.controls(collected=True).values()))
        self.assertEqual(self.client.get("/service-worker.js").status_code, 200)
        (self.collected / self.manifest["js/bundle.js"]).unlink()
        # Hashed files, rather than only the original names, must be present.
        storage = json.loads((self.collected / "staticfiles.json").read_text())["paths"]
        (self.collected / storage[self.manifest["js/bundle.js"]]).unlink()
        self.assertFalse(self.controls(collected=True)["collectstatic"][0])

    def test_old_collected_sources_and_maps_remain_blockers(self):
        self.write(self.collected / "js/legacy.js", "source")
        self.write(self.collected / "pwa/service-worker.old.js", "source")
        self.write(self.collected / "legacy.map", "{}")
        controls = self.controls(collected=True)
        self.assertFalse(controls["sources JS coletados"][0])
        self.assertFalse(controls["source maps coletados"][0])

    def test_production_worker_fails_closed_without_built_worker(self):
        (self.static / self.manifest["__service_worker__"]).unlink()
        self.assertEqual(pwa_service_worker(RequestFactory().get("/service-worker.js")).status_code, 503)

    def test_production_tags_resolve_hashes_and_fail_for_unknown_modules(self):
        call_command("collectstatic", interactive=False, verbosity=0)
        for tag in ("arion_js", "arion_bundle_js"):
            rendered = Template("{% " + tag + " 'js/page.js' %}").render(Context())
            self.assertIn("/static/arion_build/page.", rendered)
            self.assertNotIn("/static/js/", rendered)
        for tag in (arion_js, arion_bundle_js):
            with self.assertRaises(ImproperlyConfigured):
                tag("js/missing.js")
            with self.assertRaises(ImproperlyConfigured):
                tag("js/../secret.js")

    def test_repository_templates_use_canonical_sources_including_recent_modules(self):
        root = settings.PROJECT_ROOT
        references, legacy = template_js_references(root / "arion_geo/templates")
        self.assertFalse(legacy)
        for source in references:
            self.assertTrue((root / "frontend_src" / source).is_file(), source)
        self.assertTrue({"js/painel/plant-history.js", "js/qualidade/contacts.js", "js/base-links-identity.js",
                         "js/qualidade/archive.js", "js/qualidade/monitoring.js", "js/qualidade/checklist.js",
                         "js/integracoes/brevo.js", "js/mapa/mapa-real.js", "js/mapa/presentation-start.js"} <= references)
