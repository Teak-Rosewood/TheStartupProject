"""Example connector: a recipe site, scraped from its HTML listing with a free JSON API as fallback.

The site, URLs and JSON shape are invented. Copy this into your application repository, adjust
the two parsers, register it by importing the module, and add a row to the sources CSV:

    key,name,url,strategies,schedule,rate_limit_rps
    recipes-example,Example Recipes,https://recipes.example.com,scrape;api_free,daily 02:00,1
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from tsp.crawl import Page
from tsp.sources import (
    ApiPage,
    Connector,
    Entry,
    HttpListStrategy,
    Item,
    JsonApiStrategy,
    Listing,
    register_connector,
)

BASE = "https://recipes.example.com"


_RECIPE = re.compile(r"/recipes/(?P<id>\d+)$")
_NEXT = re.compile(r"/recipes\?page=\d+$")


def parse_listing(page: Page) -> Listing:
    """Recipe pages are the links ending in ``/recipes/<id>``; the next list page ends in ``?page=N``.
    ``Page.links`` is already absolute and normalised, so no HTML parsing is needed here."""
    entries = [
        Entry(external_id=m.group("id"), url=link)
        for link in page.links
        if (m := _RECIPE.search(link))
    ]
    nxt = next((link for link in page.links if _NEXT.search(link)), None)
    return Listing(entries, nxt)


def api_url(stream: str, since: str | None, token: str | None) -> str:
    q = f"?updated_since={since}" if since else "?"
    return f"{BASE}/api/{stream}{q}{'&page=' + token if token else ''}"


def parse_api(doc: Any) -> ApiPage:
    return ApiPage(
        [
            Item(
                external_id=str(r["id"]),
                url=f"{BASE}/recipes/{r['id']}",
                content=r["body"].encode(),
                content_type="text/markdown",
                metadata={"title": r["title"]},
                updated_at=datetime.fromisoformat(r["updated"]),
            )
            for r in doc["results"]
        ],
        doc.get("next"),
    )


@register_connector
class RecipesConnector(Connector):
    key = "recipes-example"
    streams = ["recipes"]

    def strategies(self) -> dict[str, Any]:
        return {
            "scrape": HttpListStrategy(lambda stream, since: f"{BASE}/{stream}", parse_listing),
            "api_free": JsonApiStrategy(api_url, parse_api, auth_key="recipes_api_key"),
        }
