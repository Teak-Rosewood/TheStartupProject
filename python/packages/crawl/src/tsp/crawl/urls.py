from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .page import Page

_DEFAULT_PORTS = {"http": 80, "https": 443}
_TRACKING = ("utm_",)
_TRACKING_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid", "msclkid", "ref_src"}


def normalise(url: str) -> str:
    """Stable form for de-duplication: lower-case host, no default port or fragment, sorted query
    without tracking parameters, ``/`` for an empty path."""
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    if ":" in host:
        host = f"[{host}]"
    port = p.port
    netloc = host if port in (None, _DEFAULT_PORTS.get(scheme)) else f"{host}:{port}"
    query = sorted(
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING) and k.lower() not in _TRACKING_EXACT
    )
    return urlunsplit((scheme, netloc, p.path or "/", urlencode(query), ""))


def canonical(page: Page) -> str:
    """The page's own ``rel=canonical`` if it names the same host, else its final URL.

    A cross-host canonical is ignored so a page cannot redirect our identity for it elsewhere."""
    final = normalise(page.final_url)
    if page.canonical_url:
        cand = normalise(page.canonical_url)
        if urlsplit(cand).netloc == urlsplit(final).netloc:
            return cand
    return final
