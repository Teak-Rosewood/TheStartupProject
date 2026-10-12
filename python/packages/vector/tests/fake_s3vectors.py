"""An in-memory stand-in for the boto3 ``s3vectors`` client (documented API shapes only)."""

import math


class NotFoundException(Exception):
    pass


class ConflictException(Exception):
    pass


def _match(meta, flt):
    if not flt:
        return True
    if "$and" in flt:
        return all(_match(meta, f) for f in flt["$and"])
    return all(meta.get(k) == v["$eq"] for k, v in flt.items())


class FakeS3Vectors:
    def __init__(self):
        self.indexes: dict[str, dict[str, dict]] = {}

    def _idx(self, name):
        if name not in self.indexes:
            raise NotFoundException(name)
        return self.indexes[name]

    def create_index(
        self,
        *,
        vectorBucketName,
        indexName,
        dataType,
        dimension,
        distanceMetric,
        metadataConfiguration=None,
    ):
        if indexName in self.indexes:
            raise ConflictException(indexName)
        self.indexes[indexName] = {}

    def put_vectors(self, *, vectorBucketName, indexName, vectors):
        assert len(vectors) <= 500
        idx = self._idx(indexName)
        for v in vectors:
            idx[v["key"]] = {"data": v["data"]["float32"], "metadata": v["metadata"]}

    def query_vectors(
        self,
        *,
        vectorBucketName,
        indexName,
        queryVector,
        topK,
        returnMetadata=False,
        returnDistance=False,
        filter=None,
    ):
        q = queryVector["float32"]
        out = []
        for key, v in self._idx(indexName).items():
            if not _match(v["metadata"], filter):
                continue
            dot = sum(a * b for a, b in zip(q, v["data"], strict=True))
            na = math.sqrt(sum(a * a for a in q)) or 1
            nb = math.sqrt(sum(b * b for b in v["data"])) or 1
            out.append({"key": key, "distance": 1 - dot / (na * nb), "metadata": v["metadata"]})
        out.sort(key=lambda r: r["distance"])
        return {"vectors": out[:topK]}

    def delete_vectors(self, *, vectorBucketName, indexName, keys):
        for k in keys:
            self._idx(indexName).pop(k, None)

    def list_vectors(self, *, vectorBucketName, indexName, returnMetadata=False, nextToken=None):
        return {
            "vectors": [
                {"key": k, "metadata": v["metadata"]} for k, v in self._idx(indexName).items()
            ]
        }

    def list_indexes(self, *, vectorBucketName, prefix="", nextToken=None):
        return {"indexes": [{"indexName": n} for n in self.indexes if n.startswith(prefix)]}

    def delete_index(self, *, vectorBucketName, indexName):
        self.indexes.pop(indexName, None)
