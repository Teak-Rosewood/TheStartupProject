"""Turn an uploaded file into plain text. PDF, DOCX and HTML need the ``parse`` extra."""

from __future__ import annotations

import html.parser
import io


class UnsupportedDocument(ValueError):
    pass


class _Strip(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html(raw: str) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(raw, include_comments=False)
        if text:
            return str(text)
    except ImportError:
        pass
    p = _Strip()
    p.feed(raw)
    return "".join(p.parts)


def extract(data: bytes, content_type: str, filename: str = "") -> str:
    ct = content_type.split(";")[0].strip().lower()
    name = filename.lower()
    if ct == "application/pdf" or name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise UnsupportedDocument("PDF needs the 'parse' extra (pypdf)") from e
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if ct.endswith("wordprocessingml.document") or name.endswith(".docx"):
        try:
            import docx
        except ImportError as e:
            raise UnsupportedDocument("DOCX needs the 'parse' extra (python-docx)") from e
        return "\n\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    if ct in {"text/html", "application/xhtml+xml"} or name.endswith((".html", ".htm")):
        return _html(data.decode("utf-8", "replace"))
    if (
        ct.startswith("text/")
        or ct in {"application/json", ""}
        or name.endswith((".md", ".txt", ".csv"))
    ):
        return data.decode("utf-8", "replace")
    raise UnsupportedDocument(f"cannot read {content_type or 'unknown type'} documents")
