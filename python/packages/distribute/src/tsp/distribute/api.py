from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from tsp.i18n import Catalog

from .cards import get_or_render
from .items import Item
from .seo import news_sitemap, robots_txt, sitemap, sitemap_index
from .social import social_copy

XML = "application/xml"


def build_router(
    items: Callable[[], Awaitable[list[Item]]],
    *,
    base_url: str,
    languages: Sequence[str],
    publication: str,
    catalog: Catalog,
    store: Any | None = None,
    allow_ai_crawlers: bool = True,
) -> APIRouter:
    """Public, cacheable routes for search engines and sharing. Mount them at the site root
    (``/sitemap.xml``, ``/robots.txt``); ``items`` returns the published items."""
    r = APIRouter()
    base = base_url.rstrip("/")

    async def find(item_id: str) -> Item:
        found = next((i for i in await items() if i.id == item_id), None)
        if found is None:
            raise HTTPException(404, "not found")
        return found

    @r.get("/robots.txt")
    async def robots() -> Response:
        return Response(
            robots_txt(base, allow_ai_crawlers=allow_ai_crawlers), media_type="text/plain"
        )

    @r.get("/sitemap.xml")
    async def index() -> Response:
        return Response(sitemap_index(base, languages), media_type=XML)

    @r.get("/sitemap-news.xml")
    async def news() -> Response:
        return Response(news_sitemap(await items(), base, languages, publication), media_type=XML)

    @r.get("/sitemap-{lang}.xml")
    async def per_language(lang: str) -> Response:
        if lang not in languages:
            raise HTTPException(404, "not found")
        return Response(sitemap(await items(), base, languages, lang), media_type=XML)

    @r.get("/distribute/social-copy/{item_id}/{lang}")
    async def copy(item_id: str, lang: str) -> dict[str, str]:
        item = await find(item_id)
        code, _ = item.variant(lang)
        return social_copy(item, code, item.url(base, code), catalog)

    if store is not None:

        @r.get("/distribute/card/{item_id}/{lang}")
        async def card(item_id: str, lang: str) -> dict[str, str]:
            """The storage key of the card (rendered on first request). Serve the object from your
            bucket or CDN; the key changes when the item's text does."""
            return {
                "key": await get_or_render(store, await find(item_id), lang, site_name=publication)
            }

    return r
