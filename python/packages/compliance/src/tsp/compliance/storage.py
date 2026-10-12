"""Where export files go. S3 in production, a directory in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import aioboto3


class ExportStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def url(self, key: str, ttl_seconds: int) -> str: ...


class LocalExportStore:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    async def put(self, key: str, data: bytes) -> None:
        path = self.dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def url(self, key: str, ttl_seconds: int) -> str:
        return (self.dir / key).as_uri()


class S3ExportStore:
    """Private bucket with a short lifecycle (the stack sets 7 days); links are presigned."""

    def __init__(self, bucket: str, region: str | None = None):
        self.bucket = bucket
        self._region = region
        self._session = aioboto3.Session()

    async def put(self, key: str, data: bytes) -> None:
        async with self._session.client("s3", region_name=self._region) as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )

    async def url(self, key: str, ttl_seconds: int) -> str:
        async with self._session.client("s3", region_name=self._region) as s3:
            return str(
                await s3.generate_presigned_url(
                    "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_seconds
                )
            )
