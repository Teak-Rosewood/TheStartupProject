from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngageConfig
from .polls import PollError
from .receipts import TooManyPings
from .service import Engage


class PingBody(BaseModel):
    org_id: str
    dwell: float = Field(ge=0, le=86400)
    scroll: float = Field(ge=0, le=1)
    reached_end: bool = False


class AnswerBody(BaseModel):
    phase: str
    x: float
    y: float | None = None


def build_router(
    account: Callable[..., Any],
    session: Callable[..., Any],
    cfg: EngageConfig | None = None,
    *,
    allow_ping: Callable[[str], Any] | None = None,
) -> APIRouter:
    """Signed-in readers only. ``org_id`` is the publishing organization (a namespace, not a
    permission). Serving the full item calls ``Receipts.start`` on the server; the page sends pings."""
    cfg = cfg or EngageConfig()
    r = APIRouter(prefix="/engage")

    def engage(s: AsyncSession) -> Engage:
        return Engage(s, cfg, allow_ping=allow_ping)

    @r.get("/me")
    async def me(
        org_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return await engage(s).me(u.sub, org_id)

    @r.post("/receipts/{item_id}/ping")
    async def ping(
        item_id: str, body: PingBody, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            await e.receipts.ping(u.sub, item_id, body.dwell, body.scroll, body.reached_end)
        except TooManyPings as ex:
            raise HTTPException(429, "too many pings") from ex
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        out = await e.record_read(u.sub, body.org_id, item_id)
        return {
            "read": out.newly_read,
            "points": out.points,
            "badges": out.badges,
            "streak_week_met": out.streak_week_met,
        }

    @r.get("/polls")
    async def polls_for_item(
        org_id: str, item_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        e = engage(s)
        out = []
        for p in await e.polls.for_item(org_id, item_id):
            out.append(
                {
                    "id": str(p.id),
                    "kind": p.kind,
                    "axes": p.axes,
                    "opens_at": p.opens_at,
                    "closes_at": p.closes_at,
                    "shift": await e.polls.shift(u.sub, p.id),
                }
            )
        return out

    @r.post("/polls/{poll_id}/answers", status_code=201)
    async def answer(
        poll_id: uuid.UUID,
        body: AnswerBody,
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            paid = await e.respond_to_poll(u.sub, poll_id, body.phase, body.x, body.y)
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        except PollError as ex:
            raise HTTPException(422, detail={"type": "poll_rejected", "message": str(ex)}) from ex
        return {"points": paid, "shift": await e.polls.shift(u.sub, poll_id)}

    @r.get("/polls/{poll_id}/aggregate")
    async def aggregate(
        poll_id: uuid.UUID, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            return await e.polls.aggregate(poll_id)
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex

    @r.post("/onboarding/{step}")
    async def onboarding(
        step: str, org_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            paid = await e.complete_onboarding(u.sub, org_id, step)
        except ValueError as ex:
            raise HTTPException(404, str(ex)) from ex
        return {"points": paid, "remaining": await e.onboarding.remaining(u.sub, org_id)}

    return r


class SubmitBody(BaseModel):
    org_id: str
    side: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=5000)


class AppealBody(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)


class ReviewBody(BaseModel):
    accept: bool
    note: str = Field(default="", max_length=1000)


class AppealDecisionBody(BaseModel):
    upheld: bool
    resolution: str = Field(min_length=1, max_length=3000)


def build_submissions_router(
    account: Callable[..., Any],
    session: Callable[..., Any],
    submissions_factory: Callable[[AsyncSession], Any],
    principal: Callable[..., Any] | None = None,
) -> APIRouter:
    """Readers' takes. ``account`` is the signed-in user; ``principal`` (the CMS staff dependency)
    enables the review queue and appeal decisions. Public reading needs no sign-in."""
    from tsp.audit import Actor
    from tsp.cms import Principal

    from .submissions import SubmissionError

    r = APIRouter(prefix="/engage")

    def staff(p: Principal) -> Actor:
        if "reviewer" not in p.roles:
            raise HTTPException(403, "needs the reviewer role")
        return Actor(p.sub, "reviewer")

    async def guard(coro: Any) -> Any:
        try:
            return await coro
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        except SubmissionError as ex:
            raise HTTPException(
                422, detail={"type": "submission_rejected", "message": str(ex)}
            ) from ex

    @r.get("/items/{item_id}/eligibility")
    async def eligibility(
        item_id: str, org_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = await submissions_factory(s).can_submit(u.sub, org_id, item_id)
        return {"ok": e.ok, "reasons": e.reasons, "onboarding_remaining": e.onboarding_remaining}

    @r.put("/items/{item_id}/submission")
    async def submit(
        item_id: str,
        body: SubmitBody,
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        sub = await guard(
            submissions_factory(s).submit(
                u.sub,
                body.org_id,
                item_id,
                body.side,
                body.body,
                author_name=getattr(u, "name", "") or "",
            )
        )
        return {"id": str(sub.id), "status": sub.status}

    @r.get("/me/submissions")
    async def mine(
        u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        return await submissions_factory(s).mine(u.sub)  # type: ignore[no-any-return]

    @r.post("/submissions/{submission_id}/appeal", status_code=201)
    async def appeal(
        submission_id: uuid.UUID,
        body: AppealBody,
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, str]:
        c = await guard(
            submissions_factory(s).appeal(
                u.sub, submission_id, name=body.name, email=body.email, message=body.message
            )
        )
        return {"reference": c.reference}

    @r.get("/public/items/{item_id}/submissions")
    async def public(
        item_id: str,
        org_id: str,
        side: str | None = None,
        limit: int = 20,
        offset: int = 0,
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return await submissions_factory(s).public(
            org_id, item_id, side, limit=limit, offset=offset
        )  # type: ignore[no-any-return]

    if principal is not None:

        @r.get("/staff/review-queue")
        async def queue(
            p: Principal = Depends(principal), s: AsyncSession = Depends(session)
        ) -> list[dict[str, Any]]:
            staff(p)
            return [
                {
                    "id": str(x.id),
                    "item_id": x.item_id,
                    "side": x.side,
                    "body": x.body,
                    "reasons": x.reasons,
                    "scores": x.scores,
                }
                for x in await submissions_factory(s).review_queue(p.org_id)
            ]

        @r.post("/staff/submissions/{submission_id}/review")
        async def review(
            submission_id: uuid.UUID,
            body: ReviewBody,
            p: Principal = Depends(principal),
            s: AsyncSession = Depends(session),
        ) -> dict[str, Any]:
            sub = await guard(
                submissions_factory(s).review(
                    p.org_id, submission_id, staff(p), body.accept, body.note
                )
            )
            return {"id": str(sub.id), "status": sub.status}

        @r.post("/staff/submissions/{submission_id}/appeal")
        async def decide_appeal(
            submission_id: uuid.UUID,
            body: AppealDecisionBody,
            p: Principal = Depends(principal),
            s: AsyncSession = Depends(session),
        ) -> dict[str, Any]:
            sub = await guard(
                submissions_factory(s).resolve_appeal(
                    p.org_id,
                    submission_id,
                    staff(p),
                    upheld=body.upheld,
                    resolution=body.resolution,
                )
            )
            return {"id": str(sub.id), "status": sub.status}

    return r
