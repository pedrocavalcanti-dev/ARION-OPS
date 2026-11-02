"""Read-only security baseline verification command for ARION OPS releases."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from config.releases import current_version


class Command(BaseCommand):
    help = "Executa verificacoes somente leitura da baseline de seguranca do ARION OPS."

    def handle(self, *args, **options):
        version = current_version()
        env_name = getattr(settings, "ARION_ENV", "unknown")
        is_prod = bool(getattr(settings, "ARION_PRODUCTION", False))

        self.stdout.write("=================================================================")
        self.stdout.write(f" ARION OPS - Security Baseline (v{version} | env={env_name})")
        self.stdout.write("=================================================================")

        def report(status: str, label: str, detail: str = "") -> None:
            suffix = f" - {detail}" if detail else ""
            self.stdout.write(f"[{status}] {label}{suffix}")

        # 1. DEBUG
        if is_prod:
            report("OK" if not settings.DEBUG else "FAIL", "DEBUG desabilitado em producao", f"DEBUG={settings.DEBUG}")
        else:
            report("OK", "Ambiente local de desenvolvimento", f"DEBUG={settings.DEBUG} (HTTP em loopback permitido)")

        # 2. SECRET_KEY
        dev_marker = "django-insecure-"
        secret_key = getattr(settings, "SECRET_KEY", "") or ""
        is_external_secret = len(secret_key) >= 50 and not secret_key.startswith(dev_marker)
        if is_prod:
            report("OK" if is_external_secret else "FAIL", "SECRET_KEY forte e externa", "configurada (valor omitido)")
        else:
            report(
                "OK" if is_external_secret else "INFO",
                "SECRET_KEY",
                "chave externa carregada (valor omitido)" if is_external_secret else "chave de desenvolvimento local em uso",
            )

        # 3. Bind Waitress
        bind_host = getattr(settings, "ARION_WEB_BIND", "")
        report("OK" if bind_host == "127.0.0.1" else "FAIL", "Waitress em loopback", f"bind={bind_host}")

        # 4. Cookies
        http_only = bool(getattr(settings, "SESSION_COOKIE_HTTPONLY", False))
        same_site = getattr(settings, "SESSION_COOKIE_SAMESITE", "")
        report("OK" if http_only and same_site == "Lax" else "FAIL", "Cookies HttpOnly / SameSite", f"HttpOnly={http_only}, SameSite={same_site}")

        if is_prod:
            sec_cookies = bool(settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE)
            report("OK" if sec_cookies else "FAIL", "Cookies Secure em producao", f"SESSION={settings.SESSION_COOKIE_SECURE}, CSRF={settings.CSRF_COOKIE_SECURE}")
        else:
            report("OK", "Cookies Secure em desenvolvimento", "desativado para permitir HTTP em 127.0.0.1 (ativo automaticamente em production)")

        # 5. Proxy HTTPS / Redirect / HSTS
        if is_prod:
            proxy_ok = getattr(settings, "SECURE_PROXY_SSL_HEADER", None) == ("HTTP_X_FORWARDED_PROTO", "https")
            redirect_ok = bool(getattr(settings, "SECURE_SSL_REDIRECT", False))
            hsts_seconds = int(getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0)
            report("OK" if proxy_ok else "FAIL", "SECURE_PROXY_SSL_HEADER", "HTTP_X_FORWARDED_PROTO=https")
            report("OK" if redirect_ok else "WARN", "SECURE_SSL_REDIRECT", f"{redirect_ok}")
            report("OK" if hsts_seconds >= 3600 else "WARN", "SECURE_HSTS_SECONDS", f"{hsts_seconds}s (includeSubDomains=False, preload=False)")
        else:
            report("OK", "HTTPS / HSTS", "aplicados automaticamente quando ARION_ENV=production")

        # 6. ALLOWED_HOSTS e CSRF_TRUSTED_ORIGINS
        hosts = list(getattr(settings, "ALLOWED_HOSTS", []))
        hosts_ok = bool(hosts) and all("*" not in h for h in hosts)
        if is_prod:
            report("OK" if hosts_ok else "FAIL", "ALLOWED_HOSTS sem wildcard", ", ".join(hosts))
        else:
            report("OK" if hosts_ok else "INFO", "ALLOWED_HOSTS", f"{', '.join(hosts)} (wildcard bloqueado em ARION_ENV=production)")

        origins = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []))
        if is_prod:
            origins_ok = bool(origins) and all(o.startswith("https://") for o in origins)
            report("OK" if origins_ok else "FAIL", "CSRF_TRUSTED_ORIGINS", f"{len(origins)} origem(ns) HTTPS")
        else:
            report("OK", "CSRF_TRUSTED_ORIGINS", f"{len(origins)} origem(ns) configurada(s) no ambiente atual")

        # 7. Rate Limiting do Login
        max_failures = getattr(settings, "ARION_LOGIN_MAX_FAILURES", None)
        throttle_ttl = getattr(settings, "ARION_LOGIN_THROTTLE_SECONDS", None)
        rl_ok = bool(max_failures and throttle_ttl)
        report("OK" if rl_ok else "FAIL", "Rate limiting de login (cache)", f"max_failures={max_failures}, janela={throttle_ttl}s")

        # 8. Banco MySQL
        db = settings.DATABASES.get("default", {})
        db_engine = db.get("ENGINE", "")
        db_host = str(db.get("HOST", "")).strip()
        db_user = str(db.get("USER", "")).strip()
        is_mysql_local = db_engine == "django.db.backends.mysql" and db_host.casefold() in {"127.0.0.1", "localhost", "::1"}
        non_root = bool(db_user) and db_user.casefold() != "root"
        report("OK" if (is_mysql_local and non_root) else "WARN", "Banco MySQL local com usuario dedicado", f"host={db_host}, user={db_user}")

        # 9. Headers e CSP
        report("OK" if getattr(settings, "X_FRAME_OPTIONS", "") == "DENY" else "FAIL", "X-Frame-Options", getattr(settings, "X_FRAME_OPTIONS", ""))
        report("OK" if getattr(settings, "SECURE_CONTENT_TYPE_NOSNIFF", False) else "FAIL", "X-Content-Type-Options", "nosniff")
        report("INFO", "Content-Security-Policy", "Report-Only global + sandbox estrito em artefatos do ARI")
