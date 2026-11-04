"""Small, fixed-endpoint Brevo client. No API response bodies are logged or exposed."""
from dataclasses import dataclass
import json
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


BREVO_HEALTH_URL = "https://api.brevo.com/v3/smtp/statistics/aggregatedReport?days=1"
BREVO_HEALTH_RESOURCE = "smtp/statistics/aggregatedReport"
BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
DEFAULT_TIMEOUT = 10
KNOWN_BREVO_ERROR_CODES = frozenset({
    "invalid_parameter", "missing_parameter", "out_of_range", "unauthorized",
    "document_not_found", "method_not_allowed", "not_enough_credits",
    "duplicate_parameter", "duplicate_request", "account_under_validation",
    "permission_denied",
})


class _BlockRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_url(request, *, timeout):
    # Never forward the API key to a host selected by an HTTP redirect.
    return build_opener(_BlockRedirects()).open(request, timeout=timeout)


def _safe_http_error_details(error):
    """Read only the documented error code; never retain or expose Brevo's message/body."""
    try:
        body = error.read(8192)
    except Exception:
        return "", False
    finally:
        try:
            error.close()
        except Exception:
            pass
    try:
        payload = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeError, ValueError):
        return "", False
    if not isinstance(payload, dict):
        return "", False

    raw_code = payload.get("code")
    code = str(raw_code).strip().lower() if isinstance(raw_code, str) else ""
    if code not in KNOWN_BREVO_ERROR_CODES:
        code = ""

    # Classify explicit IP restrictions from the body, then discard its text.
    raw_message = payload.get("message")
    text = " ".join(raw_message.casefold().split()) if isinstance(raw_message, str) else ""
    mentions_ip = bool(re.search(r"\bip(?:\s+address)?\b", text))
    blocked = any(term in text for term in (
        "not authorized", "not allowed", "not whitelisted", "not allowlisted",
        "blocked", "restricted", "whitelist", "allowlist",
    ))
    return code, mentions_ip and blocked


def _connection_http_error_message(status, code, ip_restricted):
    if status == 401:
        description = "API Key inválida ou não reconhecida."
    elif status == 403 and ip_restricted:
        description = f"Acesso bloqueado pela política de IP no recurso {BREVO_HEALTH_RESOURCE}."
    elif status == 403:
        description = f"Permissão insuficiente para o recurso {BREVO_HEALTH_RESOURCE}."
    elif status == 429:
        description = "Limite de requisições atingido; tente novamente mais tarde."
    elif status == 402:
        description = "A conta Brevo requer ativação ou créditos adicionais."
    elif status is not None and status >= 500:
        description = "Serviço Brevo indisponível no momento."
    else:
        description = "A Brevo recusou o teste de autenticação."
    status_text = f"HTTP {status} — " if status is not None else "Falha HTTP — "
    code_text = f" Código Brevo: {code}." if code else ""
    return f"{status_text}{description}{code_text}"


@dataclass(frozen=True)
class BrevoResult:
    ok: bool
    message: str
    latency_ms: int | None = None
    message_id: str = ""
    uncertain: bool = False
    status_code: int | None = None
    error_code: str = ""


class BrevoSendingDisabled(RuntimeError):
    """Raised until the dedicated TQ send flow is approved and enabled."""


class BrevoTransactionalClient:
    def __init__(self, api_key, *, timeout=DEFAULT_TIMEOUT):
        self.api_key = str(api_key or "").strip()
        self.timeout = max(1, min(int(timeout), 30))
        if not self.api_key:
            raise ValueError("Credencial Brevo não configurada.")

    def _request(self, url, *, method="GET", payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {
            "api-key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        return Request(url, data=data, headers=headers, method=method)

    def test_connection(self):
        started = time.perf_counter()
        status = None
        try:
            with _open_url(self._request(BREVO_HEALTH_URL), timeout=self.timeout) as response:
                status = response.getcode()
            latency = max(0, int((time.perf_counter() - started) * 1000))
            if status == 200:
                return BrevoResult(True, "HTTP 200 — API de e-mails transacionais autenticada.", latency,
                                   status_code=status)
            message = f"HTTP {status} — resposta inesperada no recurso {BREVO_HEALTH_RESOURCE}."
            return BrevoResult(False, message, latency, status_code=status)
        except HTTPError as exc:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            code, ip_restricted = _safe_http_error_details(exc)
            status = exc.code if isinstance(exc.code, int) else None
            message = _connection_http_error_message(status, code, ip_restricted)
            return BrevoResult(False, message, latency, status_code=status, error_code=code)
        except TimeoutError:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            return BrevoResult(False, "Timeout — tempo limite ao conectar à Brevo.", latency)
        except URLError as exc:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return BrevoResult(False, "Timeout — tempo limite ao conectar à Brevo.", latency)
            return BrevoResult(False, "Serviço Brevo indisponível no momento.", latency)
        except Exception:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            message = (
                f"HTTP {status} — falha segura ao interpretar a resposta da Brevo."
                if isinstance(status, int) else "Falha temporária ao testar a conexão Brevo."
            )
            return BrevoResult(False, message, latency, status_code=status)

    @staticmethod
    def build_transactional_payload(*, sender_email, sender_name, to_email, subject,
                                    html_content="", text_content="", reply_to="", cc_email="", tags=None):
        values = (sender_email, to_email)
        try:
            for email in values:
                validate_email(str(email or "").strip())
        except ValidationError as exc:
            raise ValueError("Informe e-mails válidos para remetente e destinatário.") from exc
        if not str(subject or "").strip():
            raise ValueError("Informe o assunto da mensagem.")
        if not str(html_content or "").strip() and not str(text_content or "").strip():
            raise ValueError("Informe o conteúdo HTML ou texto da mensagem.")
        payload = {
            "sender": {"email": str(sender_email).strip(), "name": str(sender_name or "").strip()},
            "to": [{"email": str(to_email).strip()}],
            "subject": str(subject).strip(),
        }
        if cc_email:
            try:
                validate_email(str(cc_email).strip())
            except ValidationError as exc:
                raise ValueError("Informe um e-mail em cópia válido.") from exc
            payload["cc"] = [{"email": str(cc_email).strip()}]
        if html_content:
            payload["htmlContent"] = str(html_content)
        if text_content:
            payload["textContent"] = str(text_content)
        if reply_to:
            try:
                validate_email(str(reply_to).strip())
            except ValidationError as exc:
                raise ValueError("Informe um e-mail de resposta válido.") from exc
            payload["replyTo"] = {"email": str(reply_to).strip()}
        if tags:
            payload["tags"] = [str(tag)[:100] for tag in tags[:10] if str(tag).strip()]
        return payload

    def send_transactional(self, payload, *, allow_real_sends=False):
        """Send one application-composed message when the caller is explicitly authorized."""
        if not allow_real_sends:
            raise BrevoSendingDisabled("Envios reais estão desabilitados nesta etapa.")
        started = time.perf_counter()
        try:
            with _open_url(self._request(BREVO_SEND_URL, method="POST", payload=payload), timeout=self.timeout) as response:
                status = response.getcode()
                body = json.loads(response.read().decode("utf-8"))
                latency = max(0, int((time.perf_counter() - started) * 1000))
                message_id = str(body.get("messageId", ""))[:250] if isinstance(body, dict) else ""
                if 200 <= status < 300 and message_id:
                    return BrevoResult(True, "Solicitação aceita pela Brevo.", latency, message_id, status_code=status)
                return BrevoResult(False, "A resposta da Brevo não confirmou o identificador da mensagem.", latency,
                                   uncertain=200 <= status < 300, status_code=status)
        except HTTPError as exc:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            uncertain = exc.code >= 500 or exc.code in (408, 425)
            message = (
                "Serviço Brevo indisponível; resultado do envio pode ser incerto."
                if uncertain else "A Brevo recusou a solicitação de envio."
            )
            return BrevoResult(False, message, latency, uncertain=uncertain, status_code=exc.code)
        except (TimeoutError, URLError):
            latency = max(0, int((time.perf_counter() - started) * 1000))
            return BrevoResult(False, "Falha de comunicação; resultado do envio pode ser incerto.", latency, uncertain=True)
        except Exception:
            latency = max(0, int((time.perf_counter() - started) * 1000))
            return BrevoResult(False, "Falha ao interpretar a resposta; resultado do envio pode ser incerto.", latency, uncertain=True)
