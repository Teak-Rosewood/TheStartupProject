from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor

from .models import Item
from .service import CmsError, CmsService, PublishBlocked


@dataclass(frozen=True)
class Principal:
    """The signed-in editor: their organization, user id and CMS roles (``editor``, ``reviewer``)."""

    org_id: str
    sub: str
    roles: frozenset[str]


class ItemBody(BaseModel):
    type: str
    slug: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9-]*$")
    primary_lang: str = "en"


class VersionBody(BaseModel):
    lang: str
    title: str
    body: list[dict[str, Any]]
    summary: str = ""
    author_kind: str = "human"
    disclosures: list[dict[str, Any]] = []
    change_note: str = ""


class CorrectionBody(BaseModel):
    """Required when the item is already published: what changed, for readers."""

    kind: str | None = None
    public_note: str | None = None
    internal_note: str = ""


class ScheduleBody(CorrectionBody):
    at: datetime


class ReviewBody(BaseModel):
    decision: str
    reason: str = ""


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1)


def _item(i: Item) -> dict[str, Any]:
    return {
        "id": str(i.id), "type": i.type, "slug": i.slug, "state": i.state, "lang": i.primary_lang,
        "current_version_id": str(i.current_version_id) if i.current_version_id else None,
        "published_version_id": str(i.published_version_id) if i.published_version_id else None,
        "publish_at": i.publish_at, "lifecycle": i.lifecycle, "review_due_at": i.review_due_at,
        "frozen_at": i.frozen_at, "frozen_reason": i.frozen_reason,
    }  # fmt: skip


def build_router(
    principal: Callable[..., Any],
    session: Callable[..., Any],
    service_factory: Callable[[AsyncSession], CmsService] = CmsService,
) -> APIRouter:
    """Editor API under ``/cms``. ``editor`` may create, edit and submit; ``reviewer`` may approve,
    request changes, schedule, publish and unpublish. Someone can hold both roles; the service still
    refuses to let a version be approved by its own author."""
    r = APIRouter(prefix="/cms")

    def need(p: Principal, role: str) -> Actor:
        if role not in p.roles:
            raise HTTPException(403, f"needs the {role} role")
        return Actor(p.sub, role)

    async def call(coro: Any) -> Any:
        try:
            return await coro
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except PublishBlocked as e:
            raise HTTPException(
                409,
                {
                    "message": "publishing is blocked",
                    "failed": [{"check": n, "details": res.details} for n, res in e.failed],
                },
            ) from e
        except CmsError as e:
            raise HTTPException(409, str(e)) from e

    @r.post("/items", status_code=201)
    async def create(
        body: ItemBody, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        need(p, "editor")
        return _item(
            await call(
                service_factory(s).create(
                    p.org_id, body.type, body.slug, p.sub, primary_lang=body.primary_lang
                )
            )
        )

    @r.get("/items/{item_id}")
    async def read(
        item_id: uuid.UUID, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        if not p.roles & {"editor", "reviewer"}:
            raise HTTPException(403, "needs the editor or reviewer role")
        svc = service_factory(s)
        item = await call(svc.get(p.org_id, item_id))
        vs = await svc.versions(p.org_id, item_id)
        return {
            **_item(item),
            "versions": [
                {
                    "id": str(v.id),
                    "number": v.number,
                    "lang": v.lang,
                    "title": v.title,
                    "author_kind": v.author_kind,
                }
                for v in vs
            ],
        }

    @r.post("/items/{item_id}/versions", status_code=201)
    async def save(
        item_id: uuid.UUID,
        body: VersionBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        need(p, "editor")
        v = await call(
            service_factory(s).save_version(
                p.org_id, item_id, body.lang, created_by=p.sub, **body.model_dump(exclude={"lang"})
            )
        )
        return {"id": str(v.id), "number": v.number}

    @r.post("/items/{item_id}/submit")
    async def submit(
        item_id: uuid.UUID, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return _item(await call(service_factory(s).submit(p.org_id, item_id, need(p, "editor"))))

    @r.post("/items/{item_id}/approve")
    async def approve(
        item_id: uuid.UUID, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return _item(await call(service_factory(s).approve(p.org_id, item_id, need(p, "reviewer"))))

    @r.post("/items/{item_id}/request-changes")
    async def changes(
        item_id: uuid.UUID,
        body: ReasonBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _item(
            await call(
                service_factory(s).request_changes(
                    p.org_id, item_id, need(p, "reviewer"), body.reason
                )
            )
        )

    @r.post("/items/{item_id}/schedule")
    async def schedule(
        item_id: uuid.UUID,
        body: ScheduleBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _item(
            await call(
                service_factory(s).schedule(
                    p.org_id,
                    item_id,
                    body.at,
                    need(p, "reviewer"),
                    kind=body.kind,
                    public_note=body.public_note,
                    internal_note=body.internal_note,
                )
            )
        )

    @r.post("/items/{item_id}/publish")
    async def publish(
        item_id: uuid.UUID,
        body: CorrectionBody | None = None,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        b = body or CorrectionBody()
        return _item(
            await call(
                service_factory(s).publish(
                    p.org_id,
                    item_id,
                    need(p, "reviewer"),
                    kind=b.kind,
                    public_note=b.public_note,
                    internal_note=b.internal_note,
                )
            )
        )

    @r.post("/items/{item_id}/review")
    async def review(
        item_id: uuid.UUID,
        body: ReviewBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        """Decide a due review: ``living`` (a new cycle starts) or ``frozen`` (a public note says so)."""
        return _item(
            await call(
                service_factory(s).decide_review(
                    p.org_id, item_id, body.decision, need(p, "reviewer"), body.reason
                )
            )
        )

    @r.post("/items/{item_id}/unfreeze")
    async def unfreeze(
        item_id: uuid.UUID,
        body: ReasonBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _item(
            await call(
                service_factory(s).unfreeze(p.org_id, item_id, need(p, "reviewer"), body.reason)
            )
        )

    @r.post("/items/{item_id}/notes", status_code=201)
    async def note(
        item_id: uuid.UUID,
        body: CorrectionBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        """A clarification or correction note without a new version."""
        c = await call(
            service_factory(s).add_note(
                p.org_id,
                item_id,
                body.kind or "",
                body.public_note or "",
                need(p, "reviewer"),
                internal_note=body.internal_note,
            )
        )
        return {"id": str(c.id), "kind": c.kind}

    @r.post("/items/{item_id}/unpublish")
    async def unpublish(
        item_id: uuid.UUID,
        body: ReasonBody,
        p: Principal = Depends(principal),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return _item(
            await call(
                service_factory(s).unpublish(p.org_id, item_id, body.reason, need(p, "reviewer"))
            )
        )

    return r


def _rss(title: str, link: str, entries: list[dict[str, Any]], base_url: str) -> str:
    from email.utils import format_datetime
    from xml.sax.saxutils import escape

    items = "".join(
        f"<item><title>{escape(e['kind'].capitalize() + ': ' + e['title'])}</title>"
        f"<link>{escape(base_url.rstrip('/') + '/' + e['slug'])}</link>"
        f'<guid isPermaLink="false">{e["id"]}</guid>'
        f"<pubDate>{format_datetime(e['at'])}</pubDate>"
        f"<description>{escape(e['note'])}</description></item>"
        for e in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>{escape(title)}</title><link>{escape(link)}</link>'
        f"<description>{escape(title)}</description>{items}</channel></rss>"
    )


def build_public_router(
    session: Callable[..., Any],
    org: str | Callable[[Request], str],
    *,
    base_url: str,
    title: str = "Corrections and updates",
    service_factory: Callable[[AsyncSession], CmsService] = CmsService,
) -> APIRouter:
    """Unauthenticated, cacheable corrections log for readers and feed readers. ``org`` is the
    publishing organization (a fixed id, or a function of the request such as the Host header).
    Only entries of published items appear, and never the internal note."""
    r = APIRouter(prefix="/public/cms")

    def org_of(request: Request) -> str:
        return org(request) if callable(org) else org

    async def fetch(request: Request, s: AsyncSession, slug: str | None) -> list[dict[str, Any]]:
        entries = await service_factory(s).corrections(org_of(request), slug)
        if slug is not None and not entries:
            try:
                await service_factory(s).by_slug(org_of(request), slug)
            except LookupError as e:
                raise HTTPException(404, "not found") from e
        return entries

    def cache(resp: Response) -> None:
        resp.headers["Cache-Control"] = "public, max-age=300"

    @r.get("/corrections")
    async def feed(
        request: Request, response: Response, s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        cache(response)
        return {"corrections": await fetch(request, s, None)}

    @r.get("/corrections.rss")
    async def feed_rss(request: Request, s: AsyncSession = Depends(session)) -> Response:
        xml = _rss(title, base_url, await fetch(request, s, None), base_url)
        return Response(
            xml, media_type="application/rss+xml", headers={"Cache-Control": "public, max-age=300"}
        )

    @r.get("/items/{slug}/corrections")
    async def item_log(
        slug: str, request: Request, response: Response, s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        cache(response)
        return {"corrections": await fetch(request, s, slug)}

    @r.get("/items/{slug}/corrections.rss")
    async def item_rss(slug: str, request: Request, s: AsyncSession = Depends(session)) -> Response:
        xml = _rss(
            f"{title}: {slug}",
            f"{base_url.rstrip('/')}/{slug}",
            await fetch(request, s, slug),
            base_url,
        )
        return Response(
            xml, media_type="application/rss+xml", headers={"Cache-Control": "public, max-age=300"}
        )

    return r
