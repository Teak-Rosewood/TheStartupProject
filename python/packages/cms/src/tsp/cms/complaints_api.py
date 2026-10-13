from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, client_ip

from .api import Principal
from .complaints import ComplaintsDesk
from .models import Complaint
from .service import CmsError


class IntakeBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    category: str
    body: str = Field(min_length=10, max_length=5000)
    item_slug: str | None = Field(default=None, max_length=200)
    captcha_token: str | None = Field(default=None, max_length=4000)
    website: str = ""  # honeypot: people leave it empty, form-filling bots do not


class TextBody(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class AssignBody(BaseModel):
    assignee: str = Field(min_length=1, max_length=200)


class ResolveBody(BaseModel):
    resolution: str = Field(min_length=1, max_length=5000)
    correction_kind: str | None = None
    correction_note: str | None = None
    correction_id: uuid.UUID | None = None


def _out(c: Complaint) -> dict[str, Any]:
    return {
        "id": str(c.id), "reference": c.reference, "category": c.category, "status": c.status,
        "level": c.level, "assigned_to": c.assigned_to, "item_id": str(c.item_id) if c.item_id else None,
        "received_at": c.received_at, "acknowledged_at": c.acknowledged_at, "ack_due_at": c.ack_due_at,
        "resolve_due_at": c.resolve_due_at, "closed_at": c.closed_at,
        "correction_id": str(c.correction_id) if c.correction_id else None,
    }  # fmt: skip


def build_intake_router(
    session: Callable[..., Any],
    org: str | Callable[[Request], str],
    desk_factory: Callable[[AsyncSession], ComplaintsDesk],
    get_cache: Callable[..., Any],
    *,
    captcha: Callable[[str | None, str | None], Awaitable[bool]] | None = None,
    per_hour: int = 5,
) -> APIRouter:
    """``POST /public/cms/complaints``. Rate limited per address; ``captcha(token, ip)`` is your
    hook (``tsp.growth`` has verifiers). The reply carries only the reference number."""
    r = APIRouter(prefix="/public/cms")

    @r.post("/complaints", status_code=201)
    async def intake(
        body: IntakeBody,
        request: Request,
        s: AsyncSession = Depends(session),
        cache: Any = Depends(get_cache),
    ) -> dict[str, str]:
        ip = client_ip(request) or "unknown"
        if body.website:  # a bot filled the honeypot: pretend it worked
            return {"reference": "C-0000-000000"}
        if not await cache.allow(f"cms-complaint:{ip}", per_hour, 3600):
            raise HTTPException(429, "too many complaints from this address; try again later")
        if captcha is not None and not await captcha(body.captcha_token, ip):
            raise HTTPException(400, "could not verify that you are human")
        org_id = org(request) if callable(org) else org
        try:
            c = await desk_factory(s).intake(
                org_id, body.name, body.email, body.category, body.body, item_slug=body.item_slug
            )
        except CmsError as e:
            raise HTTPException(422, str(e)) from e
        return {"reference": c.reference}

    return r


def build_complaints_router(
    principal: Callable[..., Any],
    session: Callable[..., Any],
    desk_factory: Callable[[AsyncSession], ComplaintsDesk],
) -> APIRouter:
    """Staff routes under ``/cms/complaints``. Editors and reviewers read, acknowledge, assign and
    note; only reviewers resolve, reject and escalate."""
    r = APIRouter(prefix="/cms/complaints")

    def need(p: Principal, *roles: str) -> Actor:
        if not p.roles & set(roles):
            raise HTTPException(403, f"needs the {' or '.join(roles)} role")
        return Actor(p.sub, sorted(p.roles & set(roles))[0])

    async def call(coro: Any) -> Any:
        try:
            return await coro
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except CmsError as e:
            raise HTTPException(409, str(e)) from e

    staff = ("editor", "reviewer")

    @r.get("")
    async def listing(
        status: str | None = None, level: int | None = None,
        p: Principal = Depends(principal), s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:  # fmt: skip
        need(p, *staff)
        return [
            _out(c) for c in await desk_factory(s).listing(p.org_id, status=status, level=level)
        ]

    @r.get("/report")
    async def report(
        month: str, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        need(p, *staff)
        return await call(desk_factory(s).report(p.org_id, month))  # type: ignore[no-any-return]

    @r.get("/report.csv")
    async def report_csv(
        month: str, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> Response:
        need(p, *staff)
        text = await call(desk_factory(s).report_csv(p.org_id, month))
        return Response(
            text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="complaints-{month}.csv"'},
        )

    @r.get("/{complaint_id}")
    async def read(
        complaint_id: uuid.UUID,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        need(p, *staff)
        desk = desk_factory(s)
        c = await call(desk.get(p.org_id, complaint_id))
        return {
            **_out(c), "complainant_name": c.complainant_name, "email": c.email, "message": c.body,
            "resolution": c.resolution,
            "events": [{"at": e.at, "actor": e.actor, "kind": e.kind, "note": e.note} for e in await desk.events(p.org_id, complaint_id)],
        }  # fmt: skip

    @r.post("/{complaint_id}/acknowledge")
    async def acknowledge(
        complaint_id: uuid.UUID,
        body: TextBody | None = None,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        c = await call(
            desk_factory(s).acknowledge(
                p.org_id, complaint_id, need(p, *staff), body.text if body else ""
            )
        )
        return _out(c)

    @r.post("/{complaint_id}/assign")
    async def assign(
        complaint_id: uuid.UUID,
        body: AssignBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _out(
            await call(
                desk_factory(s).assign(p.org_id, complaint_id, body.assignee, need(p, *staff))
            )
        )

    @r.post("/{complaint_id}/start")
    async def start(
        complaint_id: uuid.UUID,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _out(await call(desk_factory(s).start(p.org_id, complaint_id, need(p, *staff))))

    @r.post("/{complaint_id}/notes", status_code=201)
    async def note(
        complaint_id: uuid.UUID,
        body: TextBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _out(
            await call(desk_factory(s).note(p.org_id, complaint_id, need(p, *staff), body.text))
        )

    @r.post("/{complaint_id}/resolve")
    async def resolve(
        complaint_id: uuid.UUID,
        body: ResolveBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        correction = (
            (body.correction_kind or "", body.correction_note or "")
            if body.correction_kind or body.correction_note
            else None
        )
        return _out(
            await call(
                desk_factory(s).resolve(
                    p.org_id,
                    complaint_id,
                    need(p, "reviewer"),
                    body.resolution,
                    correction=correction,
                    correction_id=body.correction_id,
                )
            )
        )

    @r.post("/{complaint_id}/reject")
    async def reject(
        complaint_id: uuid.UUID,
        body: TextBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _out(
            await call(
                desk_factory(s).reject(p.org_id, complaint_id, need(p, "reviewer"), body.text)
            )
        )

    @r.post("/{complaint_id}/escalate")
    async def escalate(
        complaint_id: uuid.UUID,
        body: TextBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _out(
            await call(
                desk_factory(s).escalate(p.org_id, complaint_id, need(p, "reviewer"), body.text)
            )
        )

    return r
