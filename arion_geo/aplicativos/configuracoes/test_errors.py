from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
import json

from config.errors import api_error, handler500


class GlobalErrorHandlingTests(TestCase):
    @override_settings(DEBUG=False)
    def test_server_error_records_exception_identity_without_secret_message(self):
        request = RequestFactory().get("/chamados/?token=do-not-log")
        request.request_id = "REQ-HOTFIX-TEST"
        with self.assertLogs("config.errors", level="ERROR") as logged:
            try:
                raise RuntimeError("password=do-not-log; SELECT private_value")
            except RuntimeError:
                response = handler500(request)
        self.assertEqual(response.status_code, 500)
        output = " ".join(logged.output)
        self.assertIn("exception_type=RuntimeError", output)
        self.assertIn("REQ-HOTFIX-TEST", output)
        self.assertIn("test_server_error_records_exception_identity", output)
        self.assertNotIn("do-not-log", output)
        self.assertNotIn("SELECT", output)

    @override_settings(DEBUG=False)
    def test_database_error_code_is_logged_without_sql_or_exception_message(self):
        request = RequestFactory().get("/chamados/")
        request.request_id = "REQ-HOTFIX-DATABASE"
        with self.assertLogs("config.errors", level="ERROR") as logged:
            try:
                raise OperationalError(1055, "SELECT sensitive_column password=do-not-log")
            except OperationalError:
                response = handler500(request)
        output = " ".join(logged.output)
        self.assertIn("exception_type=OperationalError", output)
        self.assertIn("error_code=1055", output)
        self.assertNotIn("sensitive_column", output)
        self.assertNotIn("do-not-log", output)
        self.assertNotIn("OperationalError", response.content.decode())

    @override_settings(DEBUG=False)
    def test_unknown_html_page_uses_safe_404_template(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "NOT_FOUND", status_code=404)
        self.assertContains(response, "Página não encontrada", status_code=404)
        self.assertNotContains(response, "urlpatterns", status_code=404)
        self.assertTrue(response["X-Request-ID"].startswith("REQ-"))

    @override_settings(DEBUG=False)
    def test_unknown_api_url_returns_standard_json_404(self):
        response = self.client.get("/api/no-such-endpoint/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    @override_settings(DEBUG=False, ROOT_URLCONF="config.error_test_urls")
    def test_api_bad_request_handler_returns_json(self):
        response = self.client.get("/api/bad-request/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "BAD_REQUEST")

    @override_settings(DEBUG=False, ROOT_URLCONF="config.error_test_urls")
    def test_bad_request_and_forbidden_handlers_hide_exception_details(self):
        bad_request = self.client.get("/bad-request/")
        self.assertEqual(bad_request.status_code, 400)
        self.assertContains(bad_request, "BAD_REQUEST", status_code=400)
        self.assertNotContains(bad_request, "private parser detail", status_code=400)

        forbidden = self.client.get("/forbidden/")
        self.assertEqual(forbidden.status_code, 403)
        self.assertContains(forbidden, "ACCESS_DENIED", status_code=403)
        self.assertNotContains(forbidden, "private permission codename", status_code=403)

    @override_settings(DEBUG=False, ROOT_URLCONF="config.error_test_urls")
    def test_405_on_api_route_is_standard_json_and_preserves_allow_header(self):
        response = self.client.get("/api/post-only/")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")
        self.assertIn("POST", response["Allow"])

    @override_settings(DEBUG=False, ROOT_URLCONF="config.error_test_urls")
    def test_csrf_error_is_safe_html(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/csrf-only/")
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "CSRF_FAILED", status_code=403)
        self.assertContains(response, "Sua sessão não pôde ser validada", status_code=403)
        self.assertNotContains(response, "CSRF cookie", status_code=403)

    @override_settings(DEBUG=False, ROOT_URLCONF="config.error_test_urls")
    def test_unhandled_html_and_api_500_are_safe_and_have_request_reference(self):
        client = Client(raise_request_exception=False)
        html = client.get("/explode/")
        self.assertEqual(html.status_code, 500)
        self.assertContains(html, "INTERNAL_ERROR", status_code=500)
        self.assertContains(html, "Referência do erro:", status_code=500)
        self.assertNotContains(html, "SECRET_KEY", status_code=500)
        self.assertNotContains(html, "internal_table", status_code=500)
        self.assertNotContains(html, "Traceback", status_code=500)

        api = client.get("/api/explode/")
        self.assertEqual(api.status_code, 500)
        self.assertEqual(api["Content-Type"], "application/json")
        self.assertEqual(api.json()["error"]["code"], "INTERNAL_ERROR")
        self.assertTrue(api.json()["request_id"].startswith("REQ-"))
        self.assertNotIn("SECRET_KEY", api.content.decode())
        self.assertNotIn("internal_table", api.content.decode())

    def test_api_errors_support_future_statuses_with_the_standard_shape(self):
        request = RequestFactory().post("/api/future/")
        for code, status in (
            ("CONFLICT", 409), ("FILE_TOO_LARGE", 413), ("UNSUPPORTED_MEDIA_TYPE", 415),
            ("VALIDATION_ERROR", 422), ("RATE_LIMITED", 429),
            ("INTEGRATION_UNAVAILABLE", 503), ("INTEGRATION_TIMEOUT", 504),
        ):
            with self.subTest(code=code):
                response = api_error(request, code)
                self.assertEqual(response.status_code, status)
                body = json.loads(response.content)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error"]["code"], code)
                self.assertEqual(body["error"]["status"], status)

    def test_web_unauthenticated_request_still_redirects_to_login(self):
        response = self.client.get(reverse("rankings:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("autenticacao:login"), response["Location"])

    @override_settings(DEBUG=True)
    @override_settings(ROOT_URLCONF="config.error_test_urls")
    def test_debug_mode_keeps_django_diagnostic_500(self):
        response = Client(raise_request_exception=False).get("/explode/")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "SECRET_KEY", status_code=500)

        api = Client(raise_request_exception=False).get("/api/explode/")
        self.assertEqual(api.status_code, 500)
        self.assertEqual(api["Content-Type"], "application/json")
        self.assertNotIn("SECRET_KEY", api.content.decode())

    def test_authenticated_user_without_permission_receives_real_403(self):
        user = get_user_model().objects.create_user(username="error-no-permission", password="Senha-segura-123")
        self.client.force_login(user)
        response = self.client.get(reverse("rankings:index"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "ACCESS_DENIED", status_code=403)
        self.assertNotContains(response, "view_rankings", status_code=403)
