# Handoff, October 2026

State of the kit after the K0–K8 build. Local `master` includes K6 (not pushed). Last full gate: 877 tests pass, ruff, mypy, `docs/check_links.py` and `docs/check_modules.py` are clean.

## Done and merged into master

| Brief | Package or area |
|---|---|
| K0 | review blockers B1, B2, B3, B5 cleared |
| 8 (A–D) | `tsp.ai` embeddings, pgvector in the Postgres image, `tsp.vector`, API wiring |
| 8b-A, 8b-B | `tsp.crawl`: safe fetching, sources and pages, plan/fetch/refresh/purge jobs, API, compliance |
| 8b-C (part) | jobs `queue` column and `claim_jobs`/`finish_job`/`fail_job`; render queue, internal render API, render worker module; API wiring and plan limits; `CrawlConfig` and Fargate Spot render task in CDK; `docker/crawl-render/Dockerfile`; `docs/modules/crawl.md` |
| K1 | `tsp.sources` |
| K2 | `tsp.graph` |
| K3-A, K3-B, K3-C | `tsp.ai` caching/citations/structured output/batches/translate; `tsp.pipeline`; eval harness |
| K4-A, K4-B, K4-C | `tsp.cms`: items and versions, corrections and lifecycle, complaints desk and restricted windows |
| K5 | `tsp.paywall` |
| K6 (A, B) | `tsp.engage`: polls, receipts, streaks, points, onboarding, moderated submissions, appeals into the K4-C desk |
| K7 | `tsp.trends` |
| K8 | `tsp.i18n`, `tsp.distribute` |

## Not done

- **K6 follow-ups**: poll creation over HTTP and admin views for polls and takes are not built. Set `ENGAGE_PRICE_IN` and `ENGAGE_PRICE_OUT` (placeholders 1.0 and 5.0 USD per million tokens). `ENGAGE_REQUIRE_SUBSCRIPTION=1` by default.
- **8b-C**: finished (web tools, extraction eval, render scaler, alarms). Tool calling works with OpenAI-compatible providers only; the Anthropic adapter does not translate tools yet. The scaler and alarms are tested against fakes and `cdk synth`, not AWS.
- **K0 B4**: the first live deploy (AWS bootstrap, Google and Apple credentials, DNS) needs the owner. Phase 9 (dev environment, verified costs) follows it.
- Phase 11 (public API docs and SDK, outbound webhooks, status page) is only planned.

## Unverified

- The render worker image (`docker/crawl-render/Dockerfile`) has never been built; Chromium and Crawl4AI versions are unpinned. `BrowserFetcher`, `PdfFetcher` (Crawl4AI, Docling) were written from memory of those libraries and tested only with fakes.
- `tsp.trends`: the GDELT and trending-searches parsers follow documented formats but never ran against the live services.
- CDK has synthesized and passed cdk-nag, but nothing has been deployed.
- Browser subresource requests are not filtered inside the render worker; the subnet must block private ranges and the metadata address.

## Known issues

- Crawl monthly page cap can be overshot by up to (concurrency − 1) pages.
- Trends scoring pools regions.
- Upgrade every job worker before enabling the render queue: a worker from before the `queue` column claims jobs from all queues.

## Running things

- Local services: `docker compose -p thestartupproject up -d postgres valkey dynamodb`. They stop by themselves now and then; if many tests skip, start them again.
- Tests: other suites drop shared tables, so give each run its own database: create one `TEMPLATE template0` and set `TSP_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/<name>`. New test packages use a private database and uniquely named test files and helper modules (not `conftest` imports across packages).
- Gate: `cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages apps/api/src && uv run pytest`; from the repository root `make docs-check`.
- Merging: merge each work branch into `master`; shared files that conflict every time: `python/uv.lock` (keep both sides, then `uv lock`), `python/pyproject.toml` `mypy_path`, `CHANGELOG.md`, `python/apps/api/Dockerfile` (`TSP_MIGRATE_MODULES`), `python/apps/api/tests/api_support.py` (`ALL_TABLES`).
- Nothing has been pushed to a remote.

## Branches

Only `master` exists, in `/home/blank/projects/TheStartupProject`. All work branches and extra worktrees are merged and removed.
