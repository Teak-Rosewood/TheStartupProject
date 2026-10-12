from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .documents import Document, Manifest
from .identity import IdentityProvider
from .models import Consent, DataRequest
from .storage import ExportStore

Exporter = Callable[[AsyncSession, str], Awaitable[Any]]
Eraser = Callable[[AsyncSession, str], Awaitable[None]]


class ConsentError(Exception):
    """A consent submission named an unknown document or an out-of-date version."""


@dataclass
class ExportResult:
    request: DataRequest
    url: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pseudonym(sub: str) -> str:
    return "deleted:" + hashlib.sha256(sub.encode()).hexdigest()[:16]


class Compliance:
    """Consent, export and deletion for one app.

    Other modules join in with ``register_exporter`` / ``register_eraser``, so this module never
    imports them. Exporters return JSON-serializable data about the user; erasers delete or
    anonymize it. Records the law requires you to keep (tax invoices) are anonymized, not deleted.
    """

    def __init__(
        self,
        manifest: Manifest | None,
        *,
        store: ExportStore | None = None,
        identity: IdentityProvider | None = None,
        grace_days: int = 30,
        export_ttl_days: int = 7,
        on_deletion_requested: Callable[[AsyncSession, str, DataRequest], Awaitable[None]]
        | None = None,
    ):
        self.manifest = manifest
        self.store = store
        self.identity = identity
        self.grace = timedelta(days=grace_days)
        self.export_ttl = timedelta(days=export_ttl_days)
        self.on_deletion_requested = on_deletion_requested
        self._exporters: dict[str, Exporter] = {"compliance": self._export_own_records}
        self._erasers: dict[str, Eraser] = {"compliance": self._erase_own_records}

    def register_exporter(self, name: str, fn: Exporter) -> None:
        self._exporters[name] = fn

    def register_eraser(self, name: str, fn: Eraser) -> None:
        self._erasers[name] = fn

    # -- consent -----------------------------------------------------------------------------

    def _manifest(self) -> Manifest:
        if self.manifest is None:
            raise RuntimeError("legal documents are not configured")
        return self.manifest

    async def stale_documents(
        self, session: AsyncSession, sub: str, doc_ids: Iterable[str] | None = None
    ) -> list[Document]:
        """Documents the user has not accepted at their current version."""
        m = self._manifest()
        wanted = [m.get(i) for i in doc_ids] if doc_ids else m.required()
        if not wanted:
            return []
        rows = await session.execute(
            select(Consent.document, Consent.version)
            .where(Consent.user_sub == sub, Consent.document.in_([d.id for d in wanted]))
            .order_by(Consent.accepted_at.desc(), Consent.id.desc())
        )
        latest: dict[str, str] = {}
        for document, version in rows:
            latest.setdefault(document, version)
        return [d for d in wanted if latest.get(d.id) != d.version]

    async def accept(
        self,
        session: AsyncSession,
        sub: str,
        accepted: Mapping[str, str],
        *,
        request: Any = None,
        source: str = "app",
    ) -> None:
        """Record that the user accepted ``{document id: version they saw}``; versions must be current."""
        m = self._manifest()
        for doc_id, version in accepted.items():
            try:
                current = m.get(doc_id).version
            except KeyError as e:
                raise ConsentError(str(e)) from e
            if version != current:
                raise ConsentError(f"{doc_id}: version {version!r} is not current ({current!r})")
        from tsp.audit import client_ip

        for doc_id, version in accepted.items():
            session.add(
                Consent(
                    user_sub=sub,
                    document=doc_id,
                    version=version,
                    source=source,
                    ip=client_ip(request) if request is not None else None,
                    user_agent=(request.headers.get("user-agent") if request is not None else None),
                )
            )
        await record(
            session, "consent.accepted", actor=Actor(sub), request=request, documents=dict(accepted)
        )

    # -- export ------------------------------------------------------------------------------

    async def request_export(
        self, session: AsyncSession, sub: str, *, request: Any = None
    ) -> ExportResult:
        if self.store is None:
            raise RuntimeError("no export store configured")
        req = DataRequest(user_sub=sub, kind="export", status="pending")
        session.add(req)
        await session.flush()
        try:
            data: dict[str, Any] = {"exported_at": _now().isoformat(), "user": sub, "data": {}}
            for name, fn in self._exporters.items():
                data["data"][name] = await fn(session, sub)
            key = f"exports/{_pseudonym(sub)[8:]}/{req.id}.json"
            await self.store.put(key, json.dumps(data, default=str, indent=2).encode())
            req.status, req.result_key, req.completed_at = "completed", key, _now()
        except Exception as e:
            req.status, req.error = "failed", str(e)[:500]
            raise
        finally:
            await record(
                session,
                "data.export_requested",
                actor=Actor(sub),
                request=request,
                target=("data_request", req.id),
                status=req.status,
            )
        return ExportResult(req, await self.export_url(req))

    async def export_url(self, req: DataRequest) -> str | None:
        if self.store is None or req.status != "completed" or not req.result_key:
            return None
        if req.completed_at and _now() - req.completed_at > self.export_ttl:
            return None  # the file has expired (bucket lifecycle)
        return await self.store.url(req.result_key, 3600)

    async def get_request(
        self, session: AsyncSession, sub: str, request_id: str
    ) -> DataRequest | None:
        return (
            await session.execute(
                select(DataRequest).where(DataRequest.id == request_id, DataRequest.user_sub == sub)
            )
        ).scalar_one_or_none()

    # -- deletion ----------------------------------------------------------------------------

    async def pending_deletion(self, session: AsyncSession, sub: str) -> DataRequest | None:
        return (
            (
                await session.execute(
                    select(DataRequest).where(
                        DataRequest.user_sub == sub,
                        DataRequest.kind == "delete",
                        DataRequest.status == "pending",
                    )
                )
            )
            .scalars()
            .first()
        )

    async def request_delete(
        self, session: AsyncSession, sub: str, *, request: Any = None
    ) -> DataRequest:
        """Start the grace period. Sign-in is blocked and sessions end now; data goes when it expires."""
        existing = await self.pending_deletion(session, sub)
        if existing:
            return existing
        scheduled = _now() + self.grace
        req = DataRequest(user_sub=sub, kind="delete", status="pending", scheduled_for=scheduled)
        session.add(req)
        await session.flush()
        if self.identity:
            await self.identity.sign_out(sub)
        await record(
            session,
            "account.deletion_requested",
            actor=Actor(sub),
            request=request,
            target=("data_request", req.id),
            scheduled_for=scheduled.isoformat(),
        )
        if self.on_deletion_requested:
            try:  # telling the user is best effort and must never block the request
                async with session.begin_nested():
                    await self.on_deletion_requested(session, sub, req)
            except Exception:
                logging.getLogger("tsp.compliance").exception("deletion hook failed")
        return req

    async def cancel_delete(self, session: AsyncSession, sub: str, *, request: Any = None) -> bool:
        req = await self.pending_deletion(session, sub)
        if req is None:
            return False
        req.status, req.completed_at = "cancelled", _now()
        await record(
            session,
            "account.deletion_cancelled",
            actor=Actor(sub),
            request=request,
            target=("data_request", req.id),
        )
        return True

    async def execute_deletion(
        self, session: AsyncSession, req: DataRequest, *, actor: Actor | None = None
    ) -> None:
        """Erase the user's data, then their identity. Run by an admin or the scheduled job."""
        if req.kind != "delete" or req.status != "pending":
            raise ValueError("not a pending deletion request")
        sub = req.user_sub
        try:
            for fn in self._erasers.values():
                await fn(session, sub)
            if self.identity:
                await self.identity.delete(sub)
            req.status, req.completed_at = "completed", _now()
        except Exception as e:
            req.status, req.error = "failed", str(e)[:500]
            raise
        finally:
            await record(
                session,
                "account.deleted",
                actor=actor or Actor(None, "system"),
                target=("user", _pseudonym(sub)),
                status=req.status,
            )

    async def run_due_deletions(self, session: AsyncSession, *, now: datetime | None = None) -> int:
        """Execute every deletion whose grace period has ended. Returns how many completed."""
        due = (
            (
                await session.execute(
                    select(DataRequest).where(
                        DataRequest.kind == "delete",
                        DataRequest.status == "pending",
                        DataRequest.scheduled_for <= (now or _now()),
                    )
                )
            )
            .scalars()
            .all()
        )
        done = 0
        for req in due:
            try:
                await self.execute_deletion(session, req)
                done += 1
            except Exception:  # recorded on the request; keep going with the others
                continue
        return done

    # -- built-in data ------------------------------------------------------------------------

    async def _export_own_records(self, session: AsyncSession, sub: str) -> dict[str, Any]:
        consents = (
            (await session.execute(select(Consent).where(Consent.user_sub == sub))).scalars().all()
        )
        requests = (
            (await session.execute(select(DataRequest).where(DataRequest.user_sub == sub)))
            .scalars()
            .all()
        )
        return {
            "consents": [
                {
                    "document": c.document,
                    "version": c.version,
                    "accepted_at": c.accepted_at,
                    "source": c.source,
                }
                for c in consents
            ],
            "data_requests": [
                {"id": r.id, "kind": r.kind, "status": r.status, "requested_at": r.requested_at}
                for r in requests
            ],
        }

    async def _erase_own_records(self, session: AsyncSession, sub: str) -> None:
        """Keep proof that consent was given and requests were answered, but detach it from the person."""
        alias = _pseudonym(sub)
        await session.execute(
            update(Consent)
            .where(Consent.user_sub == sub)
            .values(user_sub=alias, ip=None, user_agent=None)
        )
        await session.execute(
            update(DataRequest)
            .where(DataRequest.user_sub == sub, DataRequest.kind == "export")
            .values(user_sub=alias, result_key=None)
        )
