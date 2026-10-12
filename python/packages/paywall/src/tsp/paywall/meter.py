"""The signed anonymous meter cookie and the coarse device key."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import time

COOKIE = "pw_m"
MAX_AGE_S = 400 * 24 * 3600  # browsers cap cookie life at about this


def new_meter_id() -> str:
    return secrets.token_urlsafe(12)


def sign(secret: str, meter_id: str, issued: int | None = None) -> str:
    issued = int(time.time()) if issued is None else issued
    body = f"v1.{meter_id}.{issued}"
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def verify(secret: str, cookie: str | None, now: float | None = None) -> str | None:
    """The meter id inside a genuine, unexpired cookie, else None (missing, forged or tampered)."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[2].isdigit():
        return None
    body = ".".join(parts[:3])
    good = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, parts[3]):
        return None
    if (time.time() if now is None else now) - int(parts[2]) > MAX_AGE_S:
        return None
    return parts[1]


_BROWSERS = ("edg", "opr", "firefox", "chrome", "safari", "curl", "python", "okhttp")


def ua_family(user_agent: str) -> str:
    ua = user_agent.lower()
    return next((b for b in _BROWSERS if b in ua), "other")


def network(ip: str | None) -> str:
    """The /24 (IPv4) or /48 (IPv6) the address sits in; the address itself is never kept."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return "unknown"
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


def device_key(secret: str, ip: str | None, user_agent: str, accept_language: str) -> str:
    """A keyed hash of (network, browser family, first language): coarse on purpose, and not reversible."""
    lang = re.split(r"[,;]", accept_language.strip().lower())[0] if accept_language else ""
    raw = f"{network(ip)}|{ua_family(user_agent)}|{lang}"
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:24]
