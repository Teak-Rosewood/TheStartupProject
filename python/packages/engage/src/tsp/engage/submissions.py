from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record
from tsp.cms import CmsError, ComplaintsDesk
from tsp.pipeline import Runner, Workflow

from .config import EngageConfig
from .models import Submission
from .moderation import Draft, award_for_acceptance, body_hash
from .onboarding import Onboarding
from .receipts import Receipts

APPEAL_CATEGORY = "moderation_appeal"
Entitled = Callable[
    [AsyncSession, str, str], Awaitable[bool]
]  # (session, user_id, org_id) -> subscriber?


class SubmissionError(Exception):
    """A request the submission's state or the rules do not allow."""


@dataclass
class Eligibility:
    """Why a reader can or cannot submit. ``reasons`` are machine-readable: ``not_signed_in``,
    ``not_subscribed``, ``onboarding_incomplete``, ``not_read``."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    onboarding_remaining: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Submissions:
    """A reader's take on an item: pick a side, write a few sentences. No replies, no threads, no
    mentions. Moderation runs as a pipeline workflow; rejected takes can be appealed to the
    complaints desk (category ``moderation_appeal``, which the desk's categories must include)."""

    def __init__(
        self,
        session: AsyncSession,
        cfg: EngageConfig,
        runner: Runner,
        workflow: Workflow,
        *,
        entitled: Entitled | None = None,
        desk: ComplaintsDesk | None = None,
        item_title: Callable[[str, str], Awaitable[str]] | None = None,
        clock: Callable[[], datetime] = _now,
        budget_usd: float | None = 0.05,
    ):
        self.s, self.cfg, self.runner, self.wf = session, cfg, runner, workflow
        self.entitled, self.desk, self._title, self.now, self.budget = (
            entitled,
            desk,
            item_title,
            clock,
            budget_usd,
        )
        self.onboarding = Onboarding(session, cfg)
        self.receipts = Receipts(session, clock=clock)

    # -- preconditions -----------------------------------------------------------------------------

    async def can_submit(self, user_id: str | None, org_id: str, item_id: str) -> Eligibility:
        if not user_id:
            return Eligibility(False, ["not_signed_in"])
        reasons: list[str] = []
        if self.cfg.require_subscription and not (
            self.entitled and await self.entitled(self.s, user_id, org_id)
        ):
            reasons.append("not_subscribed")
        remaining = await self.onboarding.remaining(user_id, org_id)
        if remaining:
            reasons.append("onboarding_incomplete")
        if not await self.receipts.is_read(user_id, item_id, self.cfg.read_policy):
            reasons.append("not_read")
        return Eligibility(not reasons, reasons, remaining)

    # -- writing -----------------------------------------------------------------------------------

    async def submit(
        self,
        user_id: str,
        org_id: str,
        item_id: str,
        side: str,
        body: str,
        *,
        author_name: str = "",
    ) -> Submission:
        """Create the take, or edit it while it is not accepted yet; either way it is moderated again."""
        side, body = side.strip(), body.strip()
        if not side or len(side) > 40:
            raise SubmissionError("pick a side (up to 40 characters)")
        if not body:
            raise SubmissionError("write something")
        elig = await self.can_submit(user_id, org_id, item_id)
        if not elig.ok:
            raise SubmissionError("not allowed yet: " + ", ".join(elig.reasons))
        sub = (
            await self.s.execute(
                select(Submission)
                .where(
                    Submission.item_id == item_id,
                    Submission.user_id == user_id,
                    Submission.side == side,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if sub is None:
            sub = Submission(
                org_id=org_id,
                item_id=item_id,
                user_id=user_id,
                side=side,
                body=body,
                author_name=author_name[:80],
            )
            self.s.add(sub)
        elif sub.status in ("accepted", "appealed"):
            raise SubmissionError(f"this take is {sub.status} and can no longer be edited")
        else:
            sub.body, sub.status, sub.reasons, sub.scores, sub.needs_review = (
                body,
                "pending",
                [],
                {},
                False,
            )
            sub.author_name = author_name[:80] or sub.author_name
        await self.s.flush()
        title = await self._title(org_id, item_id) if self._title else ""
        draft = Draft(str(sub.id), org_id, item_id, user_id, side, body, title)
        sub.run_id = await self.runner.start(
            org_id,
            self.wf,
            {"draft": draft.__dict__, "body_hash": body_hash(body)},
            self.budget,
            session=self.s,
        )
        return sub

    async def get(self, submission_id: uuid.UUID) -> Submission:
        sub = await self.s.get(Submission, submission_id)
        if sub is None:
            raise LookupError("no such submission")
        return sub

    # -- human review of flagged takes -----------------------------------------------------------------

    async def review_queue(self, org_id: str, limit: int = 100) -> list[Submission]:
        rows = await self.s.execute(
            select(Submission)
            .where(
                Submission.org_id == org_id,
                Submission.status == "pending",
                Submission.needs_review.is_(True),
            )
            .order_by(Submission.created_at)
            .limit(limit)
        )
        return list(rows.scalars())

    async def review(
        self, org_id: str, submission_id: uuid.UUID, actor: Actor, accept: bool, note: str = ""
    ) -> Submission:
        sub = await self.get(submission_id)
        if sub.org_id != org_id or sub.status != "pending" or not sub.needs_review:
            raise SubmissionError("this take is not waiting for review")
        if not accept and not note.strip():
            raise SubmissionError("say why it is rejected: the author will read it")
        sub.needs_review, sub.decided_at = False, self.now()
        if accept:
            sub.status, sub.reasons = "accepted", []
            sub.points_awarded = await award_for_acceptance(self.s, sub, self.cfg)
        else:
            sub.status, sub.reasons = "rejected", [note.strip()]
        await record(
            self.s,
            "engage.submission.review",
            actor=actor,
            target=("en_submission", str(sub.id)),
            accepted=accept,
        )
        return sub

    # -- appeals ---------------------------------------------------------------------------------------

    async def appeal(
        self, user_id: str, submission_id: uuid.UUID, *, name: str, email: str, message: str
    ) -> Any:
        """Open a complaint about a rejection. One appeal per take."""
        if self.desk is None:
            raise SubmissionError("appeals are not configured")
        sub = await self.get(submission_id)
        if sub.user_id != user_id:
            raise LookupError("no such submission")
        if sub.status != "rejected":
            raise SubmissionError("only a rejected take can be appealed")
        if sub.appeal_complaint_id is not None:
            raise SubmissionError("this take was already appealed")
        reasons = "; ".join(map(str, sub.reasons)) or "no reason recorded"
        text = f"Appeal of submission {sub.id} on item {sub.item_id}.\nModeration said: {reasons}\nReader says: {message.strip()}"
        try:
            c = await self.desk.intake(sub.org_id, name, email, APPEAL_CATEGORY, text)
        except CmsError as e:
            raise SubmissionError(str(e)) from e
        sub.status, sub.appeal_complaint_id = "appealed", c.id
        return c

    async def resolve_appeal(
        self, org_id: str, submission_id: uuid.UUID, actor: Actor, *, upheld: bool, resolution: str
    ) -> Submission:
        """Decide an appeal: ``upheld`` accepts the take (and pays its points), otherwise it stays rejected.
        The linked complaint is closed with the same resolution text."""
        sub = await self.get(submission_id)
        if (
            sub.org_id != org_id
            or sub.status != "appealed"
            or sub.appeal_complaint_id is None
            or self.desk is None
        ):
            raise SubmissionError("this take has no open appeal")
        try:
            await self.desk.resolve(org_id, sub.appeal_complaint_id, actor, resolution)
        except (CmsError, LookupError) as e:
            raise SubmissionError(str(e)) from e
        sub.decided_at = self.now()
        if upheld:
            sub.status, sub.reasons = "accepted", []
            sub.points_awarded = await award_for_acceptance(self.s, sub, self.cfg)
        else:
            sub.status = "rejected"
        await record(
            self.s,
            "engage.submission.appeal",
            actor=actor,
            target=("en_submission", str(sub.id)),
            upheld=upheld,
        )
        return sub

    # -- reading ----------------------------------------------------------------------------------------

    async def public(
        self,
        org_id: str,
        item_id: str,
        side: str | None = None,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Accepted takes, newest first, with the author's display name. Nothing else about the author."""
        q = select(Submission).where(
            Submission.org_id == org_id,
            Submission.item_id == item_id,
            Submission.status == "accepted",
        )
        if side:
            q = q.where(Submission.side == side)
        total = (await self.s.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        rows = (
            await self.s.execute(
                q.order_by(Submission.decided_at.desc(), Submission.id)
                .limit(max(1, min(limit, 100)))
                .offset(max(0, offset))
            )
        ).scalars()
        return {
            "total": total,
            "items": [
                {
                    "id": str(r.id),
                    "side": r.side,
                    "body": r.body,
                    "author": r.author_name or "Reader",
                    "points": r.points_awarded,
                    "at": r.decided_at,
                }
                for r in rows
            ],
        }

    async def mine(self, user_id: str) -> list[dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(Submission)
                .where(Submission.user_id == user_id)
                .order_by(Submission.created_at.desc())
            )
        ).scalars()
        return [
            {
                "id": str(r.id),
                "item_id": r.item_id,
                "side": r.side,
                "body": r.body,
                "status": r.status,
                "reasons": r.reasons,
                "needs_review": r.needs_review,
                "points": r.points_awarded,
                "appeal": str(r.appeal_complaint_id) if r.appeal_complaint_id else None,
            }
            for r in rows
        ]
