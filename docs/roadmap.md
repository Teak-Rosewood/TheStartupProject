# Roadmap

Phases 1–7 are reconstructed from the phase references across the docs and are assumed complete for planning; phase 7 is partly built (flags, uploads, metering, AI gateway and the web app are in the code; the mobile starter and its docs are not yet). Phase 8 onward is new. The reasoning behind phases 9–12 is in the [October 2026 project review](review-2026-10.md).

| Phase | Scope | Status |
|---|---|---|
| 0 | `tsp.ai`, AWS stack (`tsp.infra`, `tsp.server`, `tsp.auth`, `tsp.db`, `tsp.nosql`, `tsp.cache`) | done, awaiting first live deploy |
| 1 | Repo hygiene, legal templates, startup guides | done |
| 2 | Consent records, data export and deletion, audit log, configurable log retention | done |
| 3 | Users, organizations, roles, invites, API keys, admin console | done |
| 4 | Payments: Razorpay, Stripe, GST invoices, credits | done |
| 5 | Background jobs, email, push, SMS, in-app | done |
| 6 | Landing page, SEO, waitlist, events, referrals, metrics | done |
| 7 | Feature flags, uploads, AI usage metering and gateway, web and mobile starters | in progress |
| **8** | **Vector search and RAG** | planned (below) |
| **8b** | **Web ingestion: scraping into vector search, live web tools** | planned (below) |
| K1–K8 | Content and data platform modules: source connectors, knowledge graph, AI workflows, editorial CMS, paywall, engagement, trend signals, languages and distribution | planned (after 8b; see below) |
| 9 | Launch hardening: first live deploy, dev environment (the review's other blockers are fixed before phase 8) | planned |
| 10 | Observability and quality: tracing, error tracking, LLM evals, e2e and load tests | planned |
| 11 | Platform for customers: public API docs and SDK, outbound webhooks, search, status page | planned |
| 12 | Enterprise readiness: SSO/SAML, RLS everywhere, SOC 2 / ISO evidence, DR | planned |

## Phase 8: Vector search

Goal: any app built on the kit can index an organization's documents and answer questions over them with citations, isolated per organization, billed in credits, deletable on request, and costing $0 extra at stage 0. Store choice and costs: [docs/aws/17-vector-search.md](aws/17-vector-search.md).

Prerequisite: phase 7 done (uploads in `tsp.storage`, credits in `tsp.metering`, the AI gateway and flags).

### 8.1 Embeddings in `tsp.ai`
- Implement `embedding()` for the OpenAI-compatible, Cohere and Google providers (the base class already declares it), and add a Bedrock provider for Titan and Cohere-on-Bedrock.
- `client.embeddings.create(model=..., input=[...])`, batched, returning vectors plus token usage. `model` is required: no `auto` routing for embeddings; fallback only across keys or providers serving the same model.
- `tsp-ai verify` probes embedding models too and records their dimensions in the catalog.
- Gateway: `POST /v1/embeddings` on the AI gateway with API-key scope `embed`, metered as `ai.embed`.
- Done when: unit tests with recorded payloads per provider; a dimension mismatch is an error, not silent truncation.

### 8.2 pgvector in every Postgres we run
- `docker/postgres/Dockerfile` installs `postgresql-16-pgvector`; `docker-compose.yml` and `.github/workflows/python.yml` use `pgvector/pgvector:pg16`.
- A migration runs `CREATE EXTENSION vector` (allowed on RDS and Aurora).
- `docker/postgres/test_backup.sh` restores a table with a vector column to prove backups cover it.
- Done when: CI is green with the extension and the backup drill passes.

### 8.3 New module `tsp.vector`
- `VectorStore` protocol: `upsert(org_id, items)`, `query(org_id, vector, k, filter)`, `delete(org_id, ids)`, `delete_document`, `delete_org`. `org_id` is a required argument everywhere.
- Backends: `PgVectorStore` (default, uses `tsp.db`), `S3VectorStore` (one index per organization), and a stub for OpenSearch Serverless. All pass one shared contract test suite.
- Schema: `vector_documents` (org, source file, checksum, status, metadata) and `vector_chunks` (content, `halfvec(dims)`, model, generated `tsvector`), HNSW and GIN indexes, row-level security on `org_id`.
- Hybrid search: vector top-k and full-text top-k merged by reciprocal-rank fusion; optional re-rank through `tsp.ai`.
- Done when: the contract suite passes on pgvector in CI and on S3 Vectors behind an opt-in integration flag.

### 8.4 Ingestion pipeline on `tsp.jobs`
- Upload (existing `tsp.storage` presigned POST) → job `vector.ingest`: extract text (PDF, DOCX, HTML, Markdown; OCR optional and metered) → chunk (~400 tokens, overlap) → embed in batches → upsert, all idempotent by document checksum.
- Job `vector.reembed` for a model change, run per organization in the background; old and new vectors coexist, filtered by `model`, until the switch.
- Plan limits in `plans.toml`: `vector_docs`, `vector_chunks`, and a kill switch flag `vector`.
- Done when: re-running the same upload does no work; a failed embed retries without duplicate chunks.

### 8.5 Retrieval and RAG API
- `POST /api/vector/search` (signed-in users, org role) and `POST /v1/retrieve` (API keys, scope `retrieve`).
- `POST /v1/chat/completions` accepts an optional `retrieval: {collection, k}`; the gateway retrieves, inserts sources as quoted data, and returns citations.
- Metering: `vector.search` per query and `ai.embed` per token; both draw credits like `ai.chat`.
- Web app: a documents page (upload, status, delete) and a search box with cited answers.

### 8.6 Compliance and safety
- Register exporter and eraser with `tsp.compliance`: export lists documents; deletion of a user or organization removes documents, chunks and per-org S3 Vectors indexes.
- Audit log entries for bulk deletes and re-embeds.
- Prompt-injection guard: retrieved text is delimited and never treated as instructions; the gateway's `moderate` hook can inspect it.
- Update [sub-processors](legal/subprocessors.md), the [privacy policy](legal/privacy-policy.md) and the [retention schedule](legal/data-retention.md) for embeddings and the embedding provider.

### 8.7 Infrastructure
- `VectorConfig` in `EnvConfig`: `mode: "pgvector" | "s3vectors" | "opensearch"`, `dims`, `embed_model`, `max_chunks_per_org`.
- `s3vectors`: a vector bucket in `ap-south-1` and an IAM policy scoped to it for the API task role; `opensearch`: a collection with encryption, network and data-access policies. Both behind the flag, both synthesized in CI with cdk-nag.
- Alarms: ingest queue age, vector query p95, database memory; a cost-allocation tag on vector resources.

### 8.8 Evaluation
- A small labelled set (questions → expected source chunks) per sample corpus; nightly job reports recall@5 and MRR, and fails on a drop.
- A "golden answers" check for RAG through `tsp.ai`, run on free models by default.

### 8.9 Docs and release
- `docs/modules/vector.md`, CHANGELOG, [gaps](startup/gaps.md) update, and a runbook entry: rebuild an index, re-embed an organization, move a collection from pgvector to S3 Vectors.

Exit criteria for phase 8: a new organization can upload a PDF, ask a question, get an answer with citations within its credit balance, and delete everything with one request; cost at idle is unchanged.

## Phase 8b: Web ingestion

Goal: an organization can add a website (seed URL or sitemap); the kit crawls it politely, keeps it fresh, and makes it searchable and citable through phase 8, safely, within plan limits, and at about $0 extra for small crawls. Tool choice, runtime and costs: [docs/aws/18-web-ingestion.md](aws/18-web-ingestion.md).

Prerequisite: phase 8.1–8.4 (embeddings, pgvector, `tsp.vector`, ingestion jobs).

### 8b.1 Tool spike
- Run Crawl4AI, Trafilatura-over-HTTP and Scrapy against 5 representative sites (static docs, a JavaScript app, a blog, a PDF-heavy site, a large catalog). Compare text quality, pages per vCPU-second and memory use.
- Decide the default chain (expected: HTTP + Trafilatura, then Crawl4AI for JavaScript pages, Docling for PDFs) and record it as an ADR.

### 8b.2 New module `tsp.crawl`
- `Fetcher` protocol with backends `HttpFetcher` (httpx + Trafilatura), `BrowserFetcher` (Crawl4AI) and an optional `ScrapyFetcher` for large crawls of one site.
- `safe_url()` SSRF guard shared by every backend: scheme allow-list, DNS resolution with private, loopback, link-local and metadata ranges refused, checked again after each redirect; size, time and redirect caps.
- `robots.txt` and sitemap parsing, URL normalisation and canonical URLs, include and exclude patterns, max depth.
- Output: `Page(url, final_url, title, markdown, links, content_hash, etag, last_modified, fetched_at)`.
- Done when: unit tests cover the SSRF cases (redirect to `169.254.169.254`, DNS rebinding, IPv6 loopback) and robots rules.

### 8b.3 Crawl jobs
- Tables `crawl_sources` (org, seed, rules, schedule, status) and `crawl_pages` (url, hash, etag, last fetched, status, error).
- Jobs `crawl.plan` (expand a source into URLs), `crawl.fetch` (one URL), `crawl.refresh` (scheduled per source); the frontier lives in Postgres through `tsp.jobs`, idempotent per URL and source.
- Politeness: per-domain token bucket in `tsp.cache`, back-off on 429 and 503, conditional GET on refresh.
- Changed pages (new content hash) enqueue `vector.ingest`; removed pages delete their chunks.

### 8b.4 Browser render worker
- Container image with Chromium + Crawl4AI, run as a Fargate Spot task (Graviton) started by a job when render work is queued and stopped when idle.
- Public subnet, no inbound rules, IAM limited to `crawl/*` in S3 and its queue; no database or secrets access.
- Done when: a JavaScript-only test site is rendered and ingested end to end, and the task stops itself when the queue is empty.

### 8b.5 API and web app
- `POST /api/crawl/sources`, `GET` status and page list, `DELETE` a source (which deletes its pages and vectors), and a "recrawl now" action.
- Web app page to add a site, watch progress, and see failed URLs with reasons.
- Search results and RAG answers show the source URL and fetch date.

### 8b.6 Live web tools (after the indexed crawl works)
- Tools for `tsp.ai` tool calling: `web_search` (self-hosted SearXNG by default, a paid search API as an option) and `fetch_url` (through `tsp.crawl` with the same SSRF guard).
- Short cache of fetched pages in S3 or Valkey; metered as `crawl.page` and `web.search`.
- The gateway quotes tool results as data and refuses actions that come only from web content.

### 8b.7 Compliance and limits
- Plan limits in `plans.toml`: `crawl_pages_month`, `crawl_depth`, `crawl_refresh_min_hours`; kill switch flag `crawl`.
- Updates to the acceptable use policy (customers confirm their right to crawl), sub-processors (any hosted crawl or search API), privacy policy and retention schedule (how long crawled pages are kept).
- Deletion and export through `tsp.compliance`; audit log entries for source creation and deletion.

### 8b.8 Infrastructure and operations
- `CrawlConfig` in `EnvConfig`: `enabled`, `render: "none" | "fargate"`, `max_render_tasks`, `bucket_prefix`, `search: "none" | "searxng" | "api"`.
- Alarms: crawl queue age, fetch error rate per domain, render task failures, monthly pages per org against plan.
- Runbook: pause a source, block a domain globally, handle a takedown request.

### 8b.9 Evaluation and docs
- Extraction quality check: for a fixed set of pages, compare extracted Markdown against hand-made references (boilerplate removed, tables kept).
- `docs/modules/crawl.md`, CHANGELOG, gaps update.

Exit criteria for phase 8b: an organization adds its help-center URL, the site is crawled within its plan limits while respecting robots.txt, questions are answered with cited page links, a recrawl only re-embeds changed pages, deleting the source removes everything, and a submitted URL pointing at an internal address is refused.

## Phases K1–K8: content and data platform

Generic modules built after 8b, each with neutral example data and a page under `docs/modules/`. Paste-ready implementation briefs for K0, 8, 8b and K1–K8 are in [plans/](plans/README.md). Order: K1 and K2 together, then K3, then K4 and K7, then K5, K6 and K8.

| Phase | Module | Scope |
|---|---|---|
| K1 | `tsp.sources` | source registry (licence, terms, schedule, health) and connectors that try open data, bulk files, scraping, free APIs and paid APIs in a configured order, with fallback and resumable cursors |
| K2 | `tsp.graph` | entities with multilingual aliases, claims with provenance, typed links with validity dates, stances, time series; "valid at date" and short-path queries on Postgres |
| K3 | `tsp.pipeline` + `tsp.ai` | durable multi-step AI workflows with checkpoints, budgets, traces and costs; versioned prompts; eval harness; prompt caching, batches, citations, structured outputs, glossary-aware translation |
| K4 | `tsp.cms` | content items with versions and language variants, publish checks, scheduled publish, public corrections log, living-or-frozen review cadence, complaints desk with deadlines, restricted-mode windows |
| K5 | `tsp.paywall` | meter policies, signed anonymous meter, shared-network-aware device signal, hard and soft walls, crawler verification, gift links, pay-per-item credits |
| K6 | `tsp.engage` | before-and-after position polls, read receipts, streaks, points, onboarding preconditions, moderated submissions with no replies, appeals |
| K7 | `tsp.trends` | plug-in interest signals, scoring and a daily candidate list |
| K8 | `tsp.i18n` + `tsp.distribute` | message catalogs, language URLs, Hindi-aware search, newsletters, share cards, SEO structured data, labelled AI illustrations |

## Phases 9–12

Each line links to the finding it addresses in the [project review](review-2026-10.md).

- **9 Launch hardening**: the first live deploy with verified costs and the dev environment are the phase-9 work. The other blockers are cheap and unblock everything else, so they are done first, before phase 8: deploy and security workflows on the right branch ([B1](review-2026-10.md#b1-deploy-and-security-workflows-never-run)); missing phase-7 docs ([B2](review-2026-10.md#b2-docs-the-code-points-to-do-not-exist)); stale README and gaps ([B3](review-2026-10.md#b3-status-pages-disagree-with-the-code)); first live deploy with verified costs; dev environment ([docs/aws/11](aws/11-multi-env.md)); 180-day log retention for production.
- **10 Observability and quality**: OpenTelemetry tracing, error tracking, LLM cost and latency dashboards per organization, end-to-end browser tests, load tests, generated TypeScript API client.
- **11 Platform for customers**: public API reference and SDK, outbound webhooks, full-text search (shared with phase 8), status page, in-app changelog, internationalisation.
- **12 Enterprise readiness**: SAML/OIDC SSO per organization, Postgres RLS on every tenant table, SOC 2 / ISO 27001 evidence collection, cross-region backup copies and a tested disaster-recovery runbook, e-invoicing (IRN) above the GST threshold.
