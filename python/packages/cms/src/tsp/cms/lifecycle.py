"""Review cadence: published items are reviewed every cycle; no decision means they freeze."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import Item

KINDS = ("correction", "clarification", "update")


@dataclass(frozen=True)
class LifecycleConfig:
    """``review_after_days`` after publishing (and after each ``living`` decision) a review falls due;
    if nobody decides within ``auto_freeze_after_days`` the item freezes. ``by_type`` overrides both
    per item type: ``{"news": (7, 3)}``. ``freeze_notes`` is the public update note per language."""

    review_after_days: int = 30
    auto_freeze_after_days: int = 7
    by_type: dict[str, tuple[int, int]] = field(default_factory=dict)
    freeze_notes: dict[str, str] = field(
        default_factory=lambda: {
            "en": "This article is no longer updated as of {date}.",
            "hi": "इस लेख को {date} से अपडेट नहीं किया जा रहा है।",
        }
    )

    def for_type(self, item_type: str) -> tuple[int, int]:
        return self.by_type.get(item_type, (self.review_after_days, self.auto_freeze_after_days))

    def review_due(self, item_type: str, after: datetime) -> datetime:
        return after + timedelta(days=self.for_type(item_type)[0])

    def freeze_deadline(self, item_type: str, due_at: datetime) -> datetime:
        return due_at + timedelta(days=self.for_type(item_type)[1])

    def freeze_note(self, lang: str, at: datetime) -> str:
        template = (
            self.freeze_notes.get(lang) or self.freeze_notes.get("en") or "Frozen as of {date}."
        )
        return template.format(date=at.strftime("%d %B %Y").lstrip("0"))


def is_refreshable(item: Item) -> bool:
    """Whether a job may rewrite, re-translate or re-fetch this item. Frozen and unpublished items are
    left alone; call this at the top of every job that touches published content."""
    return item.state == "published" and item.lifecycle != "frozen"
