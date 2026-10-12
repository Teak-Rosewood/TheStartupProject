# tsp-vector

Vector and hybrid (keyword + vector) search for multi-tenant apps, with the pieces RAG needs: text extraction, chunking, an ingestion job, per-organization isolation, usage metering, export and deletion. Guide: [docs/modules/vector.md](../../docs/modules/vector.md). Design and cost: [docs/aws/17-vector-search.md](../../docs/aws/17-vector-search.md).

```python
from tsp.vector import PgVectorStore, VectorConfig, VectorService, ai_embedder, register_jobs

service = VectorService(
    store=PgVectorStore(db),
    embedder=ai_embedder(llm_client),
    db=db,
    config=VectorConfig(dims=1024, embed_model="text-embedding-3-small"),
)
register_jobs(registry, service)  # tsp.jobs handlers: vector.ingest, vector.reembed
app.include_router(build_router(service, account, org_member, db_session), prefix="/api")
```

Migrate with `upgrade(url, "migrations", modules=["tsp.vector", ...])`. The embedding size is fixed per database by `VECTOR_DIMS` (default 1024) when the first migration runs.
