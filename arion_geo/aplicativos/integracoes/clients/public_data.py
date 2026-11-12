"""Fixed HTTPS destinations; bounded response, TLS and safe errors, no URL logging."""
import json
import gzip
from io import BytesIO
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from django.views.decorators.debug import sensitive_variables


IBGE_BASE = "https://servicodados.ibge.gov.br/api/v1/localidades/"
ARCHIVE_ENDPOINTS = {
    "evaluation": "https://archive-api.open-meteo.com/v1/archive",
    "commercial": "https://customer-archive-api.open-meteo.com/v1/archive",
}
ALLOWED_URLS = {IBGE_BASE + "regioes", IBGE_BASE + "estados", *ARCHIVE_ENDPOINTS.values()}


class PublicDataError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicDataClient:
    def __init__(self, timeout=15, retries=2):
        self.timeout = max(2, min(int(timeout), 30))
        self.retries = max(0, min(int(retries), 2))

    @sensitive_variables()
    def get(self, endpoint, params=None, before_attempt=None):
        if endpoint not in ALLOWED_URLS:
            raise PublicDataError("Destino da integração não permitido.")
        url = endpoint + ("?" + urlencode(params) if params else "")
        opener = build_opener(NoRedirect())
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
                if error.code not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise PublicDataError(f"Serviço externo indisponível (HTTP {error.code}).") from None
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise PublicDataError("Falha de transporte seguro ou timeout no serviço externo.") from None
            except (ValueError, UnicodeError):
                raise PublicDataError("Resposta inválida do serviço externo.") from None
            time.sleep(0.5 * 2 ** attempt)
