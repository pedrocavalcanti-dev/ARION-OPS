"""Cache-backed authentication throttle for the login endpoint."""

from __future__ import annotations

import hashlib
import ipaddress

from django.conf import settings
from django.core.cache import cache


def normalize_username(username: str | None) -> str:
    return str(username or "").strip().casefold()[:150]


def client_ip_for_login(request) -> str:
    if request is None:
        return "unknown"
    meta = getattr(request, "META", {}) or {}
    remote_raw = str(meta.get("REMOTE_ADDR") or "").strip()
    if getattr(settings, "ARION_PRODUCTION", False) and remote_raw in {"127.0.0.1", "::1"}:
        cf_raw = str(meta.get("HTTP_CF_CONNECTING_IP") or "").strip()
        if cf_raw:
            try:
                return str(ipaddress.ip_address(cf_raw))
            except ValueError:
                pass
    try:
        return str(ipaddress.ip_address(remote_raw))
    except ValueError:
        return "unknown"


def _cache_keys(request, username: str | None) -> tuple[str, str]:
    ip_norm = client_ip_for_login(request)
    user_norm = normalize_username(username)
    ip_digest = hashlib.sha256(ip_norm.encode("utf-8")).hexdigest()[:24]
    pair_digest = hashlib.sha256(f"{ip_norm}:{user_norm}".encode("utf-8")).hexdigest()[:24]
    return f"arion:auth:pair:{pair_digest}", f"arion:auth:ip:{ip_digest}"


def is_login_throttled(request, username: str | None) -> bool:
    pair_key, ip_key = _cache_keys(request, username)
    max_pair = int(getattr(settings, "ARION_LOGIN_MAX_FAILURES", 5))
    max_ip = int(getattr(settings, "ARION_LOGIN_MAX_IP_FAILURES", 20))
    pair_failures = int(cache.get(pair_key, 0) or 0)
    ip_failures = int(cache.get(ip_key, 0) or 0)
    return pair_failures >= max_pair or ip_failures >= max_ip


def record_login_failure(request, username: str | None) -> int:
    pair_key, ip_key = _cache_keys(request, username)
    ttl = int(getattr(settings, "ARION_LOGIN_THROTTLE_SECONDS", 900))
    pair_failures = int(cache.get(pair_key, 0) or 0) + 1
    ip_failures = int(cache.get(ip_key, 0) or 0) + 1
    cache.set(pair_key, pair_failures, timeout=ttl)
    cache.set(ip_key, ip_failures, timeout=ttl)
    return pair_failures


def clear_login_failures(request, username: str | None) -> None:
    pair_key, ip_key = _cache_keys(request, username)
    cache.delete(pair_key)
    cache.delete(ip_key)

