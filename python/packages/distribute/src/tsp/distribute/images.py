"""AI-generated illustrations with provenance and a visible label. The label is a publishing rule:
``unlabelled_ai_images`` finds images that lack it, and the CMS publish check should refuse them."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from typing import Any, Protocol

import httpx
from tsp.i18n import Catalog


@dataclass
class GeneratedImage:
    data: bytes
    mime: str
    model: str
    provider: str = ""
    c2pa: dict[str, Any] | None = None  # content credentials, when the provider supplies them


class ImageGenerator(Protocol):
    async def generate(self, prompt: str, style: str, size: str) -> GeneratedImage: ...


@dataclass
class Provenance:
    """Stored next to every image so you can always say what made it and from which prompt."""

    id: str
    model: str
    provider: str
    prompt_sha256: str  # the prompt itself is not kept; its hash proves which one was used
    style: str
    size: str
    created_at: str
    mime: str
    key: str
    c2pa: dict[str, Any] | None = None
    ai_generated: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class OpenAIImageGenerator:
    """Any provider with the OpenAI ``/images/generations`` shape."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "gpt-image-1",
        provider: str = "openai",
        timeout: float = 120,
    ):
        self.base, self.key, self.model, self.provider = (
            base_url.rstrip("/"),
            api_key,
            model,
            provider,
        )
        self._http = httpx.AsyncClient(timeout=timeout)

    async def generate(self, prompt: str, style: str, size: str) -> GeneratedImage:
        full = f"{prompt}\n\nStyle: {style}" if style else prompt
        r = await self._http.post(
            f"{self.base}/images/generations",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "prompt": full,
                "size": size,
                "n": 1,
                "response_format": "b64_json",
            },
        )
        r.raise_for_status()
        entry = r.json()["data"][0]
        if "b64_json" not in entry:
            raise ValueError("the provider returned no image data")
        return GeneratedImage(
            base64.b64decode(entry["b64_json"]),
            "image/png",
            self.model,
            self.provider,
            entry.get("c2pa"),
        )

    async def close(self) -> None:
        await self._http.aclose()


async def generate_image(
    generator: ImageGenerator, store: Any, prompt: str, *, style: str = "", size: str = "1024x1024"
) -> Provenance:
    """Generate, store the bytes and a provenance record (``images/<id>.json``), return the record."""
    img = await generator.generate(prompt, style, size)
    image_id = uuid.uuid4().hex
    ext = "png" if img.mime.endswith("png") else "jpg" if "jpeg" in img.mime else "bin"
    key = f"images/{image_id}.{ext}"
    prov = Provenance(
        id=image_id,
        model=img.model,
        provider=img.provider,
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        style=style,
        size=size,
        created_at=datetime.now(UTC).isoformat(),
        mime=img.mime,
        key=key,
        c2pa=img.c2pa,
    )
    await store.write(key, img.data, img.mime)
    await store.write(
        f"images/{image_id}.json", json.dumps(asdict(prov)).encode(), "application/json"
    )
    return prov


def ai_figure(image_url: str, alt: str, image_id: str, lang: str, catalog: Catalog) -> str:
    """The only markup that passes the publish check: an image inside a figure whose caption says
    it is AI-generated, in the reader's language."""
    label = catalog.t("ai_image.label", lang)
    return (
        f'<figure class="ai-image"><img src="{escape(image_url, quote=True)}" alt="{escape(alt, quote=True)}" '
        f'data-ai-image="{escape(image_id, quote=True)}"><figcaption class="ai-label">{escape(label)}</figcaption></figure>'
    )


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, Any]] = []
        self.unlabelled: list[str] = []
        self._cap = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "figure":
            self.stack.append({"images": [], "labelled": False})
        elif tag == "img" and a.get("data-ai-image"):
            if self.stack:
                self.stack[-1]["images"].append(a["data-ai-image"])
            else:
                self.unlabelled.append(str(a["data-ai-image"]))
        elif tag == "figcaption" and "ai-label" in (a.get("class") or "").split():
            self._cap += 1

    def handle_data(self, data: str) -> None:
        if self._cap and data.strip() and self.stack:
            self.stack[-1]["labelled"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "figcaption" and self._cap:
            self._cap -= 1
        elif tag == "figure" and self.stack:
            fig = self.stack.pop()
            if not fig["labelled"]:
                self.unlabelled.extend(fig["images"])


def unlabelled_ai_images(html: str) -> list[str]:
    """Ids of AI images (``data-ai-image``) not sitting in a ``<figure>`` with a non-empty ``figcaption.ai-label``."""
    p = _Scan()
    p.feed(html)
    p.close()
    return p.unlabelled


def ai_image_labelled(html: str, required_ids: list[str] | None = None) -> bool:
    """K4 publish check. True when every AI image in ``html`` is labelled and, if ``required_ids``
    is given, each of those images actually appears (an image cannot dodge the check by being left out
    of the markup but still shipped as the cover)."""
    if unlabelled_ai_images(html):
        return False
    return all(f'data-ai-image="{i}"' in html for i in required_ids or [])
