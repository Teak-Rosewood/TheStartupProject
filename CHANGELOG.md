# Changelog
Format: [Keep a Changelog](https://keepachangelog.com). Versioning: [SemVer](https://semver.org).

## Unreleased

## 0.2.0 — 2026-11-15
First tagged release. From here on, every release is a tag `vX.Y.Z` with one version for all packages ([ADR 0002](docs/adr/0002-versioning-and-releases.md)).

### Breaking
- `tsp.trends` scores each region separately: candidates have a `region` (migration `trends_0002`; the unique key is now day, label and region), so a topic trending in two countries is two candidates. Run the migration before deploying.
- `tsp.ai` Anthropic responses use OpenAI `finish_reason` values (`stop`, `length`, `tool_calls`, `content_filter`) instead of Anthropic's `end_turn`, `max_tokens`, `tool_use`.
- `DeltaMessage.tool_calls` is now a list of `DeltaToolCall` (streamed pieces with `index`), not `ChatToolCall`.

### Fixed
- Tool calling works with Claude: the Anthropic adapter translates tools, `tool_choice`, tool calls and tool results in both directions, streaming included, so `tsp.crawl.run_with_tools` runs on Anthropic.
- Streamed AI gateway calls are charged from the provider's reported usage (Anthropic; OpenAI with `stream_options.include_usage`) instead of an estimate.
- The API image installs every module the sample API imports (`tsp-vector`, `tsp-cms`, `tsp-pipeline`, `tsp-engage`, `tsp-paywall` were missing, so the container could not start). CI now imports the app with only its declared dependencies, and the image check fails when the container exits or never serves `/api/health`.
- Dependencies with known vulnerabilities upgraded (pypdf, twisted, virtualenv, setuptools, pillow 13, soupsieve, certifi, filelock, dnspython, brotli, nltk, opentelemetry-api); the nltk audit exception is gone.
- `deploy`, `drift` and `backup-drill` skip until an AWS account is connected instead of failing.

### Changed
- One repository version in `VERSION`, checked by `scripts/version.py`; `release` workflow on tags.
- GitHub Actions moved off Node 20 (checkout, setup-node, setup-uv v7; configure-aws-credentials v6; CodeQL v4). Dependabot groups minor and patch updates.
- Nightly `live` workflow: smoke tests in `python/live` against real pages (Trafilatura), Crawl4AI, Docling, SearXNG, GDELT, trending-searches feeds and the AI providers whose keys are set; builds the render worker image and renders a page in it. Failures open one issue.
- `docs/status.md` replaces the October handoff as the single status record; the README lists every package and `docs/check_modules.py` checks it.

### Added
- `tsp.crawl` (8b): safe website fetching, sources and pages, plan/fetch/refresh jobs, API, optional browser render worker with its own job queue (`tsp.jobs` queue column), CDK `CrawlConfig`.
- `tsp.ai`: multi-provider LLM client, router, verified model catalog, OpenAI-compatible server.
- AWS backend stack: `tsp.infra`, `tsp.server`, `tsp.auth`, `tsp.db`, `tsp.cache`, `tsp.nosql`, CI/CD workflows, runbooks (`docs/aws`).
- Legal templates and startup guides (`docs/legal`, `docs/startup`).
- `tsp.distribute`: newsletters, share cards with Devanagari support, structured data, sitemaps, labelled AI illustrations.
- `tsp.engage`: polls, read receipts, streaks, points and badges, onboarding, moderated takes with appeals.
- `tsp.cms` K4-C: complaints desk with deadlines and escalation, restricted-mode windows, admin views; the CMS is wired into the sample API.
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
- `tsp.pipeline` (K3-C): eval harness (`tsp-pipeline eval`): suites of cases, exact / Python / LLM-judge scoring, comparison with the active prompt version, stored in `pl_eval_runs`.
- `tsp.crawl` (8b-D): live tools for tool calling (`web_search` via SearXNG, `fetch_url`, `run_with_tools`; results quoted as data, metered `web.search` and `crawl.page`), extraction quality set with 10 saved pages and a scorer, the `crawl.render_scale` job that starts the render worker with `ecs:RunTask`, render alarms (`TSP/Crawl` metrics), `max_render_tasks` and `render_queue_alarm_minutes` in `CrawlConfig`. `tsp.ai`: `tool_calls` and `finish_reason` are no longer dropped by `LLMClient`. Staff console views of sources and trends keep their intended identities.
- Vector search design and costs on AWS (`docs/aws/17-vector-search.md`), roadmap with phase 8 and 8b (`docs/roadmap.md`), web ingestion plan (`docs/aws/18-web-ingestion.md`), and the October 2026 project review (`docs/review-2026-10.md`).
