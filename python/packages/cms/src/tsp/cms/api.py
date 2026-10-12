from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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


class ScheduleBody(BaseModel):
    at: datetime


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1)


def _item(i: Item) -> dict[str, Any]:
    return {
        "id": str(i.id), "type": i.type, "slug": i.slug, "state": i.state, "lang": i.primary_lang,
        "current_version_id": str(i.current_version_id) if i.current_version_id else None,
        "published_version_id": str(i.published_version_id) if i.published_version_id else None,
        "publish_at": i.publish_at, "lifecycle": i.lifecycle,
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
            await call(service_factory(s).schedule(p.org_id, item_id, body.at, need(p, "reviewer")))
        )

    @r.post("/items/{item_id}/publish")
    async def publish(
        item_id: uuid.UUID, p: Principal = Depends(principal), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return _item(await call(service_factory(s).publish(p.org_id, item_id, need(p, "reviewer"))))

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
