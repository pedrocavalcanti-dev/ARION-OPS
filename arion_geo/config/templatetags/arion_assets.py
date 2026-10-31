"""Resolve first-party JavaScript to readable dev sources or production bundles."""

import json
from functools import lru_cache
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static
from django import template


register = template.Library()


@lru_cache(maxsize=1)
def _production_manifest():
    manifest_path = Path(settings.ARION_FRONTEND_MANIFEST_PATH)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ImproperlyConfigured("Manifest de JavaScript de produção ausente ou inválido.") from None
    if not isinstance(manifest, dict) or not manifest:
        raise ImproperlyConfigured("Manifest de JavaScript de produção ausente ou inválido.")
    for value in manifest.values():
        if (not isinstance(value, str) or not value.startswith("arion_build/")
                or not value.endswith(".js") or ".." in PurePosixPath(value).parts):
            raise ImproperlyConfigured("Manifest de JavaScript de produção contém um caminho inválido.")
    return manifest


@register.simple_tag
def arion_js(source_path):
    path = PurePosixPath(str(source_path))
    if path.as_posix() != str(source_path) or path.suffix != ".js" or not path.parts or path.parts[0] != "js":
        raise ImproperlyConfigured("O asset JavaScript solicitado não pertence ao diretório de fontes permitido.")
    if settings.ARION_PRODUCTION:
        try:
            output_path = _production_manifest()[path.as_posix()]
        except KeyError:
            raise ImproperlyConfigured("Bundle de JavaScript de produção não está registrado.") from None
        return static(output_path)
    return static(path.as_posix())


@register.simple_tag
def arion_bundle_js(source_path):
    """Serve a built bundle when available, including during local visual review."""
    path = PurePosixPath(str(source_path))
    if path.as_posix() != str(source_path) or path.suffix != ".js" or not path.parts or path.parts[0] != "js":
        raise ImproperlyConfigured("O asset JavaScript solicitado não pertence ao diretório de fontes permitido.")
    if settings.ARION_PRODUCTION:
        try:
            output_path = _production_manifest()[path.as_posix()]
        except KeyError:
            raise ImproperlyConfigured("Bundle de JavaScript de produção não está registrado.") from None
        return static(output_path)

    if Path(settings.ARION_FRONTEND_MANIFEST_PATH).is_file():
        try:
            output_path = _production_manifest().get(path.as_posix())
        except ImproperlyConfigured:
            output_path = None
        if output_path:
            return static(output_path)
    return static(path.as_posix())
