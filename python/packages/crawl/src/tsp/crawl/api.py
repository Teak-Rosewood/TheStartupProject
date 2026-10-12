from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor
from tsp.vector import VectorError

from .models import CrawlPage, CrawlSource
from .service import CrawlService


class SourceIn(BaseModel):
    collection: str
    seed_url: str = Field(max_length=2000)
    sitemap_url: str | None = Field(default=None, max_length=2000)
    include: list[str] = []
    exclude: list[str] = []
    max_depth: int = Field(default=2, ge=0, le=10)
    max_pages: int = Field(default=500, ge=1, le=10_000)
    render: str = "never"
    refresh_hours: int = Field(default=168, ge=1)


class SourcePatch(BaseModel):
    status: str


def _http(e: VectorError) -> HTTPException:
    return HTTPException(e.status, detail={"type": e.type, "message": str(e)})


def _source(s: CrawlSource) -> dict[str, Any]:
    return {
        "id": s.id, "collection_id": s.collection_id, "seed_url": s.seed_url,
        "sitemap_url": s.sitemap_url, "include": s.include, "exclude": s.exclude,
        "max_depth": s.max_depth, "max_pages": s.max_pages, "render": s.render,
        "refresh_hours": s.refresh_hours, "status": s.status, "last_run_at": s.last_run_at,
        "created_at": s.created_at,
    }  # fmt: skip


def _page(p: CrawlPage) -> dict[str, Any]:
    return {
        "id": p.id, "url": p.url, "final_url": p.final_url, "depth": p.depth, "status": p.status,
        "error": p.error, "http_status": p.http_status, "fetched_at": p.fetched_at,
        "document_id": p.vector_document_id,
    }  # fmt: skip


def build_router(
    svc: CrawlService,
    account: Callable[..., Any],
    org_member: Callable[[str], Callable[..., Any]],
    session: Callable[..., Any],
    *,
    killed: Callable[[AsyncSession], Awaitable[bool]] | None = None,
) -> APIRouter:
    """Routes under ``/orgs/{org_id}/crawl``. Members read; admins add, pause, recrawl and delete.
    ``killed`` lets a kill switch (flag ``kill:crawl``) stop changes."""
    r = APIRouter(prefix="/orgs/{org_id}/crawl")
    member, admin = org_member("member"), org_member("admin")

    async def live(s: AsyncSession) -> None:
        if killed and await killed(s):
            raise HTTPException(
                503,
                detail={
                    "type": "feature_disabled",
                    "message": "crawling is temporarily unavailable",
                },
            )

    @r.post("/sources", status_code=201)
    async def create(
        org_id: str, body: SourceIn, request: Request, u: Any = Depends(account),
        _: Any = Depends(admin), s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:  # fmt: skip
        await live(s)
        try:
            src = await svc.create_source(
                s, org_id, **body.model_dump(), user_sub=u.sub, actor=Actor(u.sub), request=request
            )
        except VectorError as e:
            raise _http(e) from e
        return _source(src)

    @r.get("/sources")
    async def list_sources(
        org_id: str, _: Any = Depends(member), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        return [_source(x) for x in await svc.sources(s, org_id)]

    @r.get("/sources/{source_id}/pages")
    async def pages(
        org_id: str, source_id: str, status: str | None = None, limit: int = 100, offset: int = 0,
        _: Any = Depends(member), s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:  # fmt: skip
        try:
            rows = await svc.pages(s, org_id, source_id, status=status, limit=limit, offset=offset)
        except VectorError as e:
            raise _http(e) from e
        return [_page(p) for p in rows]

    @r.post("/sources/{source_id}/recrawl", status_code=202)
    async def recrawl(
        org_id: str, source_id: str, _: Any = Depends(admin), s: AsyncSession = Depends(session)
    ) -> dict[str, bool]:
        await live(s)
        try:
            await svc.recrawl(s, org_id, source_id)
        except VectorError as e:
            raise _http(e) from e
        return {"queued": True}

    @r.patch("/sources/{source_id}")
    async def patch(
        org_id: str, source_id: str, body: SourcePatch, u: Any = Depends(account),
        _: Any = Depends(admin), s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:  # fmt: skip
        try:
            return _source(
                await svc.set_status(s, org_id, source_id, body.status, actor=Actor(u.sub))
            )
        except VectorError as e:
            raise _http(e) from e

    @r.delete("/sources/{source_id}", status_code=202)
    async def delete(
        org_id: str, source_id: str, request: Request, u: Any = Depends(account),
        _: Any = Depends(admin), s: AsyncSession = Depends(session),
    ) -> dict[str, bool]:  # fmt: skip
        try:
            await svc.delete_source(s, org_id, source_id, actor=Actor(u.sub), request=request)
        except VectorError as e:
            raise _http(e) from e
        return {"deleting": True}

    return r
