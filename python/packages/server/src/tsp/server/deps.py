"""FastAPI dependencies. The resources live on ``app.state`` and are created in the app lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.auth import AuthError, Claims
from tsp.cache import Cache
from tsp.nosql import Table


def _need(request: Request, name: str) -> Any:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(503, f"{name} is not configured")
    return value


async def current_user(request: Request) -> Claims:
    verifier = _need(request, "verifier")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        return await verifier.verify(token)
    except AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"}) from e


def require_groups(*groups: str) -> Callable[..., Any]:
    """Dependency: the user must belong to at least one of ``groups``."""
    from fastapi import Depends

    async def dep(user: Claims = Depends(current_user)) -> Claims:
        if not set(groups) & set(user.groups):
            raise HTTPException(403, "insufficient permissions")
        return user

    return dep


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with _need(request, "db").session() as session:
        yield session


def get_cache(request: Request) -> Cache:
    return _need(request, "cache")  # type: ignore[no-any-return]


def get_table(request: Request) -> Table:
    return _need(request, "table")  # type: ignore[no-any-return]
