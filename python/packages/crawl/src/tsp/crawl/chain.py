from __future__ import annotations

from collections.abc import Callable

from .errors import FetchError, RenderUnavailable, UnsupportedContentType
from .fetch_browser import BrowserFetcher
from .fetch_http import HttpFetcher
from .fetch_pdf import PdfFetcher
from .page import NotModified, Page

_JS_MARKERS = (
    "enable javascript",
    "requires javascript",
    "javascript is required",
    "javascript must be enabled",
    "you need to enable javascript",
)
MIN_CHARS = 200


def needs_render(page: Page) -> bool:
    """Heuristic: too little text, or the page tells the reader to enable JavaScript."""
    text = page.markdown.lower()
    return len(text.strip()) < MIN_CHARS or any(m in text for m in _JS_MARKERS)


class ChainFetcher:
    """HTTP first; PDFs go to ``pdf``; thin or JS-only pages fall back to ``browser``.

    If the browser is missing or fails, the plain HTTP result is returned rather than nothing."""

    name = "chain"

    def __init__(
        self,
        http: HttpFetcher,
        browser: BrowserFetcher | None = None,
        pdf: PdfFetcher | None = None,
        *,
        needs_render: Callable[[Page], bool] = needs_render,
    ):
        self._http = http
        self._browser = browser
        self._pdf = pdf
        self._needs_render = needs_render

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Page | NotModified:
        try:
            result = await self._http.fetch(url, etag=etag, last_modified=last_modified)
        except UnsupportedContentType as e:
            if self._pdf is None or e.content_type != "application/pdf":
                raise
            return await self._pdf.from_raw(e.raw)
        if (
            isinstance(result, NotModified)
            or self._browser is None
            or not self._needs_render(result)
        ):
            return result
        try:
            rendered = await self._browser.fetch(url)
        except (RenderUnavailable, FetchError):
            return result
        return rendered
