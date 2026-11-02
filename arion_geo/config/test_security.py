from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils.http import url_has_allowed_host_and_scheme

from django.core.exceptions import ValidationError
from aplicativos.ari.file_security import validate_upload
from aplicativos.autenticacao.forms import LoginForm
from aplicativos.autenticacao.throttle import (
    clear_login_failures,
    client_ip_for_login,
    is_login_throttled,
    record_login_failure,
)
from aplicativos.base_links.views import _safe_csv_value
from aplicativos.integracoes.clients.cmdb import CMDBClient
from aplicativos.integracoes.clients.otrs import OTRSReadOnlyClient
from aplicativos.integracoes.forms import CMDBConfigurationForm, OTRSConfigurationForm
from aplicativos.integracoes.services.errors import IntegrationNotConfigured, OTRSTLSRequiredError
from aplicativos.painel.views import _safe_spreadsheet_text
from aplicativos.rankings.exports import _safe_cell
from aplicativos.relatorios.services.exports import _neutralize_formula
from .errors import csrf_failure
from .middleware import ProductionSecurityHeadersMiddleware, RequestErrorMiddleware


class ProductionSecurityHeadersTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    @override_settings(
        ARION_CSP_REPORT_ONLY=True,
        CONTENT_SECURITY_POLICY_REPORT_ONLY="default-src 'self'; frame-ancestors 'none'",
        PERMISSIONS_POLICY="camera=(), microphone=()",
        DEBUG=False,
    )
    def test_rebuilt_api_error_keeps_private_cache_and_browser_security_headers(self):
        def failing_view(_request):
            return HttpResponse("traceback and internal path", status=500)

        middleware = ProductionSecurityHeadersMiddleware(RequestErrorMiddleware(failing_view))
        response = middleware(self.factory.get("/api/private/"))

        self.assertEqual(response.status_code, 500)
        self.assertNotContains(response, "traceback", status_code=500)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Permissions-Policy"], "camera=(), microphone=()")
        self.assertFalse(response.has_header("Content-Security-Policy-Report-Only"))

        html_response = middleware(self.factory.get("/dashboard/"))
        self.assertEqual(html_response.status_code, 500)
        self.assertNotContains(html_response, "traceback", status_code=500)
        self.assertEqual(html_response["Content-Security-Policy-Report-Only"], "default-src 'self'; frame-ancestors 'none'")

    def test_all_dynamic_responses_are_private_but_static_files_keep_storage_cache_policy(self):
        dynamic = ProductionSecurityHeadersMiddleware(lambda _request: HttpResponse("private"))
        dynamic_request = self.factory.get("/dashboard/")
        dynamic_request.user = SimpleNamespace(is_authenticated=True)
        self.assertEqual(dynamic(dynamic_request)["Cache-Control"], "private, no-store")

        static = ProductionSecurityHeadersMiddleware(lambda _request: HttpResponse("asset"))
        static_response = static(self.factory.get("/static/arion_build/app.js"))
        self.assertFalse(static_response.has_header("Cache-Control"))

    @override_settings(
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_cloudflare_https_forwarded_proto_avoids_redirect_loop_and_emits_gradual_hsts(self):
        middleware = SecurityMiddleware(lambda _request: HttpResponse("ok"))

        insecure = middleware(self.factory.get("/login/", HTTP_X_FORWARDED_PROTO="http"))
        self.assertEqual(insecure.status_code, 301)
        self.assertTrue(insecure["Location"].startswith("https://"))

        forwarded_https = middleware(self.factory.get("/login/", HTTP_X_FORWARDED_PROTO="https"))
        self.assertEqual(forwarded_https.status_code, 200)
        self.assertEqual(forwarded_https["Strict-Transport-Security"], "max-age=31536000")

    def test_csrf_logs_do_not_include_untrusted_origin_or_reason_text(self):
        request = self.factory.post("/login/")
        untrusted_reason = "Origin checking failed - https://operator:secret-value@example.invalid/?token=hidden"

        with self.assertLogs("config.errors", level="WARNING") as captured:
            response = csrf_failure(request, reason=untrusted_reason)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("secret-value", " ".join(captured.output))
        self.assertNotIn("hidden", " ".join(captured.output))


class SecurityRegressionBaselineTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    def test_open_redirect_protection_blocks_external_hosts(self):
        allowed = {"127.0.0.1", "nocatacadoarion.bicoai.com.br"}
        self.assertFalse(url_has_allowed_host_and_scheme("https://evil.example/phish", allowed_hosts=allowed))
        self.assertFalse(url_has_allowed_host_and_scheme("//evil.example/phish", allowed_hosts=allowed))
        self.assertTrue(url_has_allowed_host_and_scheme("/painel/", allowed_hosts=allowed))

    @override_settings(ARION_LOGIN_MAX_FAILURES=5, ARION_LOGIN_THROTTLE_SECONDS=900)
    def test_login_rate_limiting_blocks_after_threshold_and_keeps_generic_message(self):
        request = self.factory.post("/login/", REMOTE_ADDR="127.0.0.1")

        with patch("django.contrib.auth.forms.authenticate", return_value=None) as mock_auth:
            for _ in range(5):
                form = LoginForm(request=request, data={"username": "Operador", "password": "wrong"})
                self.assertFalse(form.is_valid())
                first_error = form.non_field_errors()[0]

            self.assertTrue(is_login_throttled(request, "operador"))
            self.assertEqual(mock_auth.call_count, 5)

            # 6th attempt must be blocked without calling authenticate() and return the exact same message
            blocked_form = LoginForm(request=request, data={"username": "operador", "password": "correct-password"})
            self.assertFalse(blocked_form.is_valid())
            self.assertEqual(mock_auth.call_count, 5)
            self.assertEqual(blocked_form.non_field_errors()[0], first_error)

        # Expiration / reset releases the throttle
        cache.clear()
        self.assertFalse(is_login_throttled(request, "operador"))

    @override_settings(ARION_LOGIN_MAX_FAILURES=5, ARION_LOGIN_THROTTLE_SECONDS=900)
    def test_successful_login_clears_failure_counter(self):
        request = self.factory.post("/login/", REMOTE_ADDR="127.0.0.1")
        for _ in range(4):
            record_login_failure(request, "analista")
        self.assertFalse(is_login_throttled(request, "analista"))

        fake_user = SimpleNamespace(is_active=True)
        with patch("django.contrib.auth.forms.authenticate", return_value=fake_user):
            form = LoginForm(request=request, data={"username": "Analista", "password": "valid-password"})
            self.assertTrue(form.is_valid())

        # Counter was cleared; 4 more failures still do not trigger lockout
        for _ in range(4):
            record_login_failure(request, "analista")
        self.assertFalse(is_login_throttled(request, "analista"))

    @override_settings(ARION_PRODUCTION=True)
    def test_client_ip_for_login_only_trusts_cf_connecting_ip_from_loopback_proxy(self):
        from_tunnel = self.factory.post("/login/", REMOTE_ADDR="127.0.0.1", HTTP_CF_CONNECTING_IP="203.0.113.45")
        self.assertEqual(client_ip_for_login(from_tunnel), "203.0.113.45")

        spoofed_direct = self.factory.post("/login/", REMOTE_ADDR="198.51.100.10", HTTP_CF_CONNECTING_IP="203.0.113.45")
        self.assertEqual(client_ip_for_login(spoofed_direct), "198.51.100.10")

    def test_formula_injection_helpers_neutralize_dangerous_prefixes(self):
        payloads = ("=SUM(1+1)", " +SUM(1+1)", "-SUM(1+1)", "@SUM(1+1)", "\t=SUM(1+1)")
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertTrue(str(_safe_csv_value(payload)).startswith("'"))
                self.assertTrue(str(_safe_spreadsheet_text(payload)).startswith("'"))
                self.assertTrue(str(_safe_cell(payload)).startswith("'"))
                self.assertTrue(str(_neutralize_formula(payload)).startswith("'"))

    def test_cmdb_https_validation_rejects_http_and_accepts_https(self):
        with self.assertRaises(IntegrationNotConfigured):
            CMDBClient(base_url="http://example.com/Webservice/", username="u", password="p", opener=Mock())
        client = CMDBClient(base_url="https://example.com/Webservice/", username="u", password="p", opener=Mock())
        self.assertTrue(client.base_url.startswith("https://"))

        bad_form = CMDBConfigurationForm(data={
            "base_url": "http://example.com/Webservice",
            "username": "u", "password": "p", "timeout": 15, "verify_tls": True, "batch_size": 50,
        })
        self.assertFalse(bad_form.is_valid())

        good_form = CMDBConfigurationForm(data={
            "base_url": "https://example.com/Webservice",
            "username": "u", "password": "p", "timeout": 15, "verify_tls": True, "batch_size": 50,
        })
        self.assertTrue(good_form.is_valid())

    def test_otrs_remote_host_requires_tls_fail_closed(self):
        client = OTRSReadOnlyClient(host="200.174.198.133", username="u", password="p", use_tls=False)
        with self.assertRaises(OTRSTLSRequiredError):
            client._build_ssl_context()

        bad_form = OTRSConfigurationForm(data={
            "host": "200.174.198.133", "port": 3306, "database": "otrs",
            "username": "u", "password": "p", "timeout": 10, "use_tls": False,
        })
        self.assertFalse(bad_form.is_valid())

    def test_ari_upload_rejects_dangerous_files_and_sanitizes_traversal(self):
        for name, content, mime in (
            ("xss.svg", b"<svg onload=alert(1)>", "image/svg+xml"),
            ("page.html", b"<script>alert(1)</script>", "text/html"),
            ("fake.pdf", b"not-a-pdf", "application/pdf"),
            ("invoice.pdf.exe", b"MZ\x90\x00", "application/octet-stream"),
        ):
            with self.subTest(filename=name):
                upload = SimpleUploadedFile(name, content, content_type=mime)
                with self.assertRaises(ValidationError):
                    validate_upload(upload)

        traversal = SimpleUploadedFile(r"..\..\relatorio.txt", b"linha segura\n", content_type="text/plain")
        validated = validate_upload(traversal)
        self.assertEqual(validated.original_name, "relatorio.txt")
