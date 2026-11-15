# Status

The one place that says what is built, what has never met the real world, and what is known to be broken. Update it in the same pull request as the change. Last updated 2026-11-15.

## Built

Every module in the [README table](../README.md) has code, migrations (where it stores data), tests and a page under `docs/modules/`. The kit briefs in [docs/plans/](plans/README.md) (K0, 8, 8b, K1–K8) are all implemented.

The sample API (`python/apps/api`) mounts accounts, admin, compliance, payments, jobs, notify, growth, flags, storage, metering, the AI gateway, vector search, crawl, CMS, pipeline, engage and paywall. `tsp.graph`, `tsp.sources`, `tsp.trends`, `tsp.i18n` and `tsp.distribute` are libraries that no sample app mounts or schedules yet.

## Not done

- **First deploy (K0 B4)**: needs an AWS account, Google and Apple sign-in credentials and DNS ([docs/aws/00-account-setup.md](aws/00-account-setup.md)). Until the repository variables in [09 CI/CD](aws/09-ci-cd.md) are set, the `deploy`, `drift` and `backup-drill` workflows skip.
- **Dev environment** (phase 9): only `config/prod.py` exists.
- **K6 follow-ups**: poll creation over HTTP and admin views for polls and takes. `ENGAGE_PRICE_IN` and `ENGAGE_PRICE_OUT` are placeholders (1.0 and 5.0 USD per million tokens). `ENGAGE_REQUIRE_SUBSCRIPTION=1` by default.
- **Row-level security** exists only on `vector_chunks`; other tenant tables rely on `org_id` filters in queries.
- **Observability**: no tracing or error tracker yet.
- Phases 10–12 of the [roadmap](roadmap.md).

## Unverified (tested only with fakes)

- The render worker image (`docker/crawl-render/Dockerfile`) is built and renders a page every night (`live` workflow), but has not run in AWS. `BrowserFetcher` (Crawl4AI) and `PdfFetcher` (Docling) have opt-in live smoke tests (`TSP_LIVE=1`, nightly `live` workflow).
- `tsp.trends`: GDELT and trending-searches parsers have opt-in live smoke tests; they have not run in production.
- CDK synthesizes and passes cdk-nag, but nothing has been deployed. The render scaler and alarms are tested against fakes.
- Browser subresource requests are not filtered inside the render worker; the subnet must block private ranges and the metadata address.

## Known issues

- The crawl monthly page cap can be overshot by up to (concurrency − 1) pages.
- Upgrade every job worker before enabling the render queue: a worker from before the `queue` column claims jobs from all queues.

## Running things

- Local services: `docker compose -p thestartupproject up -d postgres valkey dynamodb`. They stop by themselves now and then; if many tests skip, start them again.
- Tests: other suites drop shared tables, so give each run its own database: create one `TEMPLATE template0` and set `TSP_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/<name>`. New test packages use a private database and uniquely named test files and helper modules (not `conftest` imports across packages).
- Gate: `cd python && uv run ruff check . && uv run ruff format --check . && uv run mypy packages apps/api/src && uv run pytest`; from the repository root `make docs-check`.
- Shared files that conflict on every merge: `python/uv.lock` (keep both sides, then `uv lock`), `python/pyproject.toml` `mypy_path`, `CHANGELOG.md`, `python/apps/api/Dockerfile` (`TSP_MIGRATE_MODULES`), `python/apps/api/tests/api_support.py` (`ALL_TABLES`).
- Every module the sample API imports must be a dependency in `python/apps/api/pyproject.toml`: the image installs only those, and CI checks the import in a package-only environment.
