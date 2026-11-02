"""Safe provider logo lookup for the Dashboard ranking."""
import re
import unicodedata
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.templatetags.static import static


_PROVIDER_IDENTITIES = {
    "FIERCE TELECOM": ("fierce-telecom", "FT"),
    "CLARO": ("claro", "CL"),
    "TOWER": ("tower-connection", "TC"),
    "TOWER CONNECTION": ("tower-connection", "TC"),
    "TOWERCONNECTION": ("tower-connection", "TC"),
    "TOWER CONNECTION WKE": ("tower-connection", "TC"),
    "MIDAS CONSULTING LTDA": ("midas-consulting", "MC"),
    "VERO": ("vero-americanet", "VA"),
    "AMERICANET": ("vero-americanet", "VA"),
    "VERO AMERICANET": ("vero-americanet", "VA"),
    "DESKTOP": ("desktop", "DE"),
    "GIGA": ("giga-plus", "GP"),
    "GIGA PLUS": ("giga-plus", "GP"),
    "GIGAPLUS": ("giga-plus", "GP"),
    "ALARES": ("alares", "AL"),
    "ALGAR": ("algar", "AG"),
    "OP ALGAR": ("algar", "AG"),
    "ELO SOLUCOES": ("elo-solucoes", "ES"),
}
_LOGO_EXTENSIONS = ("png", "svg", "webp", "jpg", "jpeg")


def _normalized_name(value):
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_name = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", ascii_name.upper()).split())


def _fallback_initials(value):
    words = _normalized_name(value).split()
    if not words:
        return "?"
    return "".join(word[0] for word in words[:2]) if len(words) > 1 else words[0][:2]


@lru_cache(maxsize=32)
def _logo_url(slug):
    for extension in _LOGO_EXTENSIONS:
        relative_path = f"branding/provedores/{slug}.{extension}"
        if finders.find(relative_path):
            return static(relative_path)
    return ""


def provider_logo(value):
    """Return a static logo URL when present, plus a stable text fallback."""
    normalized = _normalized_name(value)
    slug, initials = _PROVIDER_IDENTITIES.get(normalized, ("", _fallback_initials(value)))
    return {"url": _logo_url(slug) if slug else "", "initials": initials}
