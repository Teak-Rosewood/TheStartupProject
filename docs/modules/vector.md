# Vector search (`tsp.vector`)

Search an organization's documents by meaning and by keyword, with the building blocks for answering questions over them (RAG). Package: `python/packages/vector`. Choice of store and costs: [17 Vector search](../aws/17-vector-search.md).

## What you get
- **Stores:** `PgVectorStore` (default; pgvector inside your Postgres, so $0 extra and one backup) and `S3VectorStore` (Amazon S3 Vectors, one index per organization and collection, for large or cold corpora). Both pass one shared test suite. The S3 backend is tested only with an in-memory stand-in; it has not been run against the real service.
- **Hybrid search:** vector top results plus Postgres full-text top results, merged with reciprocal-rank fusion (RRF, k=60). An optional re-ranker callable can reorder the merged list.
- **Ingestion:** add text or an uploaded file (`tsp.storage`); a `vector.ingest` job extracts text (plain text, Markdown, HTML; PDF and DOCX need the `parse` extra), splits it into overlapping chunks that respect headings and paragraphs, embeds them in batches and stores them. Adding the same content twice is one document, and a retried job never duplicates chunks.
- **Isolation:** every store method takes `org_id`; queries always filter on it. In Postgres, row-level security on the chunk table adds a second lock, but it binds only a database role that is neither superuser nor `BYPASSRLS`; run the app as such a role in production. The default container user is a superuser, so there RLS does nothing and the `org_id` filter is the only lock. Tests prove both.
- **Money and limits:** every indexing and search call writes `usage_records` rows (`ai.embed` with token counts, `vector.search`). A `limits` callback can cap documents and passages per plan (`vector_docs`, `vector_chunks`); the HTTP routes accept a kill switch callback (use flag `kill:vector`).
- **Compliance:** documents a user added are exported with their data and deleted (vectors included) when they delete their account. `VectorService.delete_org` removes everything an organization owns in every backend.
- **Changing the embedding model:** `request_reembed` queues a job that writes new vectors beside the old ones, switches the collection when all documents are done, then deletes the old vectors. Searches use the old model until the switch.

## Wire it up
```python
from tsp.vector import PgVectorStore, VectorConfig, VectorService, ai_embedder, build_router, register_jobs, register_with_compliance

service = VectorService(
    store=PgVectorStore(db),
    embedder=ai_embedder(llm_client),          # tsp.ai: client.embeddings.create(...)
    db=db,
    config=VectorConfig(dims=1024, embed_model="text-embedding-3-small"),
    reader=uploads.store.get,                  # lets the job read uploaded files
)
register_jobs(registry, service)               # vector.ingest, vector.reembed
register_with_compliance(compliance, service)
app.include_router(build_router(service, account, org_member, db_session), prefix="/api")
```
Migrate with `modules=[..., "tsp.vector"]` (creates the `vector` extension; the database image must include pgvector, see `docker/postgres`).

## Routes (under `/api/orgs/{org_id}/vector`)
| Route | Role | What |
|---|---|---|
| `POST /collections`, `GET /collections` | admin / member | create and list collections |
| `POST /documents` | member | body `{collection, text}` or `{collection, file_id}`; returns 202 and the status |
| `GET /documents[?collection=]`, `DELETE /documents/{id}` | member / admin | list with status and error; delete with vectors |
| `POST /search` | member | `{collection, query, k, filter}` returns passages with title, source and score |

The brief in `docs/plans/08-vector.md` listed routes without the organization in the path; these follow the other modules (`/orgs/{org_id}/...`) so the existing role checks apply.

## Limits and choices
- **One embedding size per database.** The column is `halfvec(VECTOR_DIMS)` (default 1024), fixed when the first migration runs. A model that returns another size is refused with an error, never truncated. Different sizes would need one chunk table per size.
- Ids are text (hex) like the rest of the kit, not UUID columns.
- Row-level security is on the chunk table only. Collections and documents hold no vectors and stay readable across organizations so that erasing a user's account can find their documents.
- Chunk size is estimated as 4 characters per token; sizes are approximate.
- Not built yet: the `/v1/retrieve` gateway route and `retrieval` on chat completions (roadmap 8.5), the ingest-queue and search-latency alarms (the app does not publish those metrics yet), and a scheduled CI job for the evaluation (it needs real provider keys, so it runs on demand).

## In the sample API
`apps/api` turns vector search on when an AI provider key is set (it needs an embedding model). Settings, all optional:

| Variable | Meaning |
|---|---|
| `TSP_AI_EMBEDDING_MODELS` | `provider:model[:dims]`, comma separated, for example `openai:text-embedding-3-small:1536`. Not needed when the verified model catalog lists embedding models |
| `VECTOR_EMBED_MODEL` | model new collections use (default `text-embedding-3-small`) |
| `VECTOR_DIMS` | vector size; fixed per database at the first migration (default 1024) |
| `VECTOR_BACKEND` | `pgvector` (default) or `s3vectors` (needs `VECTOR_S3_BUCKET`) |
| `VECTOR_MAX_CHUNKS_PER_ORG` | cap on stored passages per organization when the plan sets none |

Plan features `vector_docs` and `vector_chunks` in `plans.toml` cap documents and passages per plan. The flag `kill:vector` switches the routes off. In AWS set these through `EnvConfig.vector` (`VectorConfig`): `mode="s3vectors"` adds a retained vector bucket and gives the API task access to it only.

Many embedding models return fewer dimensions than 1024 (OpenAI `text-embedding-3-small` returns 1536, and can be asked for fewer). Pick one size, set `VECTOR_DIMS` to it before the first migration, and keep to models that return it.

## Command line and quality check
```bash
uv run tsp-vector ingest --org ORG --collection recipes --path docs/
uv run tsp-vector search --org ORG --collection recipes "how long do I sear salmon"
uv run tsp-vector eval --org ORG --collection recipes --questions python/packages/vector/tests/eval/questions.json --save-baseline baseline.json
uv run tsp-vector eval ... --baseline baseline.json --max-drop 5     # exits 1 when recall@5 or MRR falls more than 5 points
```
`tests/eval` holds a small recipe corpus and 20 labelled questions (the label is the document name). The unit test runs them with a crude word-hash embedder; run the command with a real model to judge a model or chunk-size change.

## Runbook
- **Rebuild the index:** `DROP INDEX ix_vector_chunks_embedding; CREATE INDEX ix_vector_chunks_embedding ON vector_chunks USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);` (raise `maintenance_work_mem` first on big tables and watch database memory).
- **Re-embed an organization:** `tsp-vector reembed --org ORG --collection NAME --model NEW`, or request it through `VectorService.request_reembed` (queues the job). Old vectors stay searchable until the switch.
- **Move a collection to S3 Vectors:** not automated. Create the collection with the S3 backend, re-ingest from the sources, then delete the old documents. Chunk text is kept in the store, so `store.chunks()` plus a re-embed also works.
- **Sub-processors:** list your embedding provider(s) and, with S3 Vectors, AWS, in [sub-processors](../legal/subprocessors.md); mention stored passages in the retention schedule (done: see [data retention](../legal/data-retention.md)).
