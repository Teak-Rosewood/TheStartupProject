import hashlib
import math
import os
import re

from tsp.vector import Embedded

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
DIMS = 1024
TABLES = (
    "vector_chunks, vector_documents, vector_collections, usage_records, files, jobs, cron_jobs, "
    "audit_log, alembic_version"
)


def embed_text(t: str) -> list[float]:
    """Deterministic bag-of-words vectors: texts that share words are close, so search is testable."""
    v = [0.0] * DIMS
    for w in re.findall(r"[a-z0-9]+", t.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIMS] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeEmbedder:
    def __init__(self):
        self.calls: list[tuple[int, str, str]] = []

    async def __call__(self, texts, model, kind):
        self.calls.append((len(texts), model, kind))
        # a different "model" gives different vectors, so a re-embed is visible
        salt = "" if model == "m1" else model + " "
        return Embedded(
            [embed_text(salt + t) if salt else embed_text(t) for t in texts],
            tokens=sum(len(t) // 4 for t in texts),
        )
