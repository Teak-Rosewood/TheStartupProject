"""Newsletter digests, sent through ``tsp.notify`` as marketing email. Consent, suppression and the
unsubscribe link are enforced by the notifier. Open and click tracking are not built: links point
straight at your site and there is no tracking pixel."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from tsp.i18n import Catalog
from tsp.notify.mailer import MARKETING, Notifier

from .items import Item

TEMPLATE = "digest"
Segment = Literal["all", "subscribers", "free"]


def templates_dir() -> Path:
    """Pass this to ``Templates(...)`` so the notifier can find the ``digest`` template."""
    return Path(__file__).parent / "templates"


@dataclass(frozen=True)
class Recipient:
    email: str
    lang: str = "en"
    subscriber: bool = False  # paying reader


def in_segment(r: Recipient, segment: Segment) -> bool:
    return segment == "all" or (segment == "subscribers") == r.subscriber


def digest_context(
    items: Iterable[Item], lang: str, base_url: str, catalog: Catalog, *, default_lang: str = "en"
) -> dict[str, Any]:
    """Everything the template needs, already translated. Items that have no variant in ``lang``
    appear in the default language rather than being dropped."""
    rows = []
    for item in items:
        code, v = item.variant(lang, default_lang)
        rows.append({"title": v.title, "summary": v.summary, "url": item.url(base_url, code)})
    return {
        "lang": lang,
        "subject_line": catalog.t("newsletter.subject", lang, count=len(rows)),
        "intro": catalog.t("newsletter.intro", lang),
        "read_more": catalog.t("newsletter.read_more", lang),
        "items": rows,
    }


async def send_digest(
    session: AsyncSession,
    notifier: Notifier,
    catalog: Catalog,
    items: list[Item],
    recipients: Iterable[Recipient],
    *,
    segment: Segment = "all",
    languages: Iterable[str] = ("en",),
    default_lang: str = "en",
) -> dict[str, int]:
    """Queue one digest per recipient in their language. Returns how many were queued per language.
    A recipient whose language is not in ``languages`` gets the default language. Whether each message
    is actually sent is decided at delivery: no marketing consent or a suppressed address means no email."""
    allowed = set(languages)
    counts: dict[str, int] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for r in recipients:
        if not in_segment(r, segment):
            continue
        lang = r.lang if r.lang in allowed else default_lang
        ctx = contexts.get(lang)
        if ctx is None:
            ctx = contexts[lang] = digest_context(
                items, lang, notifier.base_url, catalog, default_lang=default_lang
            )
        await notifier.queue(session, r.email, TEMPLATE, ctx, category=MARKETING)
        counts[lang] = counts.get(lang, 0) + 1
    return counts
