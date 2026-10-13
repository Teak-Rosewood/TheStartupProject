from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO

from .errors import FetchError, RenderUnavailable, UnsupportedContentType
from .fetch_http import HttpFetcher, RawResponse
from .page import NotModified, Page, content_hash

Converter = Callable[[bytes, str], str]  # (pdf bytes, file name) -> Markdown


def _docling_converter(ocr_languages: list[str]) -> Converter:
    try:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as e:
        raise RenderUnavailable("install tsp-crawl[pdf] to read PDFs") from e
    options = PdfPipelineOptions()
    options.ocr_options.lang = ocr_languages
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )

    def convert(data: bytes, name: str) -> str:
        result = converter.convert(DocumentStream(name=name, stream=BytesIO(data)))
        return str(result.document.export_to_markdown())

    return convert


class PdfFetcher:
    """Downloads through ``HttpFetcher`` (same SSRF and size rules), converts with Docling."""

    name = "pdf"

    def __init__(
        self,
        http: HttpFetcher,
        *,
        ocr_languages: list[str] | None = None,
        converter: Converter | None = None,
    ):
        self._http = http
        self._ocr = ocr_languages or ["en"]
        self._converter = converter

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Page | NotModified:
        raw = await self._http.fetch_raw(url, etag=etag, last_modified=last_modified)
        if raw.status == 304:
            return NotModified(url, raw.etag or etag, raw.last_modified or last_modified)
        if raw.status >= 400:
            raise FetchError(f"HTTP {raw.status} for {url}", status=raw.status)
        return await self.from_raw(raw)

    async def from_raw(self, raw: RawResponse) -> Page:
        if raw.content_type != "application/pdf":
            raise UnsupportedContentType(raw.content_type, raw)
        if self._converter is None:
            self._converter = _docling_converter(self._ocr)
        name = raw.final_url.rsplit("/", 1)[-1] or "document.pdf"
        markdown = await asyncio.to_thread(self._converter, raw.body, name)
        return Page(
            url=raw.url,
            final_url=raw.final_url,
            status=raw.status,
            title=None,
            markdown=markdown,
            links=[],
            content_hash=content_hash(markdown),
            etag=raw.etag,
            last_modified=raw.last_modified,
            fetched_at=datetime.now(UTC),
            content_type="application/pdf",
            fetcher=self.name,
        )
