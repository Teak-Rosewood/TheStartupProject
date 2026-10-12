"""The internal API the browser render worker talks to.

The render worker runs a real browser on pages we do not control, so it gets no database access and
no secrets other than a token that opens only these two endpoints. It asks for work, renders, and
posts the result; the API applies it with the same checks as any other fetch.
"""

from __future__ import annotations

import hmac
import os
import socket
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from tsp.vector import NotFound

if TYPE_CHECKING:
    from .service import CrawlService


class RenderResult(BaseModel):
    """What the browser saw. Either ``error`` is set, or ``markdown`` holds the page text."""

    final_url: str = Field(default="", max_length=4000)
    status: int | None = Field(default=None, ge=100, le=599)
    title: str | None = Field(default=None, max_length=1000)
    markdown: str = Field(default="", max_length=2_000_000)
    links: list[str] = Field(default_factory=list, max_length=5000)
    error: str | None = Field(default=None, max_length=1000)
    transient: bool = False  # retry later (timeout, 5xx) rather than record a final failure


def build_render_router(svc: CrawlService, token: str) -> APIRouter:
    """``POST /internal/crawl/render/claim`` and ``POST /internal/crawl/render/{job_id}/result``.

    Mount it only where the render worker can reach it (the same private route as the API is fine)
    and never expose it through CloudFront. ``token`` is compared in constant time."""
    if len(token) < 24:
        raise ValueError("the render token must be at least 24 characters")
    r = APIRouter(prefix="/internal/crawl/render")

    def authed(authorization: str = Header(default="")) -> None:
        scheme, _, given = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "invalid token")

    worker_id = f"render:{socket.gethostname()}:{os.getpid()}"

    @r.post("/claim", dependencies=[Depends(authed)])
    async def claim() -> Any:
        work = await svc.claim_render(worker_id)
        return work if work is not None else Response(status_code=204)

    @r.post("/{job_id}/result", dependencies=[Depends(authed)], status_code=204)
    async def result(job_id: int, body: RenderResult) -> Response:
        try:
            await svc.submit_render(job_id, body)
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        return Response(status_code=204)

    return r
