# Handoff, October 2026

State of the kit after the K0–K8 build. Local `master` is `530ed9c` (not pushed). On that commit: 790 tests pass, ruff, mypy, `docs/check_links.py` and `docs/check_modules.py` are clean.

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
| K7 | `tsp.trends` |
| K8 | `tsp.i18n`, `tsp.distribute` |

## Not done

- **K6 `tsp.engage`** (`docs/plans/K6-engage.md`): only a skeleton exists, on branch `feat/k6-a` (commit `eaa9063`: pyproject, migration `engage_0001`, models; untested, not synced to uv). No services, API or tests. K6-B needs the K4-C complaints desk, which is now merged.
- **8b-C leftovers**: `web_search` and `fetch_url` tools for `tsp.ai` tool calling, the 10-page extraction evaluation set, a job that starts the render worker when `crawl.render` has work (today: run the `RunTaskCommand` stack output by hand), crawl alarms. Work in progress is uncommitted in the worktree `/home/blank/projects/TSP-crawl-D` (branch `feat/8b-crawl-D`): `packages/crawl/src/tsp/crawl/tools.py`, `tests/test_crawl_tools.py`, `tests/extraction/`, `packages/ai/tests/test_tool_calls.py` and edits to `ai/client.py` and `crawl/__init__.py`. It is unreviewed and untested by the orchestrator; run its tests before committing or discard it.
- **K0 B4**: the first live deploy (AWS bootstrap, Google and Apple credentials, DNS) needs the owner. Phase 9 (dev environment, verified costs) follows it.
- Phase 11 (public API docs and SDK, outbound webhooks, status page) is only planned.

## Unverified

- The render worker image (`docker/crawl-render/Dockerfile`) has never been built; Chromium and Crawl4AI versions are unpinned. `BrowserFetcher`, `PdfFetcher` (Crawl4AI, Docling) were written from memory of those libraries and tested only with fakes.
- `tsp.trends`: the GDELT and trending-searches parsers follow documented formats but never ran against the live services.
- CDK has synthesized and passed cdk-nag, but nothing has been deployed.
- Browser subresource requests are not filtered inside the render worker; the subnet must block private ranges and the metadata address.

## Known issues

- `sqladmin` ignores a class-body `identity`, so admin views in `tsp.sources` and `tsp.trends` get names like `topic-row` and role grants on them do nothing. `tsp.cms` assigns `identity` after class creation; do the same for those two (`sources/admin.py`, `trends/admin.py`).
- Crawl monthly page cap can be overshot by up to (concurrency − 1) pages.
- Trends scoring pools regions.
- Upgrade every job worker before enabling the render queue: a worker from before the `queue` column claims jobs from all queues.

## Running things

- Local services: `docker compose -p thestartupproject up -d postgres valkey dynamodb`. They stop by themselves now and then; if many tests skip, start them again.
- Tests: other suites drop shared tables, so give each run its own database: create one `TEMPLATE template0` and set `TSP_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/<name>`. New test packages use a private database and uniquely named test files and helper modules (not `conftest` imports across packages).
- Gate: `cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages apps/api/src && uv run pytest`; from the repository root `make docs-check`.
- Merging: work branches are merged by hand into the `integration` branch (worktree `/home/blank/projects/TSP-merge`) and `master` is moved to it. Shared files that conflict every time: `python/uv.lock` (keep both sides, then `uv lock`), `python/pyproject.toml` `mypy_path`, `CHANGELOG.md`, `python/apps/api/Dockerfile` (`TSP_MIGRATE_MODULES`), `python/apps/api/tests/api_support.py` (`ALL_TABLES`).
- Nothing has been pushed to a remote.

## Worktrees

`/home/blank/projects/TheStartupProject` holds `feat/k6-a`; `TSP-merge` is `integration` (== master); `TSP-crawl-D` has the uncommitted tools work; the other `TSP-*` worktrees are merged branches and can be removed with `git worktree remove`.
