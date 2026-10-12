import gzip

import pytest
from crawl_fakes import MemCache
from tsp.crawl import Page, RobotsCache, canonical, normalise, parse_sitemap
from tsp.crawl.errors import FetchError

UA = "TSPBot"
ROBOTS = "User-agent: *\nDisallow: /private\nCrawl-delay: 3\nSitemap: https://a.test/sitemap.xml\n"


def robots(status, body="", calls=None):
    async def fetch(url):
        if calls is not None:
            calls.append(url)
        if status is None:
            raise OSError("down")
        return status, body

    return RobotsCache(MemCache(), fetch)


async def test_allow_deny_delay_and_sitemaps():
    calls = []
    r = robots(200, ROBOTS, calls)
    assert await r.allowed("https://a.test/public", UA)
    assert not await r.allowed("https://a.test/private/x", UA)
    assert await r.crawl_delay("https://a.test/", UA) == 3.0
    assert await r.sitemaps("https://a.test/") == ["https://a.test/sitemap.xml"]
    assert calls == ["https://a.test/robots.txt"]  # cached after the first fetch


@pytest.mark.parametrize("status", [404, 410, 403])
async def test_4xx_means_allow_all(status):
    assert await robots(status).allowed("https://a.test/private", UA)


@pytest.mark.parametrize("status", [500, 503, None])
async def test_5xx_or_unreachable_means_deny_all(status):
    r = robots(status)
    assert not await r.allowed("https://a.test/", UA)
    assert await r.crawl_delay("https://a.test/", UA) is None


URLSET = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://a.test/1</loc><lastmod>2026-01-01</lastmod></url><url><loc> https://a.test/2 </loc></url></urlset>"""
INDEX = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://a.test/s1.xml.gz</loc></sitemap></sitemapindex>"""


def test_urlset_index_and_gzip():
    urls = parse_sitemap(URLSET)
    assert [(e.loc, e.lastmod, e.is_sitemap) for e in urls] == [
        ("https://a.test/1", "2026-01-01", False),
        ("https://a.test/2", None, False),
    ]
    assert parse_sitemap(INDEX)[0].is_sitemap
    assert len(parse_sitemap(gzip.compress(URLSET))) == 2


def test_sitemap_rejects_entities_bombs_and_garbage():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    for bad in (bomb, b"not xml"):
        with pytest.raises(FetchError):
            parse_sitemap(bad)


def test_normalise():
    assert (
        normalise("HTTPS://Example.COM:443/a/?b=2&a=1&utm_source=x#frag")
        == "https://example.com/a/?a=1&b=2"
    )
    assert normalise("http://example.com") == "http://example.com/"
    assert normalise("http://example.com:8080/x") == "http://example.com:8080/x"
    assert normalise("http://example.com./x") == "http://example.com/x"


def _page(final, canon):
    from datetime import datetime

    return Page(
        "u", final, 200, None, "", [], "h", None, None, datetime.now(), "text/html", "http", canon
    )


def test_canonical_same_host_only():
    assert canonical(_page("https://a.test/x?utm_x=1", "https://a.test/x")) == "https://a.test/x"
    assert canonical(_page("https://a.test/x", "https://evil.test/x")) == "https://a.test/x"
    assert canonical(_page("https://a.test/x", None)) == "https://a.test/x"
