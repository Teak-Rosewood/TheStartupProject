# Changelog
Format: [Keep a Changelog](https://keepachangelog.com). Versioning: [SemVer](https://semver.org).

## Unreleased
### Added
- `tsp.ai`: multi-provider LLM client, router, verified model catalog, OpenAI-compatible server.
- AWS backend stack: `tsp.infra`, `tsp.server`, `tsp.auth`, `tsp.db`, `tsp.cache`, `tsp.nosql`, CI/CD workflows, runbooks (`docs/aws`).
- Legal templates and startup guides (`docs/legal`, `docs/startup`).
- `tsp.distribute`: newsletters, share cards with Devanagari support, structured data, sitemaps, labelled AI illustrations.
- `tsp.cms` K4-B: public corrections log and RSS, review cadence with automatic freezing, `is_refreshable`.
- `tsp.i18n`: message catalogs, language negotiation, hreflang; Hindi-aware full-text search in `tsp.vector`.
- `tsp.vector`: pgvector and S3 Vectors stores, hybrid search, chunking, ingestion jobs; pgvector in the Postgres image.
- `tsp.paywall`: metered and hard paywalls with a signed anonymous meter and shared-network-safe device signal.
- Accounts, admin console, payments (Razorpay/Stripe, GST invoices), jobs, notifications, growth, flags, storage, metering, AI gateway, landing site, web app, Expo starter, and the infra to run them (`docs/modules`).
- `tsp.ai` embeddings: `client.embeddings.create`, providers for OpenAI-compatible APIs, Cohere, Google and AWS Bedrock, dims recorded by `tsp-ai verify`, and `POST /v1/embeddings` on the AI gateway (scope `embed`, meter `ai.embed`).
- `tsp.graph`: Postgres knowledge graph with multilingual entity resolution, claims with provenance, dated links and "who held it on this date" queries, cycle-safe paths, stances, versioned time series and on-demand enrichment (`docs/modules/graph.md`).
- `tsp.pipeline`: durable multi-step AI workflows on `tsp.jobs` (checkpoints, retries, per-run budgets, traces and costs, human steps, kill switch, versioned prompt registry and `tsp-pipeline prompts` CLI), plus `tsp.ai.structured` (validated JSON output with one repair retry).
- `tsp.cms` (K4-A): content items with versions and language variants, review workflow with four-eyes approval, pluggable publish checks, AI-disclosure check, scheduled publishing job, disclosure templates, editor API (`docs/modules/cms.md`).
- `tsp.ai` (K3-A): prompt caching hints and cache usage, documents with citations, `response_schema` structured output, Message Batches client with `ai.batch.collect` job in the sample API, glossary-aware `translate`; system messages now go to Anthropic's `system` field.
- Vector search design and costs on AWS (`docs/aws/17-vector-search.md`), roadmap with phase 8 and 8b (`docs/roadmap.md`), web ingestion plan (`docs/aws/18-web-ingestion.md`), and the October 2026 project review (`docs/review-2026-10.md`).
