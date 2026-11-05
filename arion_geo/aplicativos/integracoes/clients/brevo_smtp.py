"""Brevo SMTP relay operations. Connection tests authenticate but never send mail."""
from dataclasses import dataclass
import smtplib
import socket
import ssl
import time
import logging

from django.core.mail import EmailMultiAlternatives, get_connection


BREVO_SMTP_HOST = "smtp-relay.brevo.com"
BREVO_SMTP_PORT = 587
SMTP_TIMEOUT_SECONDS = 15
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMTPResult:
    ok: bool
    message: str
    latency_ms: int | None = None
    error_code: int | None = None
    uncertain: bool = False
    exception_class: str = ""
    stage: str = ""


def _safe_error(exc, *, stage="smtp_authentication"):
    detail = getattr(exc, "smtp_error", b"")
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", errors="ignore")
    detail = str(detail).casefold()
    code = getattr(exc, "smtp_code", None)
    if isinstance(exc, smtplib.SMTPAuthenticationError) or code == 535:
        return "SMTP 535 \u2014 Login ou chave rejeitados."
    if code == 525:
        return "SMTP 525 \u2014 Possivel bloqueio de IP; verifique a autorizacao na conta Brevo."
    if "ip" in detail and any(term in detail for term in ("block", "allow", "authoriz", "whitelist", "blacklist")):
        return "Acesso SMTP bloqueado pela politica de IP."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "Timeout na conexao SMTP."
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "Falha ao negociar STARTTLS obrigatorio."
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "Falha na validacao do certificado TLS SMTP."
    if isinstance(exc, ssl.SSLError):
        return "Falha na negociacao TLS da conexao SMTP."
    if isinstance(exc, (ConnectionRefusedError, OSError)):
        return "Falha de conectividade SMTP."
    if isinstance(exc, smtplib.SMTPResponseException) and code:
        return f"SMTP {int(code)} \u2014 Resposta do servidor nao classificada."
    if stage == "message_build":
        return "Falha ao preparar a mensagem de e-mail."
    if stage == "connection_setup":
        return "Falha ao preparar a conexao SMTP."
    if stage == "smtp_send":
        return "Falha durante a transmissao SMTP."
    return "Falha durante a autenticacao SMTP."


def _smtp_error_result(exc, started):
    return SMTPResult(
        False,
        _safe_error(exc),
        max(0, round((time.monotonic() - started) * 1000)),
        getattr(exc, "smtp_code", None),
        exception_class=type(exc).__name__,
        stage="smtp_authentication",
    )


def test_brevo_smtp_auth(username, password, *, backend_factory=get_connection):
    """Authenticate through Django's SMTP backend open()/close(); never sends mail."""
    started = time.monotonic()
    backend = None
    try:
        backend = backend_factory(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=BREVO_SMTP_HOST,
            port=BREVO_SMTP_PORT,
            username=username,
            password=password,
            use_tls=True,
            use_ssl=False,
            timeout=SMTP_TIMEOUT_SECONDS,
        )
        if not backend.open():
            return SMTPResult(False, "Nao foi possivel abrir a conexao SMTP.",
                              max(0, round((time.monotonic() - started) * 1000)))
        return SMTPResult(True, "Autenticacao SMTP aceita; nenhum e-mail foi enviado.",
                          max(0, round((time.monotonic() - started) * 1000)))
    except Exception as exc:
        return _smtp_error_result(exc, started)
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass


def send_brevo_smtp(*, username, password, config, subject, text_content, html_content):
    """Send one authorized TQ and retain only sanitized failure diagnostics."""
    started = time.monotonic()
    stage = "connection_setup"
    try:
        connection = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=BREVO_SMTP_HOST, port=BREVO_SMTP_PORT, username=username,
            password=password, use_tls=True, use_ssl=False,
            timeout=SMTP_TIMEOUT_SECONDS,
        )
        stage = "message_build"
        from_email = f'{config.get("sender_name") or "ARION OPS"} <{config["sender_email"]}>'
        message = EmailMultiAlternatives(
            subject=subject, body=text_content, from_email=from_email,
            to=[config["quality_recipient"]],
            cc=[config["quality_cc"]] if config.get("quality_cc") else None,
            reply_to=[config["reply_to"]] if config.get("reply_to") else None,
            connection=connection,
        )
        message.encoding = "utf-8"
        message.attach_alternative(html_content, "text/html")
        stage = "smtp_send"
        accepted = message.send(fail_silently=False) == 1
        return SMTPResult(accepted, "Servidor SMTP aceitou a mensagem." if accepted else "Servidor SMTP recusou a mensagem.",
                          max(0, round((time.monotonic() - started) * 1000)),
                          uncertain=False, stage=stage)
    except smtplib.SMTPAuthenticationError as exc:
        result = SMTPResult(False, _safe_error(exc, stage="smtp_authentication"),
                            max(0, round((time.monotonic() - started) * 1000)),
                            getattr(exc, "smtp_code", None), uncertain=False,
                            exception_class=type(exc).__name__, stage="smtp_authentication")
    except (smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused,
            smtplib.SMTPDataError) as exc:
        result = SMTPResult(False, _safe_error(exc, stage=stage),
                            max(0, round((time.monotonic() - started) * 1000)),
                            getattr(exc, "smtp_code", None), uncertain=False,
                            exception_class=type(exc).__name__, stage=stage)
    except Exception as exc:
        result = SMTPResult(False, _safe_error(exc, stage=stage),
                            max(0, round((time.monotonic() - started) * 1000)),
                            getattr(exc, "smtp_code", None),
                            uncertain=(stage == "smtp_send"),
                            exception_class=type(exc).__name__, stage=stage)
    logger.warning("Brevo SMTP failure stage=%s exception=%s code=%s message=%s uncertain=%s",
                   result.stage, result.exception_class, result.error_code,
                   result.message, result.uncertain)
    return result
