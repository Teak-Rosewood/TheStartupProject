from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RawStore(Protocol):
    """Where raw fetched bytes and run manifests go. ``key`` is the location relative to the bucket."""

    async def put(self, key: str, data: bytes, content_type: str) -> str: ...  # returns the URI


class LocalRawStore:
    """Directory-backed store for development and tests."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        p = self.dir / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key


class S3RawStore:
    """Needs the ``s3`` extra (aioboto3)."""

    def __init__(self, bucket: str, region: str | None = None):
        import aioboto3

        self.bucket, self._region = bucket, region
        self._session: Any = aioboto3.Session()

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        async with self._session.client("s3", region_name=self._region) as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"
