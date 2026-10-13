from __future__ import annotations

import difflib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .checks import CheckContext, CheckRegistry, CheckResult, default_registry
from .lifecycle import KINDS, LifecycleConfig
from .models import CheckResultRow, Correction, DisclosureTemplate, Item, Review, Variant, Version

SYSTEM = Actor("system", "system")
DEFAULT_DISCLOSURES = {
    "ai_assistance": "Parts of this content were written with the help of AI and reviewed by our editors.",
    "methodology": "How we researched and tested this content is described in our methodology.",
    "conflict": "We have a financial or personal interest related to this content.",
}


class CmsError(Exception):
    """A request the item's current state does not allow."""


class PublishBlocked(CmsError):
    def __init__(self, failed: list[tuple[str, CheckResult]]):
        self.failed = failed
        super().__init__("blocked by: " + ", ".join(f"{n} {r.details}" for n, r in failed))


@dataclass
class Diff:
    title: tuple[str, str] | None  # (old, new) when the title changed
    ops: list[tuple[str, list[Any], list[Any]]] = field(
        default_factory=list
    )  # (op, old blocks, new blocks)


def _canon(block: Any) -> str:
    return json.dumps(block, sort_keys=True, ensure_ascii=False)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CmsService:
    """Items, versions, language variants and the publish workflow for one session.

    Flow: ``draft`` -> ``submit`` -> ``in_review`` -> ``approve`` -> ``approved`` -> ``publish`` (or
    ``schedule`` then the ``cms.publish_due`` job). Editing the content of an item in review or
    approved sends it back to ``draft``, so what is published is what was approved. A published item
    keeps serving its published version while a new draft is being worked on.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        checks: CheckRegistry | None = None,
        clock: Callable[[], datetime] = _utcnow,
        allow_self_approval: bool = False,
        lifecycle: LifecycleConfig | None = None,
        notify_review_due: Callable[[AsyncSession, Item, datetime], Awaitable[None]] | None = None,
    ):
        self.s = session
        self.checks = checks or default_registry()
        self.now = clock
        self.allow_self_approval = allow_self_approval
        self.lifecycle = lifecycle or LifecycleConfig()
        self.notify_review_due = notify_review_due

    # -- reading -----------------------------------------------------------------------------------

    async def get(self, org_id: str, item_id: uuid.UUID) -> Item:
        item = await self.s.get(Item, item_id)
        if item is None or item.org_id != org_id:
            raise LookupError(f"no item {item_id}")
        return item

    async def by_slug(self, org_id: str, slug: str) -> Item:
        item = (
            await self.s.execute(select(Item).where(Item.org_id == org_id, Item.slug == slug))
        ).scalar_one_or_none()
        if item is None:
            raise LookupError(f"no item {slug!r}")
        return item

    async def get_published(
        self, org_id: str, slug: str, lang: str | None = None
    ) -> Version | None:
        """What readers see: the published version, in ``lang`` when that variant is published."""
        item = await self.by_slug(org_id, slug)
        if item.published_version_id is None:
            return None
        if lang and lang != item.primary_lang:
            v = (
                await self.s.execute(
                    select(Version)
                    .join(Variant, Variant.version_id == Version.id)
                    .where(
                        Variant.item_id == item.id,
                        Variant.lang == lang,
                        Variant.state == "published",
                    )
                )
            ).scalar_one_or_none()
            return v
        return await self.s.get(Version, item.published_version_id)

    async def versions(self, org_id: str, item_id: uuid.UUID) -> list[Version]:
        await self.get(org_id, item_id)
        return list(
            (
                await self.s.execute(
                    select(Version).where(Version.item_id == item_id).order_by(Version.number)
                )
            ).scalars()
        )

    async def variants(self, item_id: uuid.UUID) -> dict[str, Variant]:
        rows = (await self.s.execute(select(Variant).where(Variant.item_id == item_id))).scalars()
        return {v.lang: v for v in rows}

    # -- writing -----------------------------------------------------------------------------------

    async def create(
        self,
        org_id: str,
        type: str,
        slug: str,
        created_by: str | None = None,
        *,
        primary_lang: str = "en",
        attrs: dict[str, Any] | None = None,
    ) -> Item:
        if (
            await self.s.execute(
                select(func.count()).where(Item.org_id == org_id, Item.slug == slug)
            )
        ).scalar_one():
            raise CmsError(f"slug {slug!r} is already used")
        item = Item(
            org_id=org_id,
            type=type,
            slug=slug,
            primary_lang=primary_lang,
            created_by=created_by,
            attrs=attrs or {},
        )
        self.s.add(item)
        await self.s.flush()
        return item

    async def save_version(
        self,
        org_id: str,
        item_id: uuid.UUID,
        lang: str,
        *,
        title: str,
        body: list[dict[str, Any]],
        author_kind: str = "human",
        created_by: str | None = None,
        summary: str = "",
        disclosures: list[dict[str, Any]] | None = None,
        ai_run_id: uuid.UUID | None = None,
        change_note: str = "",
    ) -> Version:
        """Store a new version (a copy, never an edit) and make it the latest in ``lang``."""
        item = await self.get(org_id, item_id)
        if author_kind not in ("human", "ai", "mixed"):
            raise CmsError("author_kind must be human, ai or mixed")
        number = (
            await self.s.execute(
                select(func.coalesce(func.max(Version.number), 0)).where(Version.item_id == item_id)
            )
        ).scalar_one() + 1
        v = Version(
            item_id=item_id,
            number=number,
            lang=lang,
            title=title,
            body=body,
            summary=summary,
            disclosures=disclosures or [],
            author_kind=author_kind,
            ai_run_id=ai_run_id,
            created_by=created_by,
            change_note=change_note,
        )
        self.s.add(v)
        await self.s.flush()
        await self.s.execute(
            insert(Variant)
            .values(item_id=item_id, lang=lang, version_id=v.id, state="draft")
            .on_conflict_do_update(
                index_elements=["item_id", "lang"], set_={"version_id": v.id, "state": "draft"}
            )
        )
        item.current_version_id = v.id if lang == item.primary_lang else item.current_version_id
        if item.current_version_id is None:
            item.current_version_id = v.id
        if item.state in ("in_review", "approved", "scheduled"):
            item.state, item.publish_at = "draft", None  # approval covered the old content
        await self.s.flush()
        return v

    async def mark_variant_ready(self, org_id: str, item_id: uuid.UUID, lang: str) -> None:
        item = await self.get(org_id, item_id)
        var = (await self.variants(item.id)).get(lang)
        if var is None:
            raise CmsError(f"no {lang!r} variant")
        if var.state == "draft":
            var.state = "ready"

    async def set_disclosure_template(self, org_id: str, type: str, lang: str, text: str) -> None:
        await self.s.execute(
            insert(DisclosureTemplate)
            .values(org_id=org_id, type=type, lang=lang, text=text)
            .on_conflict_do_update(index_elements=["org_id", "type", "lang"], set_={"text": text})
        )

    async def disclosure(self, org_id: str, type: str, lang: str) -> dict[str, str]:
        """A disclosure block with the organization's wording for ``lang`` (else its wording for the
        base language, else the built-in English text)."""
        for candidate in (lang, lang.split("-")[0], "en"):
            row = await self.s.get(DisclosureTemplate, (org_id, type, candidate))
            if row:
                return {"type": type, "text": row.text}
        if type not in DEFAULT_DISCLOSURES:
            raise CmsError(f"unknown disclosure type {type!r}")
        return {"type": type, "text": DEFAULT_DISCLOSURES[type]}

    # -- workflow ----------------------------------------------------------------------------------

    async def _audit(self, item: Item, action: str, actor: Actor, **meta: Any) -> None:
        await record(
            self.s, f"cms.item.{action}", actor=actor, target=("cms_item", str(item.id)),
            org_id=item.org_id, slug=item.slug, **meta,
        )  # fmt: skip

    def _need(self, item: Item, *states: str) -> None:
        if item.state not in states:
            raise CmsError(f"item is {item.state}; this needs {' or '.join(states)}")

    async def submit(self, org_id: str, item_id: uuid.UUID, actor: Actor) -> Item:
        item = await self.get(org_id, item_id)
        if item.current_version_id is None:
            raise CmsError("save a version first")
        if item.state == "published" and item.current_version_id == item.published_version_id:
            raise CmsError("nothing to review: the current version is already published")
        self._need(item, "draft", "published", "unpublished")
        item.state = "in_review"
        await self._audit(item, "submit", actor, version=str(item.current_version_id))
        return item

    async def approve(self, org_id: str, item_id: uuid.UUID, reviewer: Actor) -> Item:
        item = await self.get(org_id, item_id)
        self._need(item, "in_review")
        v = await self.s.get(Version, item.current_version_id)
        if v and not self.allow_self_approval and v.created_by and v.created_by == reviewer.sub:
            raise CmsError("a version cannot be approved by the person who wrote it")
        item.state = "approved"
        await self._audit(item, "approve", reviewer, version=str(item.current_version_id))
        return item

    async def request_changes(
        self, org_id: str, item_id: uuid.UUID, reviewer: Actor, note: str
    ) -> Item:
        item = await self.get(org_id, item_id)
        self._need(item, "in_review")
        item.state = "draft"
        await self._audit(item, "request_changes", reviewer, note=note)
        return item

    async def schedule(
        self,
        org_id: str,
        item_id: uuid.UUID,
        at: datetime,
        actor: Actor,
        *,
        kind: str | None = None,
        public_note: str | None = None,
        internal_note: str = "",
    ) -> Item:
        item = await self.get(org_id, item_id)
        self._need(item, "approved")
        if (
            item.published_version_id is not None
        ):  # a republish: the log entry is decided now, applied then
            self._check_correction(kind, public_note)
            item.attrs = {
                **item.attrs,
                "pending_correction": {
                    "kind": kind, "public_note": public_note, "internal_note": internal_note,
                },
            }  # fmt: skip
        if at.tzinfo is None:
            raise CmsError("schedule needs a timezone-aware time")
        item.state, item.publish_at = "scheduled", at
        await self._audit(item, "schedule", actor, at=at.isoformat())
        return item

    async def run_checks(self, item: Item, version: Version) -> list[tuple[bool, str, CheckResult]]:
        """Run every check for the item's type and store the results. Returns ``(blocking, name, result)``."""
        variants = {lang: v.state for lang, v in (await self.variants(item.id)).items()}
        ctx = CheckContext(self.s, variants)
        out: list[tuple[bool, str, CheckResult]] = []
        for check in self.checks.for_type(item.type):
            res = await check.run(ctx, item, version)
            self.s.add(
                CheckResultRow(
                    version_id=version.id, check=check.name, passed=res.passed,
                    blocking=check.blocking, details=res.details,
                )
            )  # fmt: skip
            out.append((check.blocking, check.name, res))
        await self.s.flush()
        return out

    @staticmethod
    def _check_correction(kind: str | None, public_note: str | None) -> None:
        if kind not in KINDS:
            raise CmsError(
                f"publishing a new version of a published item needs a kind: one of {', '.join(KINDS)}"
            )
        if not (public_note or "").strip():
            raise CmsError("publishing a new version of a published item needs a public note")

    async def publish(
        self,
        org_id: str,
        item_id: uuid.UUID,
        actor: Actor,
        *,
        kind: str | None = None,
        public_note: str | None = None,
        internal_note: str = "",
        complaint_id: uuid.UUID | None = None,
    ) -> Item:
        """Run the checks and make the approved version live. A failed blocking check raises
        :class:`PublishBlocked` and leaves the item as it was. Replacing a version that is already
        published needs a ``kind`` and a ``public_note``, which go into the item's public corrections
        log (a scheduled republish carries them from ``schedule``)."""
        item = await self.get(org_id, item_id)
        self._need(item, "approved", "scheduled")
        previous = item.published_version_id
        if previous is not None:
            pending = item.attrs.get("pending_correction") or {}
            kind = kind or pending.get("kind")
            public_note = public_note or pending.get("public_note")
            internal_note = internal_note or pending.get("internal_note", "")
            self._check_correction(kind, public_note)
        version = await self.s.get(Version, item.current_version_id)
        if version is None:
            raise CmsError("no version to publish")
        results = await self.run_checks(item, version)
        failed = [(n, r) for blocking, n, r in results if blocking and not r.passed]
        if failed:
            raise PublishBlocked(failed)
        for var in (await self.variants(item.id)).values():
            var.state = "published"
        item.state, item.publish_at = "published", None
        item.published_version_id, item.published_at = version.id, self.now()
        if previous is not None and previous != version.id:
            self.s.add(
                Correction(
                    org_id=item.org_id, item_id=item.id, version_from=previous, version_to=version.id,
                    kind=kind, public_note=(public_note or "").strip(), internal_note=internal_note,
                    complaint_id=complaint_id, created_by=actor.sub, created_at=self.now(),
                )
            )  # fmt: skip
            item.attrs = {k: v for k, v in item.attrs.items() if k != "pending_correction"}
        if item.lifecycle != "frozen":  # a correction to a frozen item does not restart reviews
            item.review_due_at = self.lifecycle.review_due(item.type, item.published_at)
        warnings = [n for blocking, n, r in results if not blocking and not r.passed]
        await self._audit(
            item,
            "publish",
            actor,
            version=str(version.id),
            number=version.number,
            warnings=warnings,
        )
        return item

    async def unpublish(self, org_id: str, item_id: uuid.UUID, reason: str, actor: Actor) -> Item:
        item = await self.get(org_id, item_id)
        if item.published_version_id is None:
            raise CmsError("item is not published")
        if not reason.strip():
            raise CmsError("unpublishing needs a reason")
        for var in (await self.variants(item.id)).values():
            if var.state == "published":
                var.state = "ready"
        item.state, item.published_version_id, item.publish_at = "unpublished", None, None
        item.review_due_at = None
        await self._audit(item, "unpublish", actor, reason=reason)
        return item

    async def publish_due(self, system: Actor = SYSTEM) -> list[uuid.UUID]:
        """Publish scheduled items whose time has come. An item that fails its checks goes back to
        ``approved`` (with an audit entry) so it is not retried every minute. Returns published ids."""
        due = (
            (
                await self.s.execute(
                    select(Item)
                    .where(Item.state == "scheduled", Item.publish_at <= self.now())
                    .order_by(Item.publish_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        done: list[uuid.UUID] = []
        for item in due:
            try:
                async with self.s.begin_nested():
                    await self.publish(item.org_id, item.id, system)
                done.append(item.id)
            except PublishBlocked as e:
                item.state, item.publish_at = "approved", None
                await self._audit(item, "publish_blocked", system, checks=[n for n, _ in e.failed])
        return done

    # -- comparing ---------------------------------------------------------------------------------

    async def diff(self, org_id: str, a_id: uuid.UUID, b_id: uuid.UUID) -> Diff:
        a, b = await self.s.get(Version, a_id), await self.s.get(Version, b_id)
        if a is None or b is None:
            raise LookupError("no such version")
        for v in (a, b):
            await self.get(org_id, v.item_id)  # both must belong to the organization
        ops: list[tuple[str, list[Any], list[Any]]] = [
            (tag, a.body[i1:i2], b.body[j1:j2])
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=[_canon(x) for x in a.body], b=[_canon(x) for x in b.body], autojunk=False
            ).get_opcodes()
            if tag != "equal"
        ]
        return Diff((a.title, b.title) if a.title != b.title else None, ops)

    # -- corrections log ---------------------------------------------------------------------------

    async def corrections(
        self, org_id: str, slug: str | None = None, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The public log, newest first: only entries of published items, never the internal note."""
        q = (
            select(Correction, Item.slug, Item.type, Version.title)
            .join(Item, Item.id == Correction.item_id)
            .join(Version, Version.id == Item.published_version_id)
            .where(Correction.org_id == org_id, Item.state == "published")
            .order_by(Correction.created_at.desc(), Correction.seq.desc())
            .limit(max(1, min(limit, 500)))
        )
        if slug is not None:
            q = q.where(Item.slug == slug)
        return [
            {
                "id": str(c.id),
                "slug": sl,
                "type": ty,
                "title": title,
                "kind": c.kind,
                "note": c.public_note,
                "at": c.created_at,
                "from_version": str(c.version_from) if c.version_from else None,
                "to_version": str(c.version_to) if c.version_to else None,
            }  # fmt: skip
            for c, sl, ty, title in (await self.s.execute(q)).all()
        ]

    async def add_note(
        self,
        org_id: str,
        item_id: uuid.UUID,
        kind: str,
        public_note: str,
        actor: Actor,
        *,
        internal_note: str = "",
        complaint_id: uuid.UUID | None = None,
    ) -> Correction:
        """A log entry without a new version (a clarification, or the outcome of a complaint)."""
        item = await self.get(org_id, item_id)
        if item.state != "published":
            raise CmsError("only published items have a public log")
        self._check_correction(kind, public_note)
        c = Correction(
            org_id=org_id, item_id=item.id, version_from=item.published_version_id,
            version_to=item.published_version_id, kind=kind, public_note=public_note.strip(),
            internal_note=internal_note, complaint_id=complaint_id, created_by=actor.sub, created_at=self.now(),
        )  # fmt: skip
        self.s.add(c)
        await self.s.flush()
        await self._audit(item, "note", actor, kind=kind)
        return c

    # -- review cadence ----------------------------------------------------------------------------

    def _note_lang(self, item: Item) -> str:
        return item.primary_lang

    async def _freeze(self, item: Item, reason: str, actor: Actor) -> None:
        at = self.now()
        item.lifecycle, item.frozen_at, item.frozen_reason, item.review_due_at = (
            "frozen",
            at,
            reason,
            None,
        )
        self.s.add(
            Correction(
                org_id=item.org_id, item_id=item.id, version_from=item.published_version_id,
                version_to=item.published_version_id, kind="update",
                public_note=self.lifecycle.freeze_note(self._note_lang(item), at),
                internal_note=f"frozen: {reason}", created_by=actor.sub, created_at=self.now(),
            )
        )  # fmt: skip
        await self._audit(item, "freeze", actor, reason=reason)

    async def _open_review(self, item_id: uuid.UUID) -> Review | None:
        return (
            await self.s.execute(
                select(Review).where(Review.item_id == item_id, Review.decided_at.is_(None))
            )
        ).scalar_one_or_none()

    async def run_lifecycle(self, system: Actor = SYSTEM) -> dict[str, list[uuid.UUID]]:
        """The daily job. Items whose review is due get an open review and their editors are told
        (once); an open review nobody decided within the grace period freezes its item. Safe to run
        twice in a day. Returns the ids notified and the ids frozen."""
        now = self.now()
        notified: list[uuid.UUID] = []
        frozen: list[uuid.UUID] = []
        due = (
            (
                await self.s.execute(
                    select(Item)
                    .where(
                        Item.state == "published",
                        Item.lifecycle != "frozen",
                        Item.review_due_at <= now,
                    )
                    .order_by(Item.review_due_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for item in due:
            review = await self._open_review(item.id)
            if review is None:
                review = Review(item_id=item.id, due_at=item.review_due_at)
                self.s.add(review)
            if review.notified_at is None:
                review.notified_at = now
                if self.notify_review_due:
                    await self.notify_review_due(
                        self.s, item, self.lifecycle.freeze_deadline(item.type, review.due_at)
                    )
                await self._audit(item, "review_due", system, due_at=review.due_at.isoformat())
                notified.append(item.id)
            if now >= self.lifecycle.freeze_deadline(item.type, review.due_at):
                review.decided_at, review.decision, review.decided_by = now, "frozen", system.sub
                await self._freeze(item, "auto", system)
                frozen.append(item.id)
        await self.s.flush()
        return {"notified": notified, "frozen": frozen}

    async def decide_review(
        self, org_id: str, item_id: uuid.UUID, decision: str, actor: Actor, reason: str = ""
    ) -> Item:
        """An editor's call on a published item: ``living`` (reviewed, starts a new cycle) or ``frozen``
        (no longer updated; a public note says so)."""
        if decision not in ("living", "frozen"):
            raise CmsError("decision must be living or frozen")
        item = await self.get(org_id, item_id)
        if item.state != "published":
            raise CmsError("only published items are reviewed")
        if item.lifecycle == "frozen":
            raise CmsError("the item is already frozen; unfreeze it first")
        now = self.now()
        review = await self._open_review(item.id)
        if review is None:
            review = Review(item_id=item.id, due_at=item.review_due_at or now)
            self.s.add(review)
        review.decided_at, review.decision, review.decided_by = now, decision, actor.sub
        if decision == "living":
            item.lifecycle = "living"
            item.review_due_at = self.lifecycle.review_due(item.type, now)
            await self._audit(item, "review_living", actor, reason=reason)
        else:
            await self._freeze(item, reason or "editor decision", actor)
        await self.s.flush()
        return item

    async def unfreeze(self, org_id: str, item_id: uuid.UUID, actor: Actor, reason: str) -> Item:
        item = await self.get(org_id, item_id)
        if item.lifecycle != "frozen":
            raise CmsError("the item is not frozen")
        if not reason.strip():
            raise CmsError("unfreezing needs a reason")
        item.lifecycle, item.frozen_at, item.frozen_reason = "living", None, None
        item.review_due_at = self.lifecycle.review_due(item.type, self.now())
        await self._audit(item, "unfreeze", actor, reason=reason)
        return item
