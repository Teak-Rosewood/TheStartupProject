from pathlib import Path

import httpx
import pytest
from tsp.crawl import HttpFetcher, NotModified, TooLarge, TooManyRedirects, UnsafeURL, canonical
from tsp.crawl.errors import FetchError, UnsupportedContentType

FIX = Path(__file__).parent / "fixtures"
HTML = {
    "content-type": "text/html; charset=utf-8",
    "etag": '"v1"',
    "last-modified": "Mon, 01 Jan 2026 00:00:00 GMT",
}


def make(handler, **kw):
    async def resolve(host):
        return {"help.example.com": ["93.184.216.34"], "evil.test": ["93.184.216.35"]}.get(
            host, ["10.0.0.9"]
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return HttpFetcher(client, resolver=resolve, **kw)


async def test_extracts_article_and_links():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, headers=HTML, content=(FIX / "help_article.html").read_bytes())

    page = await make(handler).fetch("https://help.example.com/reset-password")
    assert page.title == "Reset your password"
    assert "forgot password" in page.markdown and "one-time link" in page.markdown
    assert "Subscribe to our newsletter" not in page.markdown
    assert "Copyright 2026" not in page.markdown
    assert (
        "https://help.example.com/help/two-factor" in page.links
    )  # fragment dropped, de-duplicated
    assert page.links.count("https://help.example.com/help/two-factor") == 1
    assert page.etag == '"v1"' and page.fetcher == "http" and len(page.content_hash) == 64
    assert canonical(page) == "https://help.example.com/reset-password"
    # connection goes to the pinned IP; Host and SNI keep the name
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "help.example.com"
    assert seen[0].extensions["sni_hostname"] == "help.example.com"


async def test_table_is_retained():
    def handler(req):
        return httpx.Response(200, headers=HTML, content=(FIX / "pricing_table.html").read_bytes())

    page = await make(handler).fetch("https://help.example.com/pricing")
    assert (
        "Pro" in page.markdown and "$29" in page.markdown and "Unlimited projects" in page.markdown
    )
    assert "|" in page.markdown
    assert "Copyright" not in page.markdown


async def test_conditional_get_returns_not_modified():
    def handler(req):
        assert req.headers["if-none-match"] == '"v1"'
        assert req.headers["if-modified-since"] == "Mon, 01 Jan 2026 00:00:00 GMT"
        return httpx.Response(304, headers={"etag": '"v1"'})

    r = await make(handler).fetch(
        "https://help.example.com/", etag='"v1"', last_modified="Mon, 01 Jan 2026 00:00:00 GMT"
    )
    assert isinstance(r, NotModified) and r.etag == '"v1"'


async def test_redirect_to_metadata_ip_is_blocked():
    def handler(req):
        if req.headers["host"] == "help.example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError("must not connect to the metadata address")

    with pytest.raises(UnsafeURL):
        await make(handler).fetch("https://help.example.com/")


async def test_redirect_to_name_resolving_private_is_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://internal.corp/"})

    with pytest.raises(UnsafeURL):
        await make(handler).fetch("https://help.example.com/")


async def test_redirects_followed_and_final_url_reported():
    def handler(req):
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers=HTML, content=(FIX / "help_article.html").read_bytes())

    page = await make(handler).fetch("https://help.example.com/old")
    assert page.final_url == "https://help.example.com/new" and page.url.endswith("/old")


async def test_redirect_loop_stops():
    def handler(req):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(TooManyRedirects):
        await make(handler, max_redirects=3).fetch("https://help.example.com/")


async def test_body_over_limit_aborts():
    def handler(req):
        return httpx.Response(200, headers=HTML, content=b"x" * 5000)

    with pytest.raises(TooLarge):
        await make(handler, max_bytes=1000).fetch("https://help.example.com/")


async def test_http_error_and_unsupported_type():
    def handler(req):
        if req.url.path == "/gone":
            return httpx.Response(410)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7")

    f = make(handler)
    with pytest.raises(FetchError) as e:
        await f.fetch("https://help.example.com/gone")
    assert e.value.status == 410
    with pytest.raises(UnsupportedContentType) as u:
        await f.fetch("https://help.example.com/doc.pdf")
    assert u.value.raw.body == b"%PDF-1.7"


async def test_ipv6_pinned_in_brackets():
    seen = []

    async def resolve(host):
        return ["2606:2800:220:1::1"]

    def handler(req):
        seen.append(req)
        return httpx.Response(200, headers=HTML, content=(FIX / "help_article.html").read_bytes())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await HttpFetcher(client, resolver=resolve).fetch("https://v6.example.com/")
    assert seen[0].url.host == "2606:2800:220:1::1"


async def test_custom_headers_not_leaked_to_redirect_target():
    seen = {}

    def handler(req):
        seen[req.headers["host"]] = req.headers.get("x-api-key")
        if req.headers["host"] == "help.example.com":
            return httpx.Response(302, headers={"location": "https://evil.test/"})
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")

    await make(handler).fetch_raw("https://help.example.com/", headers={"x-api-key": "k"})
    assert seen == {"help.example.com": "k", "evil.test": None}
