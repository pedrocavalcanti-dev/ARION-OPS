"""Settings for the ARION OPS Django project."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent


ARION_ENV = os.getenv("ARION_ENV", "development").strip().casefold()

if ARION_ENV not in {"development", "production"}:
    raise ImproperlyConfigured(
        "ARION_ENV deve ser development ou production."
    )

ARION_PRODUCTION = ARION_ENV == "production"


def env_bool(name, default=False):
    return os.getenv(
        name,
        str(default),
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# =============================================================================
# DJANGO / SEGURANÇA BÁSICA
# =============================================================================

_development_secret = (
    "django-insecure-local-development-key-change-before-deployment"
)

if ARION_PRODUCTION:
    SECRET_KEY = os.getenv(
        "DJANGO_SECRET_KEY",
        "",
    ).strip()

    secret_classes = sum(
        (
            any(char.islower() for char in SECRET_KEY),
            any(char.isupper() for char in SECRET_KEY),
            any(char.isdigit() for char in SECRET_KEY),
            any(not char.isalnum() for char in SECRET_KEY),
        )
    )

    secret_markers = (
        "replace_with",
        "change_me",
        "changeme",
        "example_secret",
    )

    if (
        len(SECRET_KEY) < 50
        or len(set(SECRET_KEY)) < 20
        or secret_classes < 3
        or SECRET_KEY == _development_secret
        or any(
            marker in SECRET_KEY.casefold()
            for marker in secret_markers
        )
    ):
        raise ImproperlyConfigured(
            "Produção exige DJANGO_SECRET_KEY forte "
            "(mínimo de 50 caracteres) configurada fora do código."
        )

else:
    SECRET_KEY = os.getenv(
        "DJANGO_SECRET_KEY",
        _development_secret,
    )


DEBUG = env_bool(
    "DJANGO_DEBUG",
    not ARION_PRODUCTION,
)

if ARION_PRODUCTION and DEBUG:
    raise ImproperlyConfigured(
        "DJANGO_DEBUG deve ser false em ARION_ENV=production."
    )


# =============================================================================
# SERVIDOR WEB
# =============================================================================

try:
    ARION_WEB_PORT = int(
        os.getenv(
            "ARION_WEB_PORT",
            "8000",
        )
    )
except ValueError:
    raise ImproperlyConfigured(
        "ARION_WEB_PORT deve ser uma porta TCP entre 1 e 65535."
    ) from None

if not 1 <= ARION_WEB_PORT <= 65535:
    raise ImproperlyConfigured(
        "ARION_WEB_PORT deve ser uma porta TCP entre 1 e 65535."
    )

# Nunca expor diretamente o Waitress para a rede.
# O acesso externo é feito através do Cloudflare Tunnel.
ARION_WEB_BIND = "127.0.0.1"


# =============================================================================
# HOSTS / CSRF
# =============================================================================

_default_hosts = (
    ""
    if ARION_PRODUCTION
    else "127.0.0.1,localhost"
)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        _default_hosts,
    ).split(",")
    if host.strip()
]

if ARION_PRODUCTION and (
    not ALLOWED_HOSTS
    or any(
        "*" in host
        or "/" in host
        or "@" in host
        for host in ALLOWED_HOSTS
    )
):
    raise ImproperlyConfigured(
        "Produção exige DJANGO_ALLOWED_HOSTS explícito e sem wildcard."
    )

ARION_PUBLIC_BASE_URL = os.getenv(
    "ARION_PUBLIC_BASE_URL",
    f"http://127.0.0.1:{ARION_WEB_PORT}" if not ARION_PRODUCTION else "",
).strip().rstrip("/")
_public_origin = urlsplit(ARION_PUBLIC_BASE_URL)
if (
    not ARION_PUBLIC_BASE_URL
    or not _public_origin.hostname
    or _public_origin.username
    or _public_origin.password
    or _public_origin.path not in ("", "/")
    or _public_origin.query
    or _public_origin.fragment
    or (ARION_PRODUCTION and _public_origin.scheme != "https")
    or (not ARION_PRODUCTION and _public_origin.scheme not in {"http", "https"})
    or _public_origin.hostname.casefold() not in {host.casefold() for host in ALLOWED_HOSTS}
):
    raise ImproperlyConfigured(
        "ARION_PUBLIC_BASE_URL deve ser a origem pública confiável e estar em DJANGO_ALLOWED_HOSTS; em produção, use HTTPS."
    )


if ARION_PRODUCTION:
    _csrf_origins = [
        origin.strip()
        for origin in os.getenv(
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "",
        ).split(",")
        if origin.strip()
    ]

    if not _csrf_origins:
        raise ImproperlyConfigured(
            "Produção exige DJANGO_CSRF_TRUSTED_ORIGINS "
            "com origens HTTPS explícitas."
        )

    for _origin in _csrf_origins:
        _parsed_origin = urlsplit(_origin)

        if (
            _parsed_origin.scheme != "https"
            or not _parsed_origin.hostname
            or "*" in _parsed_origin.netloc
            or _parsed_origin.username
            or _parsed_origin.password
            or _parsed_origin.path not in ("", "/")
            or _parsed_origin.query
            or _parsed_origin.fragment
        ):
            raise ImproperlyConfigured(
                "DJANGO_CSRF_TRUSTED_ORIGINS aceita "
                "somente origens HTTPS exatas."
            )

        if _parsed_origin.hostname.casefold() not in {
            host.casefold()
            for host in ALLOWED_HOSTS
        }:
            raise ImproperlyConfigured(
                "Cada origem CSRF de produção também "
                "deve constar em DJANGO_ALLOWED_HOSTS."
            )

    CSRF_TRUSTED_ORIGINS = _csrf_origins

else:
    CSRF_TRUSTED_ORIGINS = [
        origin.strip()
        for origin in os.getenv(
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "",
        ).split(",")
        if origin.strip()
    ]


# =============================================================================
# OTRS
# =============================================================================

OTRS_WEB_BASE_URL = os.getenv(
    "OTRS_WEB_BASE_URL",
    "https://suporte.grupoarion.com.br/otrs/index.pl",
).strip().rstrip("?")

OTRS_NOC_ATACADO_QUEUE_URL = os.getenv(
    "OTRS_NOC_ATACADO_QUEUE_URL",
    (
        "https://suporte.grupoarion.com.br/otrs/index.pl"
        "?Action=AgentKPISearch;"
        "Subaction=Search;"
        "TakeLastSearch=1;"
        "Profile=251_2"
    ),
).strip()

# Non-TLS compatibility is limited to this explicitly configured legacy host.
OTRS_LEGACY_NO_TLS_HOST = os.getenv("ARION_OTRS_LEGACY_NO_TLS_HOST", "").strip().casefold()


# =============================================================================
# APLICAÇÕES
# =============================================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "aplicativos.autenticacao.apps.AutenticacaoConfig",
    "aplicativos.configuracoes.apps.ConfiguracoesConfig",
    "aplicativos.auditoria.apps.AuditoriaConfig",
    "aplicativos.integracoes.apps.IntegracoesConfig",
    "aplicativos.ari.apps.AriConfig",
    "aplicativos.painel.apps.PainelConfig",
    "aplicativos.mapa.apps.MapaConfig",
    "aplicativos.rankings.apps.RankingsConfig",
    "aplicativos.base_links.apps.BaseLinksConfig",
    "aplicativos.chamados.apps.ChamadosConfig",
    "aplicativos.relatorios.apps.RelatoriosConfig",
    "aplicativos.qualidade.apps.QualidadeConfig",
]


# =============================================================================
# MIDDLEWARE
# =============================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    *(
        ["whitenoise.middleware.WhiteNoiseMiddleware"]
        if ARION_PRODUCTION
        else []
    ),

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",

    "config.middleware.ProductionSecurityHeadersMiddleware",
    "config.middleware.RequestErrorMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "config.middleware.PasswordPolicyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# =============================================================================
# URLS / TEMPLATES
# =============================================================================

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "templates",
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "builtins": [
                "config.templatetags.arion_assets",
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "aplicativos.configuracoes.context_processors.user_theme",
                "aplicativos.configuracoes.context_processors.arion_version",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"


# =============================================================================
# BANCO DE DADOS
# =============================================================================

from aplicativos.integracoes.services.arion_database import (
    django_database_settings,
)

DATABASES = {
    "default": django_database_settings(
        base_dir=BASE_DIR,
    )
}

if (
    ARION_PRODUCTION
    and DATABASES["default"]["ENGINE"]
    != "django.db.backends.mysql"
):
    raise ImproperlyConfigured(
        "Produção exige o MySQL local configurado para o ARION."
    )


if ARION_PRODUCTION:
    _production_db = DATABASES["default"]

    if (
        _production_db.get(
            "HOST",
            "",
        ).strip().casefold()
        not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        or not _production_db.get(
            "USER",
            "",
        ).strip()
        or _production_db["USER"].strip().casefold() == "root"
    ):
        raise ImproperlyConfigured(
            "Produção exige usuário MySQL próprio do ARION "
            "e conexão local por loopback."
        )


if DATABASES["default"]["ENGINE"] == "django.db.backends.mysql":
    import pymysql

    pymysql.install_as_MySQLdb()


# =============================================================================
# PASSWORD VALIDATORS
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
        "OPTIONS": {"min_length": 12},
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        )
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        )
    },
]


# =============================================================================
# LOCALIZAÇÃO
# =============================================================================

LANGUAGE_CODE = "pt-br"
DEFAULT_CHARSET = "utf-8"
TIME_ZONE = "America/Sao_Paulo"

USE_I18N = True
USE_TZ = True


# =============================================================================
# STATIC
# =============================================================================

STATIC_URL = "/static/"

STATIC_ROOT = Path(
    os.getenv(
        "DJANGO_STATIC_ROOT",
        str(
            PROJECT_ROOT
            / "var"
            / "staticfiles"
        ),
    )
).expanduser()


ARION_FRONTEND_MANIFEST_PATH = Path(
    os.getenv(
        "DJANGO_FRONTEND_MANIFEST",
        str(
            PROJECT_ROOT
            / "var"
            / "arion-frontend-manifest.json"
        ),
    )
).expanduser()


if ARION_PRODUCTION:
    STATICFILES_DIRS = [
        BASE_DIR / "static",
    ]

    if not ARION_FRONTEND_MANIFEST_PATH.is_file():
        raise ImproperlyConfigured(
            "Build frontend de produção ausente; "
            "execute npm run build:production."
        )

else:
    STATICFILES_DIRS = [
        BASE_DIR / "static",
        PROJECT_ROOT / "frontend_src",
    ]


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


if ARION_PRODUCTION:
    STORAGES = {
        "default": {
            "BACKEND": (
                "django.core.files.storage."
                "FileSystemStorage"
            )
        },
        "staticfiles": {
            "BACKEND": (
                "whitenoise.storage."
                "CompressedManifestStaticFilesStorage"
            )
        },
    }

else:
    STORAGES = {
        "default": {
            "BACKEND": (
                "django.core.files.storage."
                "FileSystemStorage"
            )
        },
        "staticfiles": {
            "BACKEND": (
                "django.contrib.staticfiles.storage."
                "StaticFilesStorage"
            )
        },
    }


# =============================================================================
# AUTENTICAÇÃO
# =============================================================================

LOGIN_URL = "autenticacao:login"
LOGIN_REDIRECT_URL = "painel:index"
LOGOUT_REDIRECT_URL = "autenticacao:login"

CSRF_FAILURE_VIEW = "config.errors.csrf_failure"

ARION_LOGIN_MAX_FAILURES = 5
ARION_LOGIN_MAX_IP_FAILURES = 20
ARION_LOGIN_THROTTLE_SECONDS = 900


# =============================================================================
# LOGGING
# =============================================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "arion_errors": {
            "format": (
                "{asctime} "
                "{levelname} "
                "{name} "
                "{message}"
            ),
            "style": "{",
        },
    },

    "handlers": {
        "arion_error_console": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
            "formatter": "arion_errors",
        },
    },

    "loggers": {
        "django.request": {
            # Django logs exception tracebacks here.
            # Shared error handlers write a sanitized
            # request record instead.
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        },

        "config.errors": {
            "handlers": [
                "arion_error_console",
            ],
            "level": "WARNING",
            "propagate": False,
        },

        (
            "aplicativos.integracoes.services."
            "arion_database_migration"
        ): {
            "handlers": [
                "arion_error_console",
            ],
            "level": "ERROR",
            "propagate": False,
        },
    },
}


# =============================================================================
# MAPBOX
# =============================================================================

MAPBOX_PUBLIC_TOKEN = os.getenv(
    "MAPBOX_PUBLIC_TOKEN",
    "",
).strip()


# =============================================================================
# MEDIA / ARI
# =============================================================================

# ARI uploads are private and are only served through
# owner-checked views.

MEDIA_ROOT = Path(
    os.getenv(
        "DJANGO_MEDIA_ROOT",
        str(
            PROJECT_ROOT
            / "var"
            / "private_media"
            if ARION_PRODUCTION
            else BASE_DIR
            / "private_media"
        ),
    )
).expanduser()


ARI_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
ARI_MAX_ATTACHMENTS = 5
ARI_MAX_TOTAL_ATTACHMENT_BYTES = 40 * 1024 * 1024


FILE_UPLOAD_HANDLERS = [
    "config.upload_limits.AriAttachmentQuotaHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]


# =============================================================================
# HEADERS DE SEGURANÇA
# =============================================================================

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

X_FRAME_OPTIONS = "DENY"


PERMISSIONS_POLICY = (
    "camera=(), "
    "microphone=(), "
    "geolocation=(), "
    "payment=(), "
    "usb=()"
)


CONTENT_SECURITY_POLICY_REPORT_ONLY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "

    "script-src "
    "'self' "
    "https://api.mapbox.com; "

    "style-src "
    "'self' "
    "'unsafe-inline' "
    "https://api.mapbox.com "
    "https://fonts.googleapis.com; "

    "img-src "
    "'self' "
    "data: "
    "blob: "
    "https://api.mapbox.com "
    "https://*.mapbox.com "
    "https://*.tiles.mapbox.com; "

    "font-src "
    "'self' "
    "data: "
    "https://fonts.gstatic.com "
    "https://fonts.mapbox.com; "

    "connect-src "
    "'self' "
    "https://api.mapbox.com "
    "https://events.mapbox.com "
    "https://*.mapbox.com "
    "https://*.tiles.mapbox.com; "

    "worker-src "
    "'self' "
    "blob:"
)


# =============================================================================
# PRODUÇÃO / CLOUDFLARE TUNNEL
# =============================================================================

if ARION_PRODUCTION:

    # O usuário acessa o sistema externamente por HTTPS através
    # do Cloudflare.
    #
    # O trecho Cloudflare Tunnel -> Waitress utiliza HTTP somente
    # no loopback da própria máquina:
    #
    # Cloudflare
    #      ↓
    # cloudflared
    #      ↓
    # http://127.0.0.1:8100
    #
    # O Waitress em 127.0.0.1 confia nos cabeçalhos X-Forwarded-Proto
    # entregues pelo agente local cloudflared (--trusted-proxy=127.0.0.1).
    # Assim, requisições HTTPS chegam com HTTP_X_FORWARDED_PROTO=https
    # sem loop de redirecionamento, e requisições HTTP são redirecionadas
    # para HTTPS.

    SECURE_PROXY_SSL_HEADER = (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )

    SECURE_SSL_REDIRECT = env_bool(
        "DJANGO_SECURE_SSL_REDIRECT",
        True,
    )

    # Cookies continuam restritos a HTTPS do lado do navegador.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = False
    CSRF_COOKIE_SAMESITE = "Lax"

    try:
        SECURE_HSTS_SECONDS = int(
            os.getenv(
                "DJANGO_SECURE_HSTS_SECONDS",
                "31536000",
            )
        )
    except ValueError:
        raise ImproperlyConfigured(
            "DJANGO_SECURE_HSTS_SECONDS "
            "deve ser um inteiro não negativo."
        ) from None

    if SECURE_HSTS_SECONDS < 0:
        raise ImproperlyConfigured(
            "DJANGO_SECURE_HSTS_SECONDS "
            "deve ser um inteiro não negativo."
        )

    # Mantidos desativados por enquanto para rollout gradual.
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

    # CSP permanece inicialmente em Report-Only.
    ARION_CSP_REPORT_ONLY = True


else:

    SECURE_SSL_REDIRECT = False
    SECURE_PROXY_SSL_HEADER = None

    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    CSRF_COOKIE_SECURE = False
    CSRF_COOKIE_HTTPONLY = False
    CSRF_COOKIE_SAMESITE = "Lax"

    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

    ARION_CSP_REPORT_ONLY = False
