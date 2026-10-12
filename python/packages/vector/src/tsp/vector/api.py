from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor

from .service import VectorError, VectorService


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    embed_model: str | None = None


class DocumentIn(BaseModel):
    collection: str
    text: str | None = Field(default=None, max_length=2_000_000)
    file_id: str | None = None
    title: str = Field(default="", max_length=300)
    metadata: dict[str, Any] = {}


class SearchIn(BaseModel):
    collection: str
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=10, ge=1, le=50)
    filter: dict[str, Any] | None = None


def _http(e: VectorError) -> HTTPException:
    return HTTPException(e.status, detail={"type": e.type, "message": str(e)})


def build_router(
    svc: VectorService,
    account: Callable[..., Any],
    org_member: Callable[[str], Callable[..., Any]],
    session: Callable[..., Any],
    *,
    killed: Callable[[AsyncSession], Awaitable[bool]] | None = None,
) -> APIRouter:
    """Routes under ``/orgs/{org_id}/vector``. Members search and add documents; admins manage
    collections and delete. ``killed`` lets a kill switch (flag ``kill:vector``) stop everything."""
    r = APIRouter(prefix="/orgs/{org_id}/vector")
    member, admin = org_member("member"), org_member("admin")

    async def live(s: AsyncSession) -> None:
        if killed and await killed(s):
            raise HTTPException(
                503,
                detail={"type": "feature_disabled", "message": "search is temporarily unavailable"},
            )

    @r.post("/collections", status_code=201)
    async def create_collection(
        org_id: str,
        body: CollectionIn,
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        await live(s)
        try:
            c = await svc.create_collection(s, org_id, body.name, body.embed_model)
        except VectorError as e:
            raise _http(e) from e
        return {"id": c.id, "name": c.name, "embed_model": c.embed_model}

    @r.get("/collections")
    async def list_collections(
        org_id: str, _m: Any = Depends(member), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        return [
            {"id": c.id, "name": c.name, "embed_model": c.embed_model}
            for c in await svc.collections(s, org_id)
        ]

    @r.post("/documents", status_code=202)
    async def add_document(
        org_id: str,
        body: DocumentIn,
        u: Any = Depends(account),
        _m: Any = Depends(member),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        await live(s)
        try:
            d = await svc.add_document(
                s,
                org_id,
                body.collection,
                text_=body.text,
                file_id=body.file_id,
                title=body.title,
                user_sub=u.sub,
                metadata=body.metadata,
            )
        except VectorError as e:
            raise _http(e) from e
        return {"id": d.id, "status": d.status, "title": d.title}

    @r.get("/documents")
    async def list_documents(
        org_id: str,
        collection: str | None = None,
        _m: Any = Depends(member),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        try:
            docs = await svc.documents(s, org_id, collection)
        except VectorError as e:
            raise _http(e) from e
        return [
            {
                "id": d.id,
                "title": d.title,
                "status": d.status,
                "error": d.error,
                "created_at": d.created_at,
            }
            for d in docs
        ]

    @r.delete("/documents/{document_id}", status_code=204)
    async def delete_document(
        org_id: str,
        document_id: str,
        request: Request,
        u: Any = Depends(account),
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> None:
        try:
            await svc.delete_document(
                s, org_id, document_id, actor=Actor(u.sub, "customer"), request=request
            )
        except VectorError as e:
            raise _http(e) from e

    @r.post("/search")
    async def search(
        org_id: str,
        body: SearchIn,
        request: Request,
        _m: Any = Depends(member),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        await live(s)
        try:
            hits = await svc.search(
                s,
                org_id,
                body.collection,
                body.query,
                k=body.k,
                filter=body.filter,
                request_id=getattr(request.state, "request_id", None),
            )
        except VectorError as e:
            raise _http(e) from e
        return {"results": hits}

    return r
