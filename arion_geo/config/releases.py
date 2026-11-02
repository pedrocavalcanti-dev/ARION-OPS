"""Release metadata shared by the version endpoint, PWA and documentation UI."""

import json
import re
from datetime import date
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


RELEASES_PATH = settings.BASE_DIR.parent / "docs" / "08-releases" / "releases.json"


def release_catalog():
    try:
        source = json.loads(RELEASES_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ImproperlyConfigured("O catálogo de versões do ARION está ausente ou inválido.") from error

    version = source.get("current_version") if isinstance(source, dict) else None
    releases = source.get("releases") if isinstance(source, dict) else None
    semver = r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
    if not isinstance(version, str) or not re.fullmatch(semver, version) or not isinstance(releases, list) or not releases:
        raise ImproperlyConfigured("O catálogo de versões do ARION está incompleto.")

    normalized = []
    for release in releases:
        if not isinstance(release, dict) or not isinstance(release.get("version"), str) or not re.fullmatch(semver, release["version"]):
            raise ImproperlyConfigured("Há uma versão inválida no catálogo de releases do ARION.")
        try:
            released_at = date.fromisoformat(release["released_at"])
        except (KeyError, TypeError, ValueError) as error:
            raise ImproperlyConfigured("A data de uma release do ARION é inválida.") from error
        sections = release.get("sections", [])
        if not isinstance(sections, list) or any(
            not isinstance(section, dict)
            or not isinstance(section.get("category"), str)
            or not isinstance(section.get("items"), list)
            for section in sections
        ):
            raise ImproperlyConfigured("As notas de uma release do ARION são inválidas.")
        normalized.append({**release, "released_on": released_at})

    current = next((release for release in normalized if release["version"] == version), None)
    if current is None or normalized[0]["version"] != version:
        raise ImproperlyConfigured("A versão atual precisa ser a primeira release do catálogo do ARION.")
    return {"version": version, "current_release": current, "releases": normalized}


def current_version():
    return release_catalog()["version"]
