"""Shared, safe responses for web errors and JSON endpoints."""

from dataclasses import asdict, dataclass
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch, reverse


logger = logging.getLogger("config.errors")


@dataclass(frozen=True)
class ErrorDefinition:
    status_code: int
    code: str
    title: str
    message: str
    level: int = logging.WARNING
    show_retry: bool = False
    show_home: bool = True
    show_login: bool = False


ERRORS = {
    "BAD_REQUEST": ErrorDefinition(400, "BAD_REQUEST", "Não foi possível processar a solicitação", "A requisição enviada é inválida ou está incompleta."),
    "AUTHENTICATION_REQUIRED": ErrorDefinition(401, "AUTHENTICATION_REQUIRED", "Autenticação necessária", "Entre novamente para continuar.", show_home=False, show_login=True),
    "ACCESS_DENIED": ErrorDefinition(403, "ACCESS_DENIED", "Acesso não permitido", "Você não possui permissão para acessar esta área ou executar esta ação."),
    "PASSWORD_CHANGE_REQUIRED": ErrorDefinition(403, "PASSWORD_CHANGE_REQUIRED", "Atualização de senha necessária", "Confirme sua identidade e atualize a senha para voltar a acessar os módulos."),
    "NOT_FOUND": ErrorDefinition(404, "NOT_FOUND", "Página não encontrada", "O endereço pode estar incorreto, ter sido alterado ou não existir."),
    "METHOD_NOT_ALLOWED": ErrorDefinition(405, "METHOD_NOT_ALLOWED", "Ação não permitida", "Este endereço não aceita o tipo de operação enviado."),
    "REQUEST_TIMEOUT": ErrorDefinition(408, "REQUEST_TIMEOUT", "A solicitação demorou mais que o esperado", "Tente novamente.", show_retry=True),
    "CONFLICT": ErrorDefinition(409, "CONFLICT", "Não foi possível concluir a operação", "O recurso foi alterado ou está em um estado incompatível."),
    "FILE_TOO_LARGE": ErrorDefinition(413, "FILE_TOO_LARGE", "Arquivo muito grande", "O arquivo enviado ultrapassa o limite permitido."),
    "ATTACHMENT_TOO_LARGE": ErrorDefinition(413, "ATTACHMENT_TOO_LARGE", "Arquivo muito grande", "O arquivo enviado ultrapassa o limite permitido."),
    "UNSUPPORTED_MEDIA_TYPE": ErrorDefinition(415, "UNSUPPORTED_MEDIA_TYPE", "Formato não suportado", "O formato enviado não é aceito neste endereço."),
    "VALIDATION_ERROR": ErrorDefinition(422, "VALIDATION_ERROR", "Revise os dados enviados", "Um ou mais dados precisam ser corrigidos."),
    "RATE_LIMITED": ErrorDefinition(429, "RATE_LIMITED", "Muitas solicitações", "Aguarde alguns instantes antes de tentar novamente.", show_retry=True),
    "INTERNAL_ERROR": ErrorDefinition(500, "INTERNAL_ERROR", "Não foi possível concluir esta operação", "Ocorreu um erro interno. Tente novamente em alguns instantes.", logging.ERROR, show_retry=True),
    "EXPORT_FAILED": ErrorDefinition(500, "EXPORT_FAILED", "Não foi possível gerar a exportação", "Não foi possível gerar a exportação.", logging.ERROR, show_retry=True),
    "SERVICE_UNAVAILABLE": ErrorDefinition(503, "SERVICE_UNAVAILABLE", "Serviço temporariamente indisponível", "O Arion está temporariamente indisponível. Tente novamente em alguns instantes.", logging.ERROR, show_retry=True),
    "BAD_GATEWAY": ErrorDefinition(502, "BAD_GATEWAY", "Não foi possível acessar um serviço necessário", "Não foi possível acessar um serviço necessário.", logging.ERROR, show_retry=True),
    "GATEWAY_TIMEOUT": ErrorDefinition(504, "GATEWAY_TIMEOUT", "A fonte de dados demorou a responder", "Um serviço demorou mais que o esperado para responder.", logging.ERROR, show_retry=True),
    "INTEGRATION_UNAVAILABLE": ErrorDefinition(503, "INTEGRATION_UNAVAILABLE", "Fonte de dados indisponível", "Não foi possível consultar a fonte de dados neste momento.", logging.ERROR, show_retry=True),
    "INTEGRATION_TIMEOUT": ErrorDefinition(504, "INTEGRATION_TIMEOUT", "A fonte de dados demorou a responder", "Um serviço necessário demorou mais que o esperado para responder.", logging.ERROR, show_retry=True),
    "INTEGRATION_AUTH_ERROR": ErrorDefinition(502, "INTEGRATION_AUTH_ERROR", "Não foi possível acessar um serviço necessário", "A consulta a um serviço necessário não pôde ser concluída.", logging.ERROR, show_retry=True),
    "INTEGRATION_INVALID_RESPONSE": ErrorDefinition(502, "INTEGRATION_INVALID_RESPONSE", "Não foi possível acessar um serviço necessário", "Um serviço necessário retornou uma resposta inválida.", logging.ERROR, show_retry=True),
    "AI_UNAVAILABLE": ErrorDefinition(503, "AI_UNAVAILABLE", "Assistente temporariamente indisponível", "O assistente não está disponível neste momento.", logging.ERROR, show_retry=True),
    "AI_LIMIT_REACHED": ErrorDefinition(429, "AI_LIMIT_REACHED", "Limite de solicitações atingido", "Aguarde alguns instantes antes de tentar novamente.", show_retry=True),
    "AI_REQUEST_FAILED": ErrorDefinition(502, "AI_REQUEST_FAILED", "Não foi possível concluir a solicitação", "O assistente não conseguiu concluir esta solicitação.", logging.ERROR, show_retry=True),
    "ATTACHMENT_INVALID": ErrorDefinition(400, "ATTACHMENT_INVALID", "Arquivo inválido", "Verifique o formato e o conteúdo do arquivo enviado."),
    "CSRF_FAILED": ErrorDefinition(403, "CSRF_FAILED", "Sua sessão não pôde ser validada", "Atualize a página e tente novamente. Se o problema continuar, entre novamente no sistema.", show_retry=True, show_login=True),
}
FALLBACK_4XX = ErrorDefinition(400, "BAD_REQUEST", "Não foi possível concluir a solicitação", "Não foi possível concluir a solicitação.")
FALLBACK_5XX = ERRORS["INTERNAL_ERROR"]

# Names identify endpoints by their resolved route, rather than by a client
# supplied Accept header. Path prefixes also cover unknown URLs below API roots.
API_ROUTE_NAMES = {
    "analytics-query", "analytics-export", "analytics-saved-views",
    "preferences_api:preferences", "preferences_api:theme",
    "base_links:provider-detail", "base_links:detail", "base_links:map-aggregates",
    "rankings:provider-detail",
    "ari:create-message", "ari:conversation-detail", "ari:send-message",
    "ari:rename", "ari:delete",
}
API_PATH_PREFIXES = ("/api/", "/ari/api/", "/base/api/")


def is_api_request(request):
    match = getattr(request, "resolver_match", None)
    if match is not None:
        qualified_name = ":".join((*match.namespaces, match.url_name or ""))
        if qualified_name in API_ROUTE_NAMES or match.url_name in API_ROUTE_NAMES:
            return True
    path = getattr(request, "path_info", "")
    return path.startswith(API_PATH_PREFIXES) or path.startswith("/rankings/provedor/")


def _request_id(request):
    return getattr(request, "request_id", "")


def _safe_user_id(request):
    try:
        user = request.user
        return user.pk if user.is_authenticated else "anon"
    except Exception:  # An unavailable database must not break the error page.
        return "unknown"


def _internal_url(value, fallback):
    try:
        return reverse(value) if value == "home" else resolve_url(value)
    except NoReverseMatch:
        return fallback


def _definition(code=None, status=None):
    definition = ERRORS.get(code) if code else None
    if definition is None and status is not None:
        definition = next((item for item in ERRORS.values() if item.status_code == status), None)
    if definition is None:
        definition = FALLBACK_5XX if status is not None and status >= 500 else FALLBACK_4XX
    return definition


def error_response(request, code, *, fields=None, status=None, headers=None):
    error = _definition(code, status)
    http_status = status or error.status_code
    body_error = {"code": error.code, "message": error.message, "status": http_status}
    if fields:
        body_error["fields"] = fields
    body = {"ok": False, "error": body_error}
    reference = _request_id(request)
    if http_status >= 500 and reference:
        body["request_id"] = reference
    response = JsonResponse(body, status=http_status)
    response._arion_error_response = True
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def html_error(request, code, *, status=None, headers=None):
    definition = _definition(code, status)
    http_status = status or definition.status_code
    context = {
        "error": asdict(definition),
        "request_id": _request_id(request),
        "request_id_short": _request_id(request).removeprefix("REQ-") if _request_id(request) else "",
        "home_url": _internal_url("home", "/"),
        "login_url": _internal_url(settings.LOGIN_URL, "/login/"),
    }
    # Use a plain template Context: error pages must not call context processors,
    # some of which query the database or depend on request-specific services.
    response = HttpResponse(render_to_string("errors/error.html", context), status=http_status)
    response._arion_error_response = True
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def responder(request, code, *, status=None, fields=None, headers=None):
    if is_api_request(request):
        return error_response(request, code, status=status, fields=fields, headers=headers)
    return html_error(request, code, status=status, headers=headers)


def handler400(request, exception=None):
    from django.core.exceptions import RequestDataTooBig

    code = "FILE_TOO_LARGE" if isinstance(exception, RequestDataTooBig) else "BAD_REQUEST"
    return responder(request, code)


def handler403(request, exception=None):
    return responder(request, "ACCESS_DENIED")


def handler404(request, exception=None):
    return responder(request, "NOT_FOUND")


def handler500(request):
    reference = _request_id(request) or "indisponível"
    user_id = _safe_user_id(request)
    logger.error(
        "Unhandled server error request_id=%s user_id=%s method=%s path=%s",
        reference, user_id, request.method, request.path_info,
    )
    return responder(request, "INTERNAL_ERROR")


def csrf_failure(request, reason=""):
    reference = _request_id(request) or "indisponível"
    user_id = _safe_user_id(request)
    logger.warning(
        "CSRF validation failed request_id=%s user_id=%s method=%s path=%s",
        reference, user_id, request.method, request.path_info,
    )
    return responder(request, "CSRF_FAILED")


def api_error(request, code, *, fields=None, status=None, headers=None):
    return error_response(request, code, fields=fields, status=status, headers=headers)


def require_media_type(request, *allowed_types):
    if request.content_type not in allowed_types:
        return api_error(request, "UNSUPPORTED_MEDIA_TYPE")
    return None

