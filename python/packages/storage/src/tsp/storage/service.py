from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import aioboto3
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .models import StoredFile

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "application/pdf", "text/plain", "text/csv"}
)


class UploadRejected(ValueError):
    """The requested upload breaks the policy (type, size, name)."""


class QuotaExceeded(UploadRejected):
    """The organization has used its storage allowance."""


def safe_filename(name: str) -> str:
    """Strip any path, replace unsafe characters, cap the length; never empty, never a dotfile."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _SAFE.sub("_", base).strip("._") or "file"
    return cleaned[-100:]


@dataclass(frozen=True)
class UploadPolicy:
    max_bytes: int = 10 * 1024 * 1024
    allowed_types: frozenset[str] = DEFAULT_TYPES
    org_quota_bytes: int = 1024 * 1024 * 1024
    url_ttl_seconds: int = 300
    prefix: str = "uploads"


class ObjectStore(Protocol):
    async def presign_post(
        self, key: str, content_type: str, max_bytes: int, ttl: int
    ) -> dict[str, Any]: ...

    async def presign_get(self, key: str, ttl: int, filename: str | None = None) -> str: ...

    async def head(
        self, key: str
    ) -> tuple[int, str | None] | None: ...  # (size, content type) or None if absent

    async def delete(self, key: str) -> None: ...

    async def get(
        self, key: str
    ) -> bytes: ...  # whole object; for server-side processing of uploads


class S3ObjectStore:
    def __init__(self, bucket: str, region: str | None = None):
        self.bucket, self._region = bucket, region
        self._session = aioboto3.Session()

    def _client(self) -> Any:
        return self._session.client("s3", region_name=self._region)

    async def presign_post(
        self, key: str, content_type: str, max_bytes: int, ttl: int
    ) -> dict[str, Any]:
        async with self._client() as s3:
            # The browser posts straight to S3; these conditions are enforced by S3 itself, so a
            # client cannot upload another type, a bigger file, or to another key.
            return dict(
                await s3.generate_presigned_post(
                    self.bucket,
                    key,
                    Fields={"Content-Type": content_type, "x-amz-server-side-encryption": "AES256"},
                    Conditions=[
                        {"Content-Type": content_type},
                        {"x-amz-server-side-encryption": "AES256"},
                        ["content-length-range", 1, max_bytes],
                    ],
                    ExpiresIn=ttl,
                )
            )

    async def presign_get(self, key: str, ttl: int, filename: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{safe_filename(filename)}"'
            )
        async with self._client() as s3:
            return str(await s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl))

    async def head(self, key: str) -> tuple[int, str | None] | None:
        async with self._client() as s3:
            try:
                r = await s3.head_object(Bucket=self.bucket, Key=key)
            except Exception as e:
                if getattr(e, "response", {}).get("Error", {}).get("Code") in (
                    "404",
                    "NoSuchKey",
                    "NotFound",
                ):
                    return None
                raise
            return int(r["ContentLength"]), r.get("ContentType")

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            r = await s3.get_object(Bucket=self.bucket, Key=key)
            return bytes(await r["Body"].read())


class LocalObjectStore:
    """For tests and development: presigned 'posts' are plain dicts and objects are files in a directory."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    async def presign_post(
        self, key: str, content_type: str, max_bytes: int, ttl: int
    ) -> dict[str, Any]:
        return {
            "url": "http://local/upload",
            "fields": {"key": key, "Content-Type": content_type},
            "max_bytes": max_bytes,
        }

    async def presign_get(self, key: str, ttl: int, filename: str | None = None) -> str:
        return (self.dir / key).as_uri()

    async def head(self, key: str) -> tuple[int, str | None] | None:
        p = self.dir / key
        return (p.stat().st_size, None) if p.exists() else None

    async def delete(self, key: str) -> None:
        (self.dir / key).unlink(missing_ok=True)

    async def get(self, key: str) -> bytes:
        return (self.dir / key).read_bytes()

    def put(
        self, key: str, data: bytes
    ) -> None:  # test helper: what the browser's upload would have done
        p = self.dir / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@dataclass
class Uploads:
    store: ObjectStore
    policy: UploadPolicy = field(default_factory=UploadPolicy)
    # Called once the object is confirmed to exist; return False to reject it (malware scan, content checks).
    scan: Callable[[StoredFile], Awaitable[bool]] | None = None

    async def used_bytes(self, session: AsyncSession, org_id: str) -> int:
        return int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(StoredFile.size), 0)).where(
                        StoredFile.org_id == org_id, StoredFile.status.in_(("pending", "ready"))
                    )
                )
            ).scalar_one()
        )

    async def request_upload(
        self,
        session: AsyncSession,
        org_id: str,
        user_sub: str,
        filename: str,
        content_type: str,
        size: int,
        *,
        request: Any = None,
    ) -> dict[str, Any]:
        """Check the policy and the organization's quota, then hand the browser a signed upload."""
        p = self.policy
        if content_type not in p.allowed_types:
            raise UploadRejected(f"files of type {content_type!r} are not accepted")
        if not 0 < size <= p.max_bytes:
            raise UploadRejected(
                f"files must be between 1 byte and {p.max_bytes // (1024 * 1024)} MB"
            )
        if await self.used_bytes(session, org_id) + size > p.org_quota_bytes:
            raise QuotaExceeded(
                "your storage allowance is used up; delete files or upgrade your plan"
            )
        name = safe_filename(filename)
        key = f"{p.prefix}/{org_id}/{uuid.uuid4().hex}/{name}"  # the organization id is part of the path, never client-chosen
        f = StoredFile(
            org_id=org_id,
            user_sub=user_sub,
            key=key,
            filename=name,
            content_type=content_type,
            size=size,
        )
        session.add(f)
        await session.flush()
        post = await self.store.presign_post(
            key, content_type, min(size, p.max_bytes), p.url_ttl_seconds
        )
        await record(
            session,
            "file.upload_requested",
            actor=Actor(user_sub),
            target=("file", f.id),
            request=request,
            org=org_id,
            size=size,
        )
        return {"file_id": f.id, "upload": post}

    async def complete(self, session: AsyncSession, org_id: str, file_id: str) -> StoredFile:
        """Confirm the object arrived with the size and type that were declared, then mark it ready."""
        f = await session.get(StoredFile, file_id)
        if f is None or f.org_id != org_id:
            raise LookupError("file")
        if f.status == "ready":
            return f
        head = await self.store.head(f.key)
        if head is None:
            raise UploadRejected("the upload has not arrived yet")
        size, ctype = head
        if (
            size > self.policy.max_bytes
            or size > f.size
            or (ctype is not None and ctype != f.content_type)
        ):
            await self.store.delete(f.key)
            f.status = "rejected"
            raise UploadRejected("the uploaded file does not match what was declared")
        f.size = size
        if self.scan is not None and not await self.scan(f):
            await self.store.delete(f.key)
            f.status = "rejected"
            raise UploadRejected("the file was rejected by the content check")
        f.status = "ready"
        return f

    async def download_url(self, session: AsyncSession, org_id: str, file_id: str) -> str:
        f = await session.get(StoredFile, file_id)
        if f is None or f.org_id != org_id or f.status != "ready":
            raise LookupError("file")
        return await self.store.presign_get(f.key, self.policy.url_ttl_seconds, f.filename)

    async def delete(
        self,
        session: AsyncSession,
        org_id: str,
        file_id: str,
        actor_sub: str,
        *,
        request: Any = None,
    ) -> None:
        f = await session.get(StoredFile, file_id)
        if f is None or f.org_id != org_id or f.status == "deleted":
            raise LookupError("file")
        await self.store.delete(f.key)
        f.status = "deleted"
        await record(
            session,
            "file.deleted",
            actor=Actor(actor_sub),
            target=("file", f.id),
            request=request,
            org=org_id,
        )

    def register_with_compliance(self, compliance: Any) -> None:
        """Include a user's uploads in their export and delete them with the account."""

        async def export(session: AsyncSession, sub: str) -> list[dict[str, Any]]:
            rows = (
                (
                    await session.execute(
                        select(StoredFile).where(
                            StoredFile.user_sub == sub, StoredFile.status == "ready"
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [
                {"filename": r.filename, "size": r.size, "uploaded_at": r.created_at} for r in rows
            ]

        async def erase(session: AsyncSession, sub: str) -> None:
            rows = (
                (
                    await session.execute(
                        select(StoredFile).where(
                            StoredFile.user_sub == sub, StoredFile.status.in_(("pending", "ready"))
                        )
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                await self.store.delete(r.key)
                r.status = "deleted"

        compliance.register_exporter("files", export)
        compliance.register_eraser("files", erase)
