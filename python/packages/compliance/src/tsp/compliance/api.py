"""FastAPI routes and dependencies. Dependencies for user and session are passed in, so this module
does not import tsp.server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .service import Compliance, ConsentError

COOKIE_CATEGORIES = [
    {"id": "necessary", "required": True},
    {"id": "preferences", "required": False},
    {"id": "analytics", "required": False},
    {"id": "marketing", "required": False},
]


class AcceptBody(BaseModel):
    documents: dict[str, str]  # document id -> version the user saw


def _need_manifest(c: Compliance) -> None:
    if c.manifest is None:
        raise HTTPException(
            503,
            detail={
                "type": "legal_not_configured",
                "message": "legal documents are not configured",
            },
        )


def make_guards(
    c: Compliance, user: Callable[..., Any], session: Callable[..., Any]
) -> tuple[Callable[..., Callable[..., Any]], Callable[..., Any]]:
    """Return ``(require_consent, require_active_account)`` dependency factories bound to ``c``."""

    def require_consent(*doc_ids: str) -> Callable[..., Any]:
        async def dep(u: Any = Depends(user), s: AsyncSession = Depends(session)) -> Any:
            _need_manifest(c)
            stale = await c.stale_documents(s, u.sub, doc_ids or None)
            if stale:
                raise HTTPException(
                    403,
                    detail={
                        "type": "consent_required",
                        "message": "please accept the updated legal documents",
                        "documents": [
                            {"id": d.id, "title": d.title, "version": d.version, "url": d.url}
                            for d in stale
                        ],
                    },
                )
            return u

        return dep

    async def require_active_account(
        u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> Any:
        pending = await c.pending_deletion(s, u.sub)
        if pending:
            raise HTTPException(
                403,
                detail={
                    "type": "account_pending_deletion",
                    "message": "this account is scheduled for deletion; cancel the request to continue",
                    "scheduled_for": pending.scheduled_for.isoformat()
                    if pending.scheduled_for
                    else None,
                },
            )
        return u

    return require_consent, require_active_account


def build_router(c: Compliance, user: Callable[..., Any], session: Callable[..., Any]) -> APIRouter:
    r = APIRouter()

    @r.get("/legal/documents")
    async def documents() -> dict[str, Any]:
        _need_manifest(c)
        assert c.manifest is not None
        return {
            "documents": [d.model_dump(exclude={"sha256"}) for d in c.manifest.documents.values()]
        }

    @r.get("/legal/config")
    async def config() -> dict[str, Any]:
        _need_manifest(c)
        assert c.manifest is not None
        return {
            "consent_required": [d.id for d in c.manifest.required()],
            "documents": {
                d.id: {"title": d.title, "version": d.version, "url": d.url}
                for d in c.manifest.documents.values()
            },
            "cookie_categories": COOKIE_CATEGORIES,
        }

    @r.get("/me/consents")
    async def my_consents(
        u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        _need_manifest(c)
        stale = await c.stale_documents(s, u.sub)
        return {
            "up_to_date": not stale,
            "required": [{"id": d.id, "version": d.version, "url": d.url} for d in stale],
        }

    @r.post("/me/consents", status_code=201)
    async def accept(
        body: AcceptBody,
        request: Request,
        u: Any = Depends(user),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        _need_manifest(c)
        try:
            await c.accept(s, u.sub, body.documents, request=request)
        except ConsentError as e:
            raise HTTPException(
                409, detail={"type": "consent_version_mismatch", "message": str(e)}
            ) from e
        return {"accepted": body.documents}

    @r.post("/me/export", status_code=201)
    async def export(
        request: Request, u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        result = await c.request_export(s, u.sub, request=request)
        return {"id": result.request.id, "status": result.request.status, "url": result.url}

    @r.get("/me/export/{request_id}")
    async def export_status(
        request_id: str, u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        req = await c.get_request(s, u.sub, request_id)
        if req is None or req.kind != "export":
            raise HTTPException(404, "not found")
        return {"id": req.id, "status": req.status, "url": await c.export_url(req)}

    @r.post("/me/delete", status_code=202)
    async def delete(
        request: Request, u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        req = await c.request_delete(s, u.sub, request=request)
        return {"id": req.id, "status": req.status, "scheduled_for": req.scheduled_for}

    @r.get("/me/delete")
    async def delete_status(
        u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        req = await c.pending_deletion(s, u.sub)
        return {"pending": req is not None, "scheduled_for": req.scheduled_for if req else None}

    @r.post("/me/delete/cancel")
    async def delete_cancel(
        request: Request, u: Any = Depends(user), s: AsyncSession = Depends(session)
    ) -> dict[str, bool]:
        return {"cancelled": await c.cancel_delete(s, u.sub, request=request)}

    return r
