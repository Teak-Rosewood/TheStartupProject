from __future__ import annotations

from typing import Any

from tsp.i18n import Catalog

from .items import Item

X_LIMIT, X_URL_COST = 280, 23  # X counts every link as 23 characters


def _clip(text: str, room: int) -> str:
    return text if len(text) <= room else text[: max(0, room - 1)].rstrip() + "…"


def social_copy(item: Item, lang: str, url: str, catalog: Catalog | None = None) -> dict[str, str]:
    """Ready-to-paste text per platform. Posting stays manual by design."""
    code, v = item.variant(lang)
    by = ""
    if item.authors and catalog:
        by = catalog.t("share.recipe_by", code, author=", ".join(item.authors))
    tags = " ".join("#" + k.replace(" ", "") for k in item.keywords[:3])
    x_room = X_LIMIT - X_URL_COST - 1 - (len(tags) + 1 if tags else 0)
    x = " ".join(p for p in (_clip(v.title, x_room), tags, url) if p)
    body = "\n\n".join(p for p in (v.title, v.summary, by) if p)
    return {
        "x": x,
        "linkedin": f"{body}\n\n{url}",
        "facebook": f"{v.title}\n{url}",
        "whatsapp": f"{v.title}\n{url}",
        "email_subject": v.title,
    }


def social_meta(
    item: Item,
    lang: str,
    base_url: str,
    *,
    image_url: str,
    site_name: str,
    twitter_handle: str | None = None,
) -> list[dict[str, str]]:
    """``og:*`` and ``twitter:*`` meta tags as ``{"property"|"name": ..., "content": ...}`` dicts."""
    code, v = item.variant(lang)
    tags = [
        {"property": "og:type", "content": "article"},
        {"property": "og:site_name", "content": site_name},
        {"property": "og:title", "content": v.title},
        {"property": "og:description", "content": v.summary},
        {"property": "og:url", "content": item.url(base_url, code)},
        {"property": "og:image", "content": image_url},
        {"property": "og:image:width", "content": "1200"},
        {"property": "og:image:height", "content": "630"},
        {"property": "og:locale", "content": code},
        {"name": "twitter:card", "content": "summary_large_image"},
        {"name": "twitter:title", "content": v.title},
        {"name": "twitter:description", "content": v.summary},
        {"name": "twitter:image", "content": image_url},
    ]
    if twitter_handle:
        tags.append({"name": "twitter:site", "content": twitter_handle})
    return tags


def meta_html(tags: list[dict[str, Any]]) -> str:
    from html import escape

    return "\n".join(
        "<meta " + " ".join(f'{k}="{escape(str(v), quote=True)}"' for k, v in t.items()) + ">"
        for t in tags
    )
