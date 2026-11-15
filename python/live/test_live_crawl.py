"""tsp.crawl against real pages: plain HTTP with Trafilatura, Crawl4AI rendering, Docling PDFs,
and the SearXNG search tool."""

import importlib.util
import os

import pytest
from tsp.crawl import HttpFetcher, Page

PAGES = [
    "https://example.com/",
    "https://www.python.org/",
    "https://en.wikipedia.org/wiki/Web_crawler",
    "https://docs.python.org/3/library/asyncio.html",
    "https://www.rfc-editor.org/rfc/rfc9309.html",
]
PDF = os.environ.get(
    "TSP_LIVE_PDF_URL", "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
)


@pytest.mark.parametrize("url", PAGES)
async def test_http_fetch_extracts_markdown(url):
    fetcher = HttpFetcher()
    try:
        page = await fetcher.fetch(url)
    finally:
        await fetcher.aclose()
    assert isinstance(page, Page) and page.status == 200
    assert len(page.markdown) > 100 or url == "https://example.com/"
    assert page.content_hash


@pytest.mark.skipif(importlib.util.find_spec("crawl4ai") is None, reason="needs tsp-crawl[browser]")
async def test_browser_render_with_crawl4ai():
    from tsp.crawl.fetch_browser import BrowserFetcher

    page = await BrowserFetcher().fetch("https://example.com/")
    assert page.status == 200 and "Example Domain" in page.markdown


@pytest.mark.skipif(importlib.util.find_spec("docling") is None, reason="needs tsp-crawl[pdf]")
async def test_pdf_with_docling():
    from tsp.crawl.fetch_pdf import PdfFetcher

    http = HttpFetcher()
    try:
        page = await PdfFetcher(http).fetch(PDF)
    finally:
        await http.aclose()
    assert page.content_type == "application/pdf" and page.markdown.strip()


async def test_searxng_search():
    from live_support import need_env
    from tsp.crawl import SearxngSearch

    env = need_env("SEARXNG_URL")
    results = await SearxngSearch(env["SEARXNG_URL"], timeout=30).search("python asyncio", 5)
    assert results and all(r.url.startswith("http") for r in results)
