"""Amazon S3 Vectors backend: one vector index per organization and collection.

Written against the documented ``s3vectors`` boto3 API and exercised with a fake client in tests;
it has not been run against the real service. Query cost grows with index size, so one index per
organization (not one shared index) is what keeps it cheap and isolated.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .store import Hit, VectorItem

_NONFILTERABLE = ["content"]
BATCH = 500  # put_vectors accepts up to 500 vectors per call


class S3VectorStore:
    def __init__(
        self, bucket: str, region: str | None = None, *, client: Any = None, dims: int = 1024
    ):
        self.bucket, self.dims = bucket, dims
        if client is None:
            import boto3

            client = boto3.client("s3vectors", region_name=region)
        self._c = client
        self._ready: set[str] = set()

    @staticmethod
    def index_name(org_id: str, collection_id: str) -> str:
        return f"org-{org_id}-{collection_id}"[:63].lower()

    async def _call(self, fn: str, **kw: Any) -> Any:
        return await asyncio.to_thread(getattr(self._c, fn), **kw)

    async def _ensure(self, name: str) -> None:
        if name in self._ready:
            return
        try:
            await self._call(
                "create_index",
                vectorBucketName=self.bucket,
                indexName=name,
                dataType="float32",
                dimension=self.dims,
                distanceMetric="cosine",
                metadataConfiguration={"nonFilterableMetadataKeys": _NONFILTERABLE},
            )
        except Exception as e:
            if "Conflict" not in type(e).__name__ and "already exists" not in str(e).lower():
                raise
        self._ready.add(name)

    async def upsert(self, org_id: str, collection_id: str, items: list[VectorItem]) -> None:
        if not items:
            return
        name = self.index_name(org_id, collection_id)
        await self._ensure(name)
        for i in range(0, len(items), BATCH):
            await self._call(
                "put_vectors",
                vectorBucketName=self.bucket,
                indexName=name,
                vectors=[
                    {
                        "key": it["id"],
                        "data": {"float32": it["embedding"]},
                        "metadata": {
                            **it["metadata"],
                            "document_id": it["document_id"],
                            "ordinal": it["ordinal"],
                            "embed_model": it["embed_model"],
                            "content": it["content"],
                        },
                    }
                    for it in items[i : i + BATCH]
                ],
            )

    async def replace_document(
        self, org_id: str, collection_id: str, document_id: str, items: list[VectorItem]
    ) -> None:
        # Not atomic (S3 Vectors has no transactions): write the new vectors first, then drop the
        # old ones, so a crash leaves duplicates that the retry removes, never a missing document.
        await self.upsert(org_id, collection_id, items)
        keep = {it["id"] for it in items}
        stale = [
            k for k, _ in await self._list(org_id, collection_id, document_id) if k not in keep
        ]
        await self.delete(org_id, collection_id, stale)

    async def query(
        self,
        org_id: str,
        collection_id: str,
        vector: list[float],
        k: int,
        filter: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> list[Hit]:
        name = self.index_name(org_id, collection_id)
        conds = [{key: {"$eq": v}} for key, v in (filter or {}).items()]
        if model:
            conds.append({"embed_model": {"$eq": model}})
        kw: dict[str, Any] = {}
        if conds:
            kw["filter"] = conds[0] if len(conds) == 1 else {"$and": conds}
        try:
            out = await self._call(
                "query_vectors",
                vectorBucketName=self.bucket,
                indexName=name,
                queryVector={"float32": vector},
                topK=k,
                returnMetadata=True,
                returnDistance=True,
                **kw,
            )
        except Exception as e:
            if "NotFound" in type(e).__name__:
                return []
            raise
        hits: list[Hit] = []
        for v in out.get("vectors", []):
            meta = dict(v.get("metadata") or {})
            doc = meta.pop("document_id", "")
            content = meta.pop("content", "")
            meta.pop("ordinal", None)
            meta.pop("embed_model", None)
            hits.append(
                {
                    "id": v["key"],
                    "document_id": doc,
                    "score": 1.0 - float(v.get("distance", 1.0)),
                    "content": content,
                    "metadata": meta,
                }
            )
        return hits

    async def delete(self, org_id: str, collection_id: str, ids: list[str]) -> None:
        name = self.index_name(org_id, collection_id)
        for i in range(0, len(ids), BATCH):
            await self._call(
                "delete_vectors",
                vectorBucketName=self.bucket,
                indexName=name,
                keys=ids[i : i + BATCH],
            )

    async def _list(
        self, org_id: str, collection_id: str, document_id: str | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        name = self.index_name(org_id, collection_id)
        out: list[tuple[str, dict[str, Any]]] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {
                "vectorBucketName": self.bucket,
                "indexName": name,
                "returnMetadata": True,
            }
            if token:
                kw["nextToken"] = token
            try:
                page = await self._call("list_vectors", **kw)
            except Exception as e:
                if "NotFound" in type(e).__name__:
                    return out
                raise
            for v in page.get("vectors", []):
                meta = v.get("metadata") or {}
                if document_id is None or meta.get("document_id") == document_id:
                    out.append((v["key"], meta))
            token = page.get("nextToken")
            if not token:
                return out

    async def _collections(self, org_id: str) -> list[str]:
        out: list[str] = []
        token: str | None = None
        prefix = f"org-{org_id}-".lower()
        while True:
            kw: dict[str, Any] = {"vectorBucketName": self.bucket, "prefix": prefix}
            if token:
                kw["nextToken"] = token
            page = await self._call("list_indexes", **kw)
            out += [i["indexName"] for i in page.get("indexes", [])]
            token = page.get("nextToken")
            if not token:
                return out

    async def delete_document(self, org_id: str, document_id: str) -> None:
        for name in await self._collections(org_id):
            keys = []
            token: str | None = None
            while True:
                kw: dict[str, Any] = {
                    "vectorBucketName": self.bucket,
                    "indexName": name,
                    "returnMetadata": True,
                }
                if token:
                    kw["nextToken"] = token
                page = await self._call("list_vectors", **kw)
                keys += [
                    v["key"]
                    for v in page.get("vectors", [])
                    if (v.get("metadata") or {}).get("document_id") == document_id
                ]
                token = page.get("nextToken")
                if not token:
                    break
            for i in range(0, len(keys), BATCH):
                await self._call(
                    "delete_vectors",
                    vectorBucketName=self.bucket,
                    indexName=name,
                    keys=keys[i : i + BATCH],
                )

    async def chunks(
        self, org_id: str, collection_id: str, document_id: str, model: str | None = None
    ) -> list[dict[str, Any]]:
        rows = []
        for _, m in await self._list(org_id, collection_id, document_id):
            if model and m.get("embed_model") != model:
                continue
            extra = {
                k: v
                for k, v in m.items()
                if k not in {"document_id", "ordinal", "embed_model", "content"}
            }
            content = m.get("content", "")
            rows.append(
                {
                    "ordinal": int(m.get("ordinal", 0)),
                    "content": content,
                    "token_count": max(1, len(content) // 4),
                    "metadata": extra,
                }
            )
        return sorted(rows, key=lambda r: r["ordinal"])

    async def delete_other_models(self, org_id: str, collection_id: str, keep_model: str) -> None:
        stale = [
            k
            for k, m in await self._list(org_id, collection_id)
            if m.get("embed_model") != keep_model
        ]
        await self.delete(org_id, collection_id, stale)

    async def delete_org(self, org_id: str) -> None:
        for name in await self._collections(org_id):
            await self._call("delete_index", vectorBucketName=self.bucket, indexName=name)
            self._ready.discard(name)
