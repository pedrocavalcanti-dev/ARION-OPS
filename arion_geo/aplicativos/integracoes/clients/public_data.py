"""Fixed HTTPS destinations; bounded response, TLS and safe errors, no URL logging."""
import json
import gzip
from io import BytesIO
import time
import ipaddress
import socket
import re
from http.client import HTTPSConnection
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


IBGE_BASE = "https://servicodados.ibge.gov.br/api/v1/localidades/"
ARCHIVE_ENDPOINTS = {
    "public": "https://archive-api.open-meteo.com/v1/archive",
    "evaluation": "https://archive-api.open-meteo.com/v1/archive",
    "commercial": "https://customer-archive-api.open-meteo.com/v1/archive",
}
ALLOWED_URLS = {IBGE_BASE + "regioes", IBGE_BASE + "estados", *ARCHIVE_ENDPOINTS.values()}


class PublicDataError(ValueError):
    def __init__(self, message, *, code="unavailable", retry_after=0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def validate_weather_endpoint(mode, endpoint):
    """Only HTTPS; credentials, query secrets and redirect destinations are rejected."""
    try:
        if not isinstance(endpoint, str) or len(endpoint) > 500 or any(c.isspace() for c in endpoint):
            raise ValueError
        parts = urlsplit(endpoint)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        if mode in {"public", "commercial"}:
            if endpoint != ARCHIVE_ENDPOINTS[mode]:
                raise ValueError
        elif mode != "self_hosted":
            raise ValueError
        return endpoint
    except (ValueError, TypeError):
        raise PublicDataError("Informe um endpoint HTTPS válido para a fonte selecionada, sem credenciais ou parâmetros na URL.", code="configuration") from None


class WeatherHTTPSConnection(HTTPSConnection):
    """Pin validated addresses while retaining HTTPS hostname/certificate validation."""
    def connect(self):
        allowed = {str(v).lower() for v in getattr(settings, "GEO_WEATHER_ALLOWED_HOSTS", ())}
        trusted = self.host.lower() in allowed
        try:
            addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
            if not addresses or (not trusted and any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses)):
                raise PublicDataError("Destino self-hosted não autorizado para esta rede.", code="configuration")
        except (socket.gaierror, ValueError):
            raise PublicDataError("Não foi possível resolver o destino meteorológico.", code="connection") from None
        def pinned_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
            failure = None
            for family, kind, protocol, _, target in addresses:
                sock = socket.socket(family, kind, protocol)
                try:
                    sock.settimeout(timeout)
                    if source_address:
                        sock.bind(source_address)
                    sock.connect(target)
                    return sock
                except OSError as error:
                    failure = error
                    sock.close()
            raise failure or OSError("Conexão indisponível")
        self._create_connection = pinned_connection
        super().connect()


class WeatherHTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(WeatherHTTPSConnection, req, context=self._context, check_hostname=self._check_hostname)


def retry_delay(value):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            return max(0, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return 60


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicDataClient:
    def __init__(self, timeout=15, retries=2, *, weather_endpoint=None):
        self.timeout = max(2, min(int(timeout), 30))
        self.retries = max(0, min(int(retries), 2))
        self.weather_endpoint = weather_endpoint

    @sensitive_variables()
    def get(self, endpoint, params=None, before_attempt=None):
        ibge_local = re.fullmatch(r"https://servicodados\.ibge\.gov\.br/api/(?:v1/localidades/estados/[1-5][0-9]/municipios|v3/malhas/municipios/[1-5][0-9]{6})",endpoint)
        if endpoint not in ALLOWED_URLS and endpoint != self.weather_endpoint and not ibge_local:
            raise PublicDataError("Destino da integração não permitido.")
        if endpoint == self.weather_endpoint:
            validate_weather_endpoint("self_hosted", endpoint)
        url = endpoint + ("?" + urlencode(params) if params else "")
        opener = build_opener(NoRedirect(), ProxyHandler({}), WeatherHTTPSHandler()) if endpoint == self.weather_endpoint and endpoint not in ALLOWED_URLS else build_opener(NoRedirect())
        for attempt in range(self.retries + 1):
            if before_attempt:
                before_attempt()
            try:
                request = Request(url, headers={"User-Agent": "ARION-OPS-GEO/1", "Accept": "application/json"})
                with opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(4_000_001)
                    encoding = response.headers.get("Content-Encoding", "").lower()
                if len(raw) > 4_000_000:
                    raise PublicDataError("Resposta da integração excede o limite.")
                if encoding == "gzip":
                    with gzip.GzipFile(fileobj=BytesIO(raw)) as compressed:
                        raw = compressed.read(4_000_001)
                    if len(raw)>4_000_000:
                        raise PublicDataError("Resposta descompactada excede o limite.")
                return json.loads(raw)
            except HTTPError as error:
                if error.code == 429:
                    raise PublicDataError("Limite do fornecedor atingido (HTTP 429). Aguarde antes de nova coleta.", code="rate_limited", retry_after=retry_delay((error.headers or {}).get("Retry-After"))) from None
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise PublicDataError(f"Serviço externo indisponível (HTTP {error.code}).", code="authentication" if error.code in {401, 403} else "unavailable") from None
            except PublicDataError:
                raise
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise PublicDataError("Falha de transporte seguro ou timeout no serviço externo.") from None
            except (ValueError, UnicodeError):
                raise PublicDataError("Resposta inválida do serviço externo.") from None
            time.sleep(0.5 * 2 ** attempt)
