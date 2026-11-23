"""Asset controls shared by the production checker and isolated validation tests."""

import json
from pathlib import Path, PurePosixPath
import re


JS_TAG_PATTERN = re.compile(
    r"\{%\s*(?:arion_js|arion_bundle_js)\s+['\"](js/[A-Za-z0-9_./-]+\.js)['\"]\s*%\}"
)
LEGACY_JS_PATTERN = re.compile(r"\{%\s*static\s+['\"](js/[^'\"]+\.js)['\"]")
# Public third-party distribution, deliberately kept outside the first-party build.
PUBLIC_VENDOR_JS = {"vendor/echarts/echarts.min.js"}


def template_js_references(template_root):
    references, legacy = set(), set()
    for template in Path(template_root).rglob("*.html"):
        content = template.read_text(encoding="utf-8")
        references.update(JS_TAG_PATTERN.findall(content))
        legacy.update(LEGACY_JS_PATTERN.findall(content))
    return references, legacy


def _manifest(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _bundle_path(value):
    if not isinstance(value, str) or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if (pure.as_posix() != value or not value.startswith("arion_build/")
            or ".." in pure.parts or pure.suffix != ".js"):
        return None
    return Path(*pure.parts)


def check_frontend_assets(settings, check, *, collected=True):
    """Report each official frontend control; no credentials or database required."""
    django_root = Path(settings.BASE_DIR)
    frontend_manifest = _manifest(settings.ARION_FRONTEND_MANIFEST_PATH)
    check(bool(frontend_manifest), "build frontend", "manifest presente" if frontend_manifest else "manifest ausente/inválido")
    references, legacy = template_js_references(django_root / "templates")
    expected = references | {"__service_worker__"}
    missing = expected - frontend_manifest.keys()
    extra = frontend_manifest.keys() - expected
    details = []
    if missing:
        details.append("ausentes: " + ", ".join(sorted(missing)))
    if extra:
        details.append("extras: " + ", ".join(sorted(extra)))
    if legacy:
        details.append("referências static legadas: " + ", ".join(sorted(legacy)))
    check(bool(references) and not (missing or extra or legacy), "referências JS",
          "; ".join(details) if details else "ambas as tags e Service Worker cobertos pelo manifest")

    bad_bundles = []
    for key, value in frontend_manifest.items():
        relative = _bundle_path(value)
        if relative is None or not (django_root / "static" / relative).is_file():
            bad_bundles.append(key)
    build_maps = list((django_root / "static" / "arion_build").rglob("*.map"))
    check(bool(frontend_manifest) and not (bad_bundles or build_maps), "bundles de produção",
          "ausentes/inválidos: " + ", ".join(bad_bundles) if bad_bundles else
          "source maps presentes" if build_maps else "arquivos presentes, sem source maps")

    source_static_js = []
    for configured in settings.STATICFILES_DIRS:
        static_dir = Path(configured[1] if isinstance(configured, (tuple, list)) else configured)
        for path in static_dir.rglob("*.js"):
            relative = path.relative_to(static_dir)
            # Match Django's default ignore of hidden files/directories. Incomplete
            # staging output is never published by collectstatic.
            if any(part.startswith(".") for part in relative.parts):
                continue
            if relative.parts[0] != "arion_build" and relative.as_posix() not in PUBLIC_VENDOR_JS:
                source_static_js.append(relative.as_posix())
    check(not source_static_js, "fontes JS em STATICFILES_DIRS",
          "fontes indevidas: " + ", ".join(sorted(source_static_js)) if source_static_js else
          "somente bundles e distribuição pública ECharts autorizada")

    if not collected:
        return
    static_root = Path(settings.STATIC_ROOT)
    storage_paths = _manifest(static_root / "staticfiles.json").get("paths", {})
    storage_ok = isinstance(storage_paths, dict) and bool(storage_paths) and bool(frontend_manifest)
    if storage_ok:
        for value in frontend_manifest.values():
            hashed = storage_paths.get(value) if isinstance(value, str) else None
            relative = _bundle_path(hashed)
            if relative is None or not (static_root / relative).is_file():
                storage_ok = False
                break
    source_maps = list(static_root.rglob("*.map")) if static_root.exists() else []
    collected_sources = list((static_root / "js").rglob("*.js")) if (static_root / "js").is_dir() else []
    # Also catch the old, readable Service Worker left by a non-clean collection.
    collected_sources.extend((static_root / "pwa").glob("service-worker*.js"))
    check(storage_ok, "collectstatic", "manifest com todos os bundles" if storage_ok else "execute collectstatic após o build")
    check(not source_maps, "source maps coletados", "nenhum" if not source_maps else "remova-os de STATIC_ROOT")
    check(not collected_sources, "sources JS coletados",
          "fontes indevidas: " + ", ".join(sorted(path.relative_to(static_root).as_posix() for path in collected_sources))
          if collected_sources else "nenhum")
