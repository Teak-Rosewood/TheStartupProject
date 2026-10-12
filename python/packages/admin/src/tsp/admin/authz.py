from __future__ import annotations

import re
from typing import Any

from sqladmin.authorization import AuthorizationBackend
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.exceptions import HTTPException
from starlette.requests import Request
from tsp.audit import Actor, record

from .roles import allowed

_DETAILS = re.compile(r"/([a-z-]+)/details/([^/]+)$")


class RoleAuthorization(AuthorizationBackend):
    """Role -> grants. Also records every staff view of a record's details (personal data)."""

    def __init__(self, engine: AsyncEngine):
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def load(self, request: Request) -> None:
        staff = request.session.get("staff") or {}
        request.state.staff_role = staff.get("role")
        m = _DETAILS.search(request.url.path)
        if m and request.method == "GET":
            # Fail closed: if the read cannot be audited, it does not happen.
            try:
                async with self._sessions() as s:
                    await record(
                        s,
                        "admin.viewed",
                        actor=Actor(staff.get("sub"), staff.get("role") or "staff"),
                        target=(m.group(1), m.group(2)),
                        request=request,
                    )
                    await s.commit()
            except Exception as e:
                raise HTTPException(503, "audit log unavailable") from e

    def has_permission(
        self, request: Request, identity: str, action: str, obj: Any | None = None
    ) -> bool:
        return allowed(getattr(request.state, "staff_role", None), identity, action)
