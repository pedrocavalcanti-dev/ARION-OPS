"""Request correlation and API error response normalization."""

import secrets
import logging

from django.conf import settings
from django.shortcuts import redirect, resolve_url
from django.urls import reverse

from .errors import _safe_user_id, error_response, html_error, is_api_request


STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "AUTHENTICATION_REQUIRED",
    403: "ACCESS_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "INTEGRATION_UNAVAILABLE",
    503: "INTEGRATION_UNAVAILABLE",
    504: "INTEGRATION_TIMEOUT",
}
logger = logging.getLogger("config.errors")

SENSITIVE_AUTH_PATHS = ("/ativacao/", "/redefinir-senha/")


class RequestErrorMiddleware:
    """Attach a non-secret request reference and standardize API failures."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = f"REQ-{secrets.token_hex(8).upper()}"
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        if not getattr(response, "_arion_error_response", False):
            status = response.status_code
            api_request = is_api_request(request)
            is_login_redirect = False
            if api_request and status in (301, 302, 303, 307, 308):
                login_path = resolve_url(settings.LOGIN_URL).rstrip("/")
                destination = response.get("Location", "").split("?", 1)[0].rstrip("/")
                is_login_redirect = destination == login_path
            if is_login_redirect:
                response = error_response(request, "AUTHENTICATION_REQUIRED")
            elif settings.DEBUG and not api_request and status >= 500:
                # Keep Django's technical HTML page available for local debugging.
                pass
            elif status == 405 or (api_request and status >= 400) or (not api_request and status >= 400 and status != 400):
                headers = {}
                for name in ("Allow", "Retry-After"):
                    if response.has_header(name):
                        headers[name] = response[name]
                code = STATUS_CODES.get(status, "INTERNAL_ERROR" if status >= 500 else "BAD_REQUEST")
                if status >= 500:
                    logger.error(
                        "HTTP error response status=%s request_id=%s user_id=%s method=%s path=%s",
                        status, request.request_id, _safe_user_id(request), request.method,
                        "<sensitive-auth-flow>" if request.path_info.startswith(SENSITIVE_AUTH_PATHS) else request.path_info,
                    )
                response = error_response(request, code, status=status, headers=headers) if api_request else html_error(request, code, status=status, headers=headers)

        response["X-Request-ID"] = request.request_id
        return response


class ProductionSecurityHeadersMiddleware:
    """Apply conservative browser policy and prevent shared caching of user data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", settings.PERMISSIONS_POLICY)

        content_type = response.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if settings.ARION_CSP_REPORT_ONLY and content_type == "text/html":
            response.setdefault("Content-Security-Policy-Report-Only", settings.CONTENT_SECURITY_POLICY_REPORT_ONLY)

        user = getattr(request, "user", None)
        is_authenticated = bool(user and user.is_authenticated)
        static_prefix = settings.STATIC_URL.rstrip("/") + "/"
        is_static = request.path_info.startswith(static_prefix)
        if request.path_info == "/service-worker.js":
            response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif request.path_info == "/manifest.webmanifest":
            response["Cache-Control"] = "public, max-age=300, must-revalidate"
        elif is_authenticated or not is_static:
            response["Cache-Control"] = "private, no-store"
        if request.path_info.startswith(SENSITIVE_AUTH_PATHS):
            response["Referrer-Policy"] = "no-referrer"
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
        return response


class PasswordPolicyMiddleware:
    """Restrict authenticated users with pending password renewal to its recovery flow."""

    allowed_paths = (
        "/login/", "/logout/", "/ativacao/", "/redefinir-senha/", "/senha/obrigatoria/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path_info
        static_prefix = settings.STATIC_URL.rstrip("/") + "/"
        if (
            user and user.is_authenticated
            and not path.startswith(self.allowed_paths)
            and not path.startswith(static_prefix)
        ):
            from aplicativos.autenticacao.security import password_change_required

            if password_change_required(user):
                if is_api_request(request):
                    return error_response(request, "PASSWORD_CHANGE_REQUIRED")
                return redirect(reverse("autenticacao:required-password"))
        return self.get_response(request)
