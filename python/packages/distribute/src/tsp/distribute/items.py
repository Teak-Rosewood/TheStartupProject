from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Variant:
    """One language version of an item."""

    title: str
    summary: str = ""
    url: str | None = None  # absolute or site-relative; defaults to /<lang>/<slug>


@dataclass(frozen=True)
class Item:
    """What distribution needs to know about a published piece. Your CMS adapts its rows to this."""

    id: str
    slug: str
    published_at: datetime
    variants: dict[str, Variant]
    updated_at: datetime | None = None
    authors: tuple[str, ...] = ()
    image_url: str | None = None
    free: bool = True  # False: behind a paywall
    section: str = "recipes"
    keywords: tuple[str, ...] = ()
    paywall_selector: str = ".paywalled"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def languages(self) -> list[str]:
        return sorted(self.variants)

    def variant(self, lang: str, default: str = "en") -> tuple[str, Variant]:
        """The requested language, else the default, else whatever exists: (language, variant)."""
        for code in (lang, default, *self.languages):
            if code in self.variants:
                return code, self.variants[code]
        raise KeyError(f"item {self.id} has no variants")

    def path(self, lang: str) -> str:
        return f"/{lang}/{self.slug}"

    def url(self, base_url: str, lang: str) -> str:
        v = self.variants.get(lang)
        if v and v.url:
            return v.url if v.url.startswith("http") else base_url.rstrip("/") + v.url
        return base_url.rstrip("/") + self.path(lang)
