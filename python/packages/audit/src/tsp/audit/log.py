from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


@dataclass(frozen=True)
class Actor:
    """Who acted. ``role`` is free text such as ``user``, ``support``, ``system``."""

    sub: str | None
    role: str = "user"


@dataclass(frozen=True)
class AuditEntry:
    action: str
    actor: Actor
    target_type: str | None = None
    target_id: str | None = None
    ip: str | None = None
    request_id: str | None = None
    meta: dict[str, Any] | None = None


def client_ip(request: Any) -> str | None:
    """Viewer IP. Behind CloudFront the first ``X-Forwarded-For`` entry is the viewer.

    Only trust this when the origin is reachable solely through the edge (it is, in this stack:
    the security group admits CloudFront and the app checks the origin-verify header).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return str(xff.split(",")[0].strip()) or None
    return request.client.host if request.client else None


async def record(
    session: AsyncSession,
    action: str,
    *,
    actor: Actor,
    target: tuple[str, str] | None = None,
    request: Any = None,
    **meta: Any,
) -> None:
    """Add an audit row in the caller's transaction, so it commits or rolls back with the change."""
    session.add(
        AuditLog(
            actor_sub=actor.sub,
            actor_role=actor.role,
            action=action,
            target_type=target[0] if target else None,
            target_id=target[1] if target else None,
            ip=client_ip(request) if request is not None else None,
            request_id=getattr(request.state, "request_id", None) if request is not None else None,
            meta=meta,
        )
    )
    await session.flush()
