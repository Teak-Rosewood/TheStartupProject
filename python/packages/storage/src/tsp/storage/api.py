from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .service import QuotaExceeded, UploadRejected, Uploads


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=100)
    size: int = Field(gt=0)


def build_router(
    uploads: Uploads,
    account: Callable[..., Any],
    org_member: Callable[[str], Callable[..., Any]],
    session: Callable[..., Any],
) -> APIRouter:
    """Routes under ``/orgs/{org_id}/files``. Any member may upload and list; deleting needs an admin."""
    r = APIRouter()
    member, admin = org_member("member"), org_member("admin")

    @r.post("/orgs/{org_id}/files", status_code=201)
    async def request_upload(
        org_id: str,
        body: UploadIn,
        request: Request,
        u: Any = Depends(account),
        _m: Any = Depends(member),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        try:
            return await uploads.request_upload(
                s, org_id, u.sub, body.filename, body.content_type, body.size, request=request
            )
        except QuotaExceeded as e:
            raise HTTPException(
                402, detail={"type": "storage_quota_exceeded", "message": str(e)}
            ) from e
        except UploadRejected as e:
            raise HTTPException(422, detail={"type": "upload_rejected", "message": str(e)}) from e

    @r.post("/orgs/{org_id}/files/{file_id}/complete")
    async def complete(
        org_id: str, file_id: str, _m: Any = Depends(member), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        try:
            f = await uploads.complete(s, org_id, file_id)
        except LookupError as e:
            raise HTTPException(404, "not found") from e
        except UploadRejected as e:
            raise HTTPException(422, detail={"type": "upload_rejected", "message": str(e)}) from e
        return {"file_id": f.id, "status": f.status, "size": f.size}

    @r.get("/orgs/{org_id}/files/{file_id}/download")
    async def download(
        org_id: str, file_id: str, _m: Any = Depends(member), s: AsyncSession = Depends(session)
    ) -> dict[str, str]:
        try:
            return {"url": await uploads.download_url(s, org_id, file_id)}
        except LookupError as e:
            raise HTTPException(404, "not found") from e

    @r.delete("/orgs/{org_id}/files/{file_id}", status_code=204)
    async def delete(
        org_id: str,
        file_id: str,
        request: Request,
        u: Any = Depends(account),
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> None:
        try:
            await uploads.delete(s, org_id, file_id, u.sub, request=request)
        except LookupError as e:
            raise HTTPException(404, "not found") from e

    return r
