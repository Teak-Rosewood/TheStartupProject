# Phase 8 Vector search (`tsp.ai` embeddings + `tsp.vector`)

**Goal:** any app on the kit can index an organization's documents and search them (vector + full text) with citations, isolated per organization, metered, deletable, at $0 extra idle cost. Design: [../../vector-search-on-aws.md](../aws/17-vector-search.md); roadmap steps 8.1–8.9 in [../../roadmap-phase-8-vector-db.md](../roadmap.md).

**Depends on:** K0. **Branch:** `feat/8-vector-<step>`. **Size:** large; four pull requests (8-A to 8-D), in order.

## 8-A Embeddings in `tsp.ai`

Files: `python/packages/ai/src/tsp/ai/{models.py,client.py,catalog.py,cli.py,server.py}`, `providers/{openai_compat.py,cohere.py,google.py,bedrock.py (new),registry.py}`, tests.

Interfaces:
```python
# models.py
class EmbeddingItem(BaseModel): index: int; embedding: list[float]
class EmbeddingResponse(BaseModel): model: str; provider: str; dims: int; data: list[EmbeddingItem]; usage: Usage

# client.py
client.embeddings.create(model: str, input: list[str], *, dims: int | None = None,
                         input_type: Literal["document", "query"] = "document",
                         batch_size: int = 96) -> EmbeddingResponse
```
Rules:
- `model` required; no `auto` routing. Fallback only across keys or providers serving the *same* model id.
- Batching inside `create`; results returned in input order.
- `input_type` maps to Cohere `search_document/search_query`, Google `RETRIEVAL_DOCUMENT/RETRIEVAL_QUERY`, ignored where unsupported.
- Catalog records `dims` per embedding model; `tsp-ai verify` probes embedding models with one short input and stores dims.
- If the returned vector length differs from the catalog's `dims` (or the requested `dims`), raise `EmbeddingDimensionError`. Never truncate silently.
- Bedrock provider (new): Titan Text Embeddings v2 and Cohere embed multilingual via `boto3` `bedrock-runtime` `invoke_model`; chat not required.
- Gateway (`server.py` / API app): `POST /v1/embeddings` (OpenAI-compatible shape), API-key scope `embed`, `record_usage(meter="ai.embed", quantity=usage.total_tokens, ...)`, plan rate limits and the AI kill switch apply as for chat.

Tests: recorded payloads per provider (no network); order preserved across batches; dimension mismatch raises; gateway scope enforced; usage recorded.

## 8-B pgvector everywhere

Files: `docker/postgres/Dockerfile` (install `postgresql-16-pgvector`), `docker-compose.yml` and `.github/workflows/python.yml` service image `pgvector/pgvector:pg16`, `docker/postgres/test_backup.sh` (create a table with a `vector(3)` column, back up, restore, compare).

The `CREATE EXTENSION IF NOT EXISTS vector` lives in `vector_0001` (8-C), not here.

Done when CI is green on the new image and the backup drill restores the vector column.

## 8-C `tsp.vector` package

Files: `python/packages/vector/` with `pyproject.toml` (deps: `tsp-db`, `tsp-ai`, `tsp-jobs`, `tsp-storage`, `tsp-metering`, `tsp-compliance`, `tsp-audit`, `pgvector`, optional extra `s3` → `boto3`, optional extra `parse` → `pypdf`, `python-docx`, `trafilatura`), `src/tsp/vector/{__init__,config,models,store,pg,s3,chunker,extract,search,service,jobs,api,compliance}.py`, `migrations/versions/vector_0001_init.py`, `tests/`.

Data model (`vector_0001`):
| Table | Columns |
|---|---|
| `vector_collections` | id uuid pk, org_id, name, embed_model, dims, backend (`pgvector`/`s3vectors`), created_at; unique (org_id, name) |
| `vector_documents` | id uuid pk, org_id, collection_id fk, source_uri, title, checksum (sha256), status (`pending`/`ready`/`failed`/`deleting`), error, metadata jsonb, created_at, updated_at; unique (collection_id, checksum) |
| `vector_chunks` | id uuid pk, org_id, document_id fk cascade, ordinal, content text, token_count, embedding `halfvec(dims)` (dims fixed per migration config, default 1024), embed_model, metadata jsonb, `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED` |

Indexes: HNSW `halfvec_cosine_ops` on `embedding` (m=16, ef_construction=64); GIN on `tsv`; btree on (org_id, document_id). Row-level security on all three tables: `USING (org_id = current_setting('app.org_id')::uuid)`; the service sets `SET LOCAL app.org_id` per transaction.

Note: different dims per collection needs one chunk table per dims value. Start with one global dims (config `VECTOR_DIMS`); document the limitation.

Interfaces:
```python
class VectorItem(TypedDict): id: UUID; document_id: UUID; content: str; embedding: list[float]; metadata: dict
class Hit(TypedDict): id: UUID; document_id: UUID; score: float; content: str; metadata: dict

class VectorStore(Protocol):
    async def upsert(self, org_id: UUID, collection: str, items: list[VectorItem]) -> None
    async def query(self, org_id: UUID, collection: str, vector: list[float], k: int, filter: dict | None = None) -> list[Hit]
    async def delete(self, org_id: UUID, collection: str, ids: list[UUID]) -> None
    async def delete_document(self, org_id: UUID, document_id: UUID) -> None
    async def delete_org(self, org_id: UUID) -> None

PgVectorStore(session_factory)        # default
S3VectorStore(bucket, region)         # index name f"org-{org_id}-{collection}"; metadata filter on document_id
OpenSearchStore                       # stub raising NotImplementedError, documented

chunk(text: str, *, target_tokens=400, overlap=60, headings=True) -> list[Chunk]   # splits on headings/paragraphs first
extract(path_or_bytes, mime) -> str                                                # pdf, docx, html (trafilatura), md, txt
hybrid_search(org_id, collection, query: str, k=10, filter=None, rerank=False) -> list[Hit]  # vector top 4k + FTS top 4k, RRF k=60
VectorService: create_collection, add_document(upload_key|text|url), search, delete_document, reembed(collection, new_model)
```

Jobs: `vector.ingest` (payload: document_id) = extract → chunk → embed in batches → upsert; idempotent by checksum (same checksum and status ready = no-op); retries do not duplicate chunks (delete chunks for document before insert, in one transaction). `vector.reembed` (collection_id, model): writes new chunks with the new `embed_model`, flips `collection.embed_model` when done, then deletes old.

API (`build_router(service, account, session)`): `POST /api/vector/collections`, `POST /api/vector/documents` (from an upload key), `GET /api/vector/documents`, `DELETE /api/vector/documents/{id}`, `POST /api/vector/search`. Gateway: `POST /v1/retrieve` (scope `retrieve`), and `retrieval: {collection, k}` on `/v1/chat/completions` which inserts hits as quoted sources and returns `citations`.

Metering: `vector.search` per query, `ai.embed` tokens (via tsp.ai), `vector.docs` gauge. Plan limits in `plans.toml`: `vector_docs`, `vector_chunks`; kill switch flag `vector`.

Compliance: exporter lists collections and documents; eraser deletes per user-owned documents and on org deletion calls `delete_org` on every backend. Audit entries on bulk delete and reembed.

Contract tests: one parametrised suite run against `PgVectorStore` (CI) and `S3VectorStore` (only when `TSP_TEST_S3VECTORS_BUCKET` is set). Must cover: org isolation (org B never sees org A hits even with a crafted filter), delete_document, delete_org, upsert idempotency, RRF ordering on a small fixture, RLS blocks a raw query without `app.org_id`.

## 8-D Infra, eval, docs

- `VectorConfig` in `EnvConfig`: `mode: Literal["pgvector","s3vectors","opensearch"] = "pgvector"`, `dims=1024`, `embed_model`, `max_chunks_per_org`. `s3vectors` adds a vector bucket (CDK L1 `CfnVectorBucket` if available, else custom resource) and an IAM policy scoped to it on the API task role. cdk-nag passes. Alarms: ingest queue age, search p95.
- Eval: `tests/eval/` fixture corpus (recipes or product docs) with 20 labelled questions → expected chunk ids; `tsp-vector eval` prints recall@5 and MRR; a nightly CI job fails on a drop of >5 points.
- CLI `tsp-vector`: `ingest --collection --path`, `search`, `reembed`, `eval`.
- Docs: `docs/modules/vector.md`, runbook (rebuild index, re-embed org, move collection to S3 Vectors), legal pages (sub-processors, privacy, retention), CHANGELOG.

## Done when (phase exit)
A new organization uploads a PDF, searches it, gets a cited answer within its credits, and deletes everything with one request; idle cost unchanged; contract suite green on pgvector.

## Out of scope
OpenSearch backend implementation; multiple dims per database; re-rank model training.

## Prompt (one per step; replace X with A, B, C or D)

```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/8-vector-X.
Read docs/plans/08-vector.md, section 8-X, and docs/plans/README.md for kit conventions.
Read the existing packages it touches first (tsp.ai providers and client, tsp.jobs, tsp.storage,
tsp.metering, tsp.compliance, the flags package as a layout template).
Implement section 8-X exactly; if a name in the brief clashes with existing code, follow the code
and note it in the PR. Unit tests use recorded fixtures, no network. Database tests use
TSP_TEST_DATABASE_URL. Run lint, type checks and the affected tests before each commit.
Write or update docs as the section says. Open a pull request listing what is done against
the section's checklist.
```
