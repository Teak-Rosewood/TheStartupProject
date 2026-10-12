# 17 · Vector search

Semantic search, retrieval-augmented generation (RAG), recommendations and deduplication all need the same thing: store an embedding (a list of numbers) for each piece of content and find the nearest ones fast, **only inside the customer's own organization**. This page compares the ways to do that on AWS, estimates what each costs at four sizes, and picks a default that fits the rest of this stack: close to $0 at idle, no cold starts, and a config switch instead of a rewrite when you outgrow it.

The build plan is [phase 8 in the roadmap](../roadmap.md#phase-8-vector-search). Page 16 is reserved for the AI gateway.

> Every price here is a planning estimate from published list prices (mostly us-east-1; ap-south-1 is usually within about 10%) and is marked **(verify)** where it moves often. Check the [AWS Pricing Calculator](https://calculator.aws/) before you commit.

## Decision

| Stage | Store | Move when |
|---|---|---|
| V0 | **pgvector in the existing Postgres** (host container at stage 0, RDS or Aurora later, following `database.mode`) | — |
| V1 | same, with quantized indexes (`halfvec`, binary) and a larger instance | the HNSW index no longer fits in about half of the database's memory, or p95 search latency passes ~100 ms |
| V2 | **S3 Vectors** for large or cold corpora, one index per organization; pgvector keeps small, hot or transactional sets | more than ~5–10M vectors, or vectors cost more in RAM than the rest of the database |
| V2 alt | **OpenSearch Serverless** vector collection | you need high-QPS, low-latency hybrid (keyword + vector) search over tens of millions of documents and the ~$350/month floor is small next to revenue |

Why pgvector first:
- **$0 extra.** It is an extension in the database you already run, backed up by the same pgBackRest WAL archive ([05 SQL](05-sql.md)).
- **One transaction.** A document, its chunks and its vectors are written and deleted together. Data export and deletion (`tsp.compliance`) cover vectors with no second system to forget.
- **Tenant filtering is a `WHERE org_id = ...`**, and Postgres row-level security can enforce it. Leaking another customer's chunks into a RAG prompt is the worst failure this feature can have.
- **Hybrid search in one query**: Postgres full-text (`tsvector`) and vector similarity, merged with reciprocal-rank fusion.
- **Moves with the database**: the host container, RDS and Aurora all support pgvector (check the extension version on your engine version **(verify)**).

Why not the others first: OpenSearch Serverless has a floor of about $175/month (dev, no redundancy) or about $350/month (production) even when idle. Aurora Serverless v2 can scale to zero but resumes in about 15 s, which is visible on a search box. S3 Vectors is very cheap but answers in roughly 100 ms or more, has no joins or transactions, and bills each query by the size of the index it scans, so it needs per-tenant indexes to stay cheap (see costs). Bedrock Knowledge Bases is a managed pipeline on top of one of these stores, not a store of its own (below).

## Options compared

| | pgvector (host / RDS) | Aurora PostgreSQL + pgvector | S3 Vectors | OpenSearch Serverless (vector) | Bedrock Knowledge Bases | Self-hosted Qdrant / Weaviate / Milvus |
|---|---|---|---|---|---|---|
| What it is | Postgres extension | same, on Aurora storage | vector buckets and indexes inside S3, with a query API | managed OpenSearch, vector collection type | managed ingest (S3 → parse → chunk → embed) + retrieve API over a store you choose | open-source vector database in a container on EC2/ECS |
| Idle cost | $0 extra | $0 (Serverless v2 at 0 ACU) to ~$45 | ~$0 (storage only) | ~$175 dev / ~$350 prod **(verify)** | the store's idle cost | the instance |
| Query latency | ~1–20 ms in memory | same; +~15 s after scale-to-zero | ~100 ms–1 s **(verify)** | ~10–50 ms | the store's + API overhead | ~1–20 ms |
| Scale comfort | to ~5–10M vectors per instance | tens of millions with large instances | billions per bucket (very large per-index limits) | hundreds of millions | the store's | hundreds of millions, you operate it |
| Tenant isolation | `org_id` filter + RLS | same | one index per org (strong), or metadata filter | filter or index per tenant | metadata filter | collection or payload filter |
| Hybrid keyword + vector | yes (full-text in the same query) | yes | no (vector + metadata filter only) | yes, built in | yes (with OpenSearch backing) | Weaviate yes; Qdrant via sparse vectors |
| Transactions with app data | yes | yes | no | no | no | no |
| Backups | already covered (pgBackRest, snapshots) | Aurora backups | S3 durability; re-embed is the rebuild path | snapshots | the store's | yours to build |
| Fits this stack | best | stage 4 databases | best second tier | when search is the product | only if you accept Bedrock-only embeddings | only at large scale with ops time |

Others you may meet: **ElastiCache for Valkey vector search** and **MemoryDB** keep vectors in RAM at in-memory prices (good for a small semantic cache in front of the LLM, not for a corpus); **DocumentDB** has vector search if you already run it; **Pinecone, Turbopuffer, Zilliz** are external SaaS (add them to [sub-processors](../legal/subprocessors.md) and check where data is stored before using them for Indian personal data).

### Bedrock Knowledge Bases, in short
It ingests from S3 (and some SaaS sources), parses, chunks, embeds with a Bedrock model, writes to a store (OpenSearch Serverless, Aurora pgvector, S3 Vectors and others), and gives you `Retrieve` and `RetrieveAndGenerate` APIs. You pay for the store, the embedding calls, any advanced parsing (per page) and the generation model; the knowledge base itself has no separate charge **(verify)**. It saves building the ingestion job, but embeddings and generation must be Bedrock models, while `tsp.ai` is deliberately multi-provider, and per-tenant isolation becomes a metadata filter you must never forget. Use it for an internal knowledge assistant, not as the customer-facing default.

## Sizing assumptions

- Embedding: **1024 dimensions** (Titan Text Embeddings V2, Cohere Embed, and many open models can produce 1024). Some models allow 256 or 512 for 2–4x less storage at a small quality cost.
- A vector of 1024 `float32` is 4 KB; as pgvector `halfvec` 2 KB; as a binary (bit) index 128 bytes. With metadata, count ~4.5 KB per vector in S3 Vectors or OpenSearch.
- One chunk is ~400 tokens of text. A query embeds ~30 tokens.
- An HNSW index needs memory of roughly the vector data plus 20–50% to stay fast.

| Tier | Vectors (chunks) | Text indexed | Queries / month | Example |
|---|---|---|---|---|
| T1 Prototype | 50,000 | ~20M tokens (~1,500 long PDFs) | 100,000 | first customers, one RAG feature |
| T2 Early | 1M | ~400M tokens | 1M | a few hundred paying organizations |
| T3 Growth | 10M | ~4B tokens | 10M | thousands of organizations or one large corpus |
| T4 Scale | 100M | ~40B tokens | 30M | consider dedicated search engineering |

## Estimated monthly cost by tier

Store only. Embedding costs are listed separately below; they are small.

| Option | T1 (50k) | T2 (1M) | T3 (10M) | T4 (100M) |
|---|---|---|---|---|
| **pgvector on the stage-0 host** | **~$0** (+<1 GB EBS) | $12–40: host to `t4g.medium`/`t4g.large`, or stay smaller with a binary-quantized index and re-ranking | not recommended: move to RDS first | — |
| **pgvector on RDS** (single-AZ; Multi-AZ ~2x) | $0 extra on the stage-1 `db.t4g.micro` | $50–100 (`db.t4g.medium`–`large`, 30 GB gp3) | $360–720 (`db.r7g.xlarge`–`2xlarge`, `halfvec`, 100 GB) | $2,500+ and partitioning work; move to V2 |
| **Aurora Serverless v2 + pgvector** | $0–45 (scale-to-zero has ~15 s resume) | $175–350 (2–4 ACU average) | $1,400–2,800 (16–32 ACU) | not cost-effective |
| **S3 Vectors, one shared index** | **<$1** | ~$13 | ~$500–950 | **$5,000+**: every query is billed by the whole index size |
| **S3 Vectors, one index per organization** (~100k vectors each) | <$1 | ~$5–10 | ~$40–60 | ~$150–250 |
| **OpenSearch Serverless** | ~$175 (dev) / ~$350 (prod) | ~$350 | $1,500–2,500 | $3,000–6,000 (quantized / disk modes) |
| **Bedrock Knowledge Bases** | backing store + parsing | backing store + parsing | backing store + parsing | backing store + parsing |
| **Self-hosted Qdrant on EC2** (one node, quantization) | $0 on the host (RAM permitting) | $50–60 (`r7g.medium`/`t4g.large` + EBS) | $150–250 (`r7g.xlarge`) | $600–1,500 (cluster) + your time |

How the S3 Vectors numbers are built **(verify all rates)**: storage ~$0.06 per GB-month; writes ~$0.20 per GB uploaded; ~$2.50 per million query requests; plus a query-processing charge per TB of index scanned, tiered so larger indexes pay less per TB (with a further reduction above 10M vectors announced in 2026). Each query is billed for the size of the index it searches. A 1M-vector index is ~4.5 GB, so 1M queries cost about $10 in processing; a 100M-vector index is ~450 GB per query, which is why one big shared index gets expensive and one index per organization stays cheap. Index-per-org also gives the strongest tenant isolation.

### Embedding cost (all options)
| | T1 | T2 | T3 | T4 |
|---|---|---|---|---|
| Initial indexing, Titan Text Embeddings V2 at ~$0.02 per 1M tokens **(verify)** | ~$0.40 once | ~$8 once | ~$80 once | ~$800 once |
| Same with Cohere Embed v4 at ~$0.12 per 1M tokens **(verify)** | ~$2.40 once | ~$48 once | ~$480 once | ~$4,800 once |
| Query embeddings per month (~30 tokens each, Titan) | <$0.01 | ~$0.60 | ~$6 | ~$18 |
| Free-tier or self-hosted models through `tsp.ai` (Gemini, Mistral, Ollama, ...) | $0 within limits | rate limits bite | — | — |

Two costs people forget: **re-embedding** (changing the embedding model means embedding the whole corpus again, so pick it deliberately and record it per vector), and **document parsing** (PDF and image OCR, e.g. Textract or Bedrock Data Automation, is priced per page and can cost more than the embeddings).

## Design rules for this stack

1. **Pin the embedding model.** Vectors from different models cannot be compared. The `auto` router in `tsp.ai` must never pick an embedding model; fallback may only switch API keys or providers that serve the *same* model. Store `model` and `dims` with every vector and filter on it.
2. **Every query carries `org_id`.** Enforce it in the store API (the parameter is required, not optional) and with row-level security in Postgres, or with one S3 Vectors index per organization.
3. **Vectors are personal data when the text is.** Register vectors with `tsp.compliance` so export includes the source documents and deletion removes chunks and vectors in the same request. List the embedding provider in [sub-processors](../legal/subprocessors.md).
4. **Keep data in `ap-south-1`.** pgvector, S3 Vectors and OpenSearch Serverless are available in Mumbai; check Bedrock model availability per region **(verify)**, or call the model through `tsp.ai` with a provider whose data terms you accept.
5. **Retrieved text is untrusted input.** A customer document can contain "ignore previous instructions". Quote it as data in prompts and keep tool permissions narrow when RAG answers can trigger actions.
6. **Bill for it.** Embedding and search use credits through `tsp.metering` (kinds `ai.embed`, `vector.search`), and plans cap stored chunks per organization.

## pgvector at stage 0, concretely

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE vector_chunks (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL,
  document_id uuid NOT NULL REFERENCES vector_documents(id) ON DELETE CASCADE,
  chunk_index int  NOT NULL,
  content     text NOT NULL,
  model       text NOT NULL,           -- e.g. 'amazon.titan-embed-text-v2:0'
  embedding   halfvec(1024) NOT NULL,
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX ON vector_chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX ON vector_chunks (org_id, document_id);
CREATE INDEX ON vector_chunks USING gin (tsv);
ALTER TABLE vector_chunks ENABLE ROW LEVEL SECURITY;
```

- Query with `ORDER BY embedding <=> $1 LIMIT k` and `WHERE org_id = $2 AND model = $3`. With selective filters turn on iterative index scans (`SET hnsw.iterative_scan = relaxed_order`, pgvector 0.8+) so a filter does not return fewer than `k` rows.
- Memory on the 2 GB `t4g.small` is shared with the API, Postgres buffers and Valkey. Up to ~100k–200k `halfvec(1024)` vectors is comfortable; beyond that, raise `compute.instance_type` or move to stage 1 RDS as the [scaling playbook](10-scaling-playbook.md) already describes.
- Build large indexes in a job with a raised `maintenance_work_mem`, and watch the database memory alarm while it runs.
- The image needs the extension: install `postgresql-16-pgvector` in `docker/postgres/Dockerfile`, and use `pgvector/pgvector:pg16` in `docker-compose.yml` and CI.

## Signals to move up

| Signal | Action |
|---|---|
| Database memory alarm or swap after vectors grew | larger host or RDS class; `halfvec` or binary index |
| p95 vector query > 100 ms at normal load | check iterative scan and `ef_search`; then larger instance |
| > 5–10M vectors, or index > ~50% of DB RAM | move large or cold collections to S3 Vectors (per-org indexes) |
| Search is the product: high QPS, faceting, hybrid ranking tuning | OpenSearch Serverless |
| Vector store > 20% of the AWS bill | revisit dimensions, quantization and which documents are indexed at all |
