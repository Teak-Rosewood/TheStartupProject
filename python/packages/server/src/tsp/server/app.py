from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from tsp.auth import CognitoVerifier
from tsp.cache import cache_from_url
from tsp.db import Database
from tsp.nosql import Table

from .settings import Settings

log = logging.getLogger("tsp.server")


def _error(status: int, kind: str, message: str, request_id: str | None = None) -> JSONResponse:
    body: dict = {"error": {"type": kind, "message": message}}
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(body, status_code=status)


def create_app(
    settings: Settings,
    *,
    background: Sequence[Callable[[FastAPI], AbstractAsyncContextManager[None]]] = (),
) -> FastAPI:
    """Build the app. Include your own routers with ``app.include_router(r, prefix=settings.api_prefix)``.

    ``background`` items are async context managers entered after the database, cache and clients
    exist and exited (in reverse order) before they close; use them for in-process workers.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings.database_url) if settings.database_url else None
        app.state.cache = cache_from_url(settings.cache_url)
        app.state.table = (
            Table(
                settings.dynamodb_table,
                region=settings.aws_region,
                endpoint_url=settings.dynamodb_endpoint,
            )
            if settings.dynamodb_table
            else None
        )
        app.state.verifier = (
            CognitoVerifier(settings.auth_issuer, settings.auth_client_ids)
            if settings.auth_issuer and settings.auth_client_ids
            else None
        )
        try:
            async with AsyncExitStack() as stack:
                for factory in background:
                    await stack.enter_async_context(factory(app))
                yield
        finally:
            if app.state.db:
                await app.state.db.dispose()
            await app.state.cache.close()
            if app.state.verifier:
                await app.state.verifier.close()

    app = FastAPI(
        title="tsp api", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    health_path = f"{settings.api_prefix}/health"

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def edge(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        start = time.perf_counter()
        expected = settings.origin_verify
        if expected and request.url.path != health_path:
            sent = request.headers.get("x-origin-verify", "")
            if not hmac.compare_digest(sent.encode(), expected.encode()):
                return _error(403, "forbidden", "request did not come through the edge", request_id)
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        log.info(
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000, 1),
                    "request_id": request_id,
                }
            },
        )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):  # structured errors: {"type": ..., "message": ..., **extra}
            extra = {k: v for k, v in detail.items() if k not in ("type", "message")}
            resp = _error(
                exc.status_code,
                str(detail.get("type", "http_error")),
                str(detail.get("message", "")),
            )
            if extra:
                resp_body = json.loads(resp.body)
                resp_body["error"].update(extra)
                resp = JSONResponse(resp_body, status_code=exc.status_code)
        else:
            resp = _error(exc.status_code, "http_error", str(detail))
        resp.headers.update(exc.headers or {})
        return resp

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(
            422,
            "invalid_request",
            "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "internal_error", "internal server error")

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: the process is up. Used by the container health check; touches no dependency."""
        return {"status": "ok"}

    @router.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Readiness: every configured dependency answers."""
        st = request.app.state
        checks: dict[str, Awaitable[object]] = {}
        if st.db:
            checks["db"] = st.db.ping()
        checks["cache"] = st.cache.ping()
        if st.table:
            checks["nosql"] = st.table.ping()
        results = await asyncio.gather(
            *(asyncio.wait_for(c, 3) for c in checks.values()), return_exceptions=True
        )
        status = {k: ("ok" if r is True else "fail") for k, r in zip(checks, results, strict=True)}
        ok = all(v == "ok" for v in status.values())
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "checks": status}, status_code=200 if ok else 503
        )

    app.include_router(router, prefix=settings.api_prefix)
    return app
