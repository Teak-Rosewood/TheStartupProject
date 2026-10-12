from datetime import datetime
from pathlib import Path

import httpx
import pytest
from crawl_fakes import MemCache
from tsp.crawl import (
    BrowserFetcher,
    ChainFetcher,
    DomainLimiter,
    HttpFetcher,
    NotModified,
    PdfFetcher,
    RateLimited,
    RenderUnavailable,
    needs_render,
)
from tsp.crawl.fetch_browser import Rendered
from tsp.crawl.page import Page

FIX = Path(__file__).parent / "fixtures"


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def now(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(s)
        self.t += s


def limiter(rps=1.0):
    c = Clock()
    return DomainLimiter(MemCache(), rps, clock=c.now, sleep=c.sleep), c


async def test_second_acquire_waits_for_next_window():
    lim, c = limiter(1.0)
    await lim.acquire("a.test")
    await lim.acquire("a.test")
    assert len(c.slept) == 1 and 0 < c.slept[0] <= 1
    await lim.acquire("b.test")  # other host unaffected
    assert len(c.slept) == 1


async def test_crawl_delay_slows_host():
    lim, c = limiter(5.0)
    await lim.acquire("a.test", delay=4)
    await lim.acquire("a.test", delay=4)
    assert 0 < c.slept[0] <= 4


async def test_429_backoff_honours_retry_after_and_clears_on_success():
    lim, c = limiter()
    await lim.report("a.test", 429, retry_after=30)
    await lim.acquire("a.test")
    assert sum(c.slept) >= 30
    await lim.report("a.test", 503)
    await lim.report("a.test", 200)
    before = len(c.slept)
    await lim.acquire("a.test")
    assert len(c.slept) == before or c.slept[-1] <= 1


async def test_backoff_longer_than_max_wait_raises():
    lim, _ = limiter()
    await lim.report("a.test", 429, retry_after=600)
    with pytest.raises(RateLimited):
        await lim.acquire("a.test", max_wait=60)


async def test_repeated_503_backs_off_exponentially():
    lim, c = limiter()
    await lim.report("a.test", 503)
    first = c.t + 2
    await lim.report("a.test", 503)
    assert await lim._cache.get_json("crawl:backoff:a.test") > first


def html_fetcher(fixture):
    async def resolve(host):
        return ["93.184.216.34"]

    def handler(req):
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=(FIX / fixture).read_bytes()
        )

    return HttpFetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolver=resolve)


def browser(calls):
    async def resolve(host):
        return ["93.184.216.34"]

    async def render(url):
        calls.append(url)
        return Rendered(
            url, 200, "App", "# Dashboard\n" + "Real rendered content. " * 30, ["https://a.test/x"]
        )

    return BrowserFetcher(render, resolver=resolve)


async def test_js_only_page_falls_back_to_browser():
    calls = []
    chain = ChainFetcher(html_fetcher("js_only.html"), browser(calls))
    page = await chain.fetch("https://a.test/app")
    assert page.fetcher == "browser" and "Real rendered content" in page.markdown
    assert calls == ["https://a.test/app"]


async def test_good_page_does_not_render():
    calls = []
    page = await ChainFetcher(html_fetcher("help_article.html"), browser(calls)).fetch(
        "https://a.test/"
    )
    assert page.fetcher == "http" and calls == []


async def test_missing_browser_extra_keeps_http_result():
    async def broken(url):
        raise RenderUnavailable("no")

    async def resolve(host):
        return ["93.184.216.34"]

    chain = ChainFetcher(html_fetcher("js_only.html"), BrowserFetcher(broken, resolver=resolve))
    assert (await chain.fetch("https://a.test/app")).fetcher == "http"


async def test_browser_final_url_must_be_safe():
    from tsp.crawl import UnsafeURL

    async def resolve(host):
        return ["10.0.0.1"] if host == "internal.corp" else ["93.184.216.34"]

    async def render(url):
        return Rendered("http://internal.corp/", 200, None, "x")

    with pytest.raises(UnsafeURL):
        await BrowserFetcher(render, resolver=resolve).fetch("https://a.test/")


async def test_pdf_routed_to_pdf_fetcher_without_second_download():
    downloads = []

    async def resolve(host):
        return ["93.184.216.34"]

    def handler(req):
        downloads.append(req)
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7 data"
        )

    http = HttpFetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolver=resolve)
    pdf = PdfFetcher(http, converter=lambda data, name: f"# {name}\n{len(data)} bytes")
    page = await ChainFetcher(http, pdf=pdf).fetch("https://a.test/files/guide.pdf")
    assert page.fetcher == "pdf" and page.markdown.startswith("# guide.pdf") and len(downloads) == 1


async def test_not_modified_passes_through():
    async def resolve(host):
        return ["93.184.216.34"]

    http = HttpFetcher(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(304))),
        resolver=resolve,
    )
    assert isinstance(
        await ChainFetcher(http, browser([])).fetch("https://a.test/", etag="x"), NotModified
    )


def test_needs_render_heuristic():
    def p(md):
        return Page(
            "u", "u", 200, None, md, [], "h", None, None, datetime.now(), "text/html", "http"
        )

    assert needs_render(p("short"))
    assert needs_render(p("x" * 500 + " Please enable JavaScript to continue"))
    assert not needs_render(p("word " * 100))
