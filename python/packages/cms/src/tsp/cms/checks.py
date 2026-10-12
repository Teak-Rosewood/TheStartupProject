from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import Item, Version


@dataclass
class CheckResult:
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


class PublishCheck(Protocol):
    """One rule an item must satisfy to publish. A failed ``blocking`` check stops publishing; a
    failed non-blocking one is recorded as a warning. ``run`` may query through ``ctx.session``."""

    name: str
    blocking: bool

    async def run(self, ctx: CheckContext, item: Item, version: Version) -> CheckResult: ...


@dataclass
class CheckContext:
    session: Any  # AsyncSession
    variants: dict[str, str]  # lang -> variant state, for the item being checked


class CheckRegistry:
    """Checks per item type; ``"*"`` applies to every type."""

    def __init__(self) -> None:
        self._by_type: dict[str, list[PublishCheck]] = {}

    def register(self, item_type: str, check: PublishCheck) -> None:
        checks = self._by_type.setdefault(item_type, [])
        if any(c.name == check.name for c in checks):
            raise ValueError(f"check {check.name!r} is already registered for {item_type!r}")
        checks.append(check)

    def for_type(self, item_type: str) -> list[PublishCheck]:
        return [*self._by_type.get("*", []), *self._by_type.get(item_type, [])]


class RequiredFields:
    name, blocking = "required_fields", True

    async def run(self, ctx: CheckContext, item: Item, version: Version) -> CheckResult:
        missing = [
            n
            for n, ok in (
                ("slug", bool(item.slug.strip())),
                ("title", bool(version.title.strip())),
                ("body", bool(version.body)),
            )
            if not ok
        ]
        return CheckResult(not missing, {"missing": missing})


class DisclosurePresent:
    """Content that an AI wrote or helped write must say so, in a disclosure block."""

    name, blocking = "disclosure_present", True

    async def run(self, ctx: CheckContext, item: Item, version: Version) -> CheckResult:
        if version.author_kind == "human":
            return CheckResult(True)
        has = any(
            d.get("type") == "ai_assistance" and str(d.get("text", "")).strip()
            for d in version.disclosures
        )
        return CheckResult(has, {"author_kind": version.author_kind, "needs": "ai_assistance"})


class AllVariantsReady:
    """Every listed language has a variant that is ready (or already published)."""

    name, blocking = "all_variants_ready", True

    def __init__(self, languages: Iterable[str]):
        self.languages = tuple(languages)

    async def run(self, ctx: CheckContext, item: Item, version: Version) -> CheckResult:
        not_ready = [
            lang for lang in self.languages if ctx.variants.get(lang) not in ("ready", "published")
        ]
        return CheckResult(not not_ready, {"not_ready": not_ready})


def default_registry() -> CheckRegistry:
    reg = CheckRegistry()
    reg.register("*", RequiredFields())
    reg.register("*", DisclosurePresent())
    return reg
