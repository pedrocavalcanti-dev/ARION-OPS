"""Validate the effective local production configuration without printing secrets."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from importlib.util import find_spec


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DJANGO_ROOT = PROJECT_ROOT / "arion_geo"
sys.path.insert(0, str(DJANGO_ROOT))


def say(ok: bool, label: str, detail: str = "") -> None:
    prefix = "OK" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{prefix}] {label}{suffix}")


def _strong_secret(value: str) -> bool:
    markers = ("replace_with", "change_me", "changeme", "example_secret")
    classes = sum((any(char.islower() for char in value), any(char.isupper() for char in value),
                   any(char.isdigit() for char in value), any(not char.isalnum() for char in value)))
    return (len(value) >= 50 and len(set(value)) >= 20 and classes >= 3
            and not any(marker in value.casefold() for marker in markers))


def _effective_env(name: str, file_values: dict[str, str | None]) -> str:
    return os.environ.get(name, file_values.get(name) or "").strip()


def main(*, build_only=False) -> int:
    try:
        from dotenv import dotenv_values
        file_values = dotenv_values(PROJECT_ROOT / ".env")
    except Exception:
        file_values = {}

    env_mode = _effective_env("ARION_ENV", file_values)
    say(env_mode.casefold() == "production", "ARION_ENV", "production" if env_mode.casefold() == "production" else "configure production no ambiente/.env")
    if env_mode.casefold() != "production":
        return 1

    raw_secret = _effective_env("DJANGO_SECRET_KEY", file_values)
    say(_strong_secret(raw_secret), "DJANGO_SECRET_KEY", "presente e com formato forte" if _strong_secret(raw_secret) else "ausente, fraca ou placeholder; valor omitido")
    if not _strong_secret(raw_secret):
        return 1

    raw_debug = _effective_env("DJANGO_DEBUG", file_values).casefold()
    debug_enabled = raw_debug in {"1", "true", "yes", "on"}
    say(not debug_enabled, "DEBUG", "False" if not debug_enabled else "deve ser false em produção")
    if debug_enabled:
        return 1

    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    try:
        import django
        django.setup()
        from django.conf import settings
        from django.core.checks import run_checks
    except Exception as exc:
        say(False, "settings Django", f"não carregadas ({type(exc).__name__}); detalhes omitidos")
        return 1

    failures = 0

    def check(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        say(ok, label, detail)
        failures += not ok

    check(settings.ARION_PRODUCTION, "modo Django de produção")
    check(settings.DEBUG is False, "DEBUG efetivo")
    hosts = [str(host).strip() for host in settings.ALLOWED_HOSTS]
    check(bool(hosts) and all(host and "*" not in host and "/" not in host and "@" not in host for host in hosts),
          "ALLOWED_HOSTS", "whitelist explícita" if hosts else "ausente/inválida")
    origins = list(settings.CSRF_TRUSTED_ORIGINS)
    origin_hosts = set()
    valid_origins = bool(origins)
    for origin in origins:
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        try:
            _ = parsed.port
        except ValueError:
            valid_origins = False
        if (parsed.scheme != "https" or not parsed.hostname or "*" in parsed.netloc
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            valid_origins = False
        elif parsed.hostname.casefold() not in {host.casefold() for host in hosts}:
            valid_origins = False
        if parsed.hostname:
            origin_hosts.add(parsed.hostname.casefold())
    check(valid_origins, "CSRF_TRUSTED_ORIGINS", "origens HTTPS exatas" if valid_origins else "ausente/inválida")

    database = settings.DATABASES["default"]
    db_host = str(database.get("HOST", "")).strip().casefold()
    db_user = str(database.get("USER", "")).strip().casefold()
    check(database.get("ENGINE") == "django.db.backends.mysql", "banco de produção", "MySQL")
    check(db_host in {"127.0.0.1", "localhost", "::1"}, "MySQL local", "host de loopback")
    check(bool(db_user) and db_user != "root", "usuário MySQL", "conta própria, não root")
    check(getattr(settings, "ARION_WEB_BIND", None) == "127.0.0.1", "bind Waitress", "127.0.0.1")
    check(find_spec("waitress") is not None, "Waitress", "dependência instalada")

    from config.frontend_checks import check_frontend_assets
    check_frontend_assets(settings, check, collected=not build_only)
    if build_only:
        say(failures == 0, "resultado preflight de build", "coleta e saúde dos serviços ainda não verificadas")
        return 0 if failures == 0 else 1

    log_root = PROJECT_ROOT / "var" / "logs"
    check(log_root.is_dir() and os.access(log_root, os.W_OK), "diretório de logs", "presente e gravável" if log_root.is_dir() and os.access(log_root, os.W_OK) else "crie/proteja var/logs")

    check(settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https"),
          "SECURE_PROXY_SSL_HEADER", "HTTP_X_FORWARDED_PROTO=https")
    check(settings.SECURE_SSL_REDIRECT is True, "HTTPS redirect", "ativo via proxy HTTPS confiável")
    check(settings.SESSION_COOKIE_SECURE is True and settings.CSRF_COOKIE_SECURE is True,
          "cookies Secure")
    check(settings.SESSION_COOKIE_HTTPONLY is True and settings.CSRF_COOKIE_HTTPONLY is False,
          "cookies HttpOnly", "sessão protegida; CSRF legível pelo AJAX same-origin")
    check(settings.SECURE_HSTS_SECONDS >= 3600, "HSTS max-age", f"{settings.SECURE_HSTS_SECONDS}s")
    check(settings.SECURE_HSTS_PRELOAD is False and settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is False,
          "HSTS gradual", "sem preload/includeSubDomains automáticos")
    check(bool(settings.ARION_CSP_REPORT_ONLY), "CSP", "Report-Only")

    for message in run_checks(include_deployment_checks=True):
        level = "FAIL" if message.is_serious() else "WARN"
        print(f"[{level}] Django {message.id}: {message.msg}")
        failures += message.is_serious()

    say(failures == 0, "resultado production-check", "sem blocker conhecido" if failures == 0 else f"{failures} controle(s) falharam")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true", help="Validar build antes da parada; não verifica collectstatic/saúde.")
    raise SystemExit(main(build_only=parser.parse_args().build_only))
