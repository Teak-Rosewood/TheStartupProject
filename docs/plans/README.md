# Kit implementation briefs: conventions

Plans only. These briefs build the kit's phases K0, 8, 8b and K1–K8 in `Teak-Rosewood/TheStartupProject`. Every brief's prompt reads this file and its own brief from `docs/plans/`.

The design documents the briefs link to are already in the repository: vector search is `docs/aws/17-vector-search.md`, web ingestion is `docs/aws/18-web-ingestion.md`, the roadmap is `docs/roadmap.md` and the project review is `docs/review-2026-10.md`. The briefs link to them directly.

## Order

| # | Brief | Depends on |
|---|---|---|
| 1 | [K0-blockers.md](K0-blockers.md) | phases 1–7 (then the manual first deploy, B4) |
| 2 | [08-vector.md](08-vector.md) | K0 |
| 3 | [08b-crawl.md](08b-crawl.md) | 8 |
| 4 | [K1-sources.md](K1-sources.md) | 8b |
| 5 | [K2-graph.md](K2-graph.md) | 8 (can run with K1) |
| 6 | [K3-pipeline.md](K3-pipeline.md) | K2 |
| 7 | [K4-cms.md](K4-cms.md) | K3 |
| 8 | [K7-trends.md](K7-trends.md) | K1 (can run with K4) |
| 9 | [K5-paywall.md](K5-paywall.md) | payments, cache |
| 10 | [K6-engage.md](K6-engage.md) | K3, K4 |
| 11 | [K8-i18n-distribution.md](K8-i18n-distribution.md) | 8, K4, notify |

One brief, one pull request (large briefs: one per step). Names of tables and classes are proposals; adjust them before handing a brief over.

## Conventions

These are the kit's existing conventions. Every brief repeats only what differs.

| Area | Convention |
|---|---|
| Package | `python/packages/<name>/` with `pyproject.toml` (hatchling, `name = "tsp-<name>"`, `packages = ["src/tsp"]`, workspace deps under `[tool.uv.sources]`), `src/tsp/<name>/`, `tests/`, `README.md`, `examples/` |
| Namespace | `tsp.<name>`; no `__init__.py` in `src/tsp/` (namespace package) |
| Exports | `src/tsp/<name>/__init__.py` re-exports the public interface and defines `migrations_path()` returning `Path(__file__).parent / "migrations" / "versions"` |
| Models | SQLAlchemy 2 typed models on `tsp.db.Base`; every tenant table has `org_id` and an index on it |
| Migrations | Alembic per package: file `<name>_0001_<slug>.py`, `revision = "<name>_0001"`, `branch_labels = ("<name>",)` on the first one; register the path in the app's migration config |
| API | `build_router(...)` returning a FastAPI `APIRouter`, taking its services and auth dependencies as arguments; no global state |
| Jobs | handlers registered with `JobRegistry.job(name)`; schedules with `JobRegistry.schedule(...)`; enqueue with `enqueue(session, name, payload, run_at=, unique_key=, max_attempts=5)` |
| Compliance | packages holding personal data register an exporter and an eraser with `tsp.compliance` (`register_exporter`, `register_eraser`) via a `register_with_compliance(...)` helper |
| Metering | billable actions call `tsp.metering.record_usage(...)` with a dotted meter name (`ai.embed`, `crawl.page`) |
| Audit | admin and state-changing actions write to `tsp.audit` |
| Admin | read-only `sqladmin` views (`ModelView`) added to `tsp.admin` |
| Tests | pytest + pytest-asyncio; database tests use `TSP_TEST_DATABASE_URL` and skip without it; no network in unit tests (use recorded fixtures) |
| Docs | a page in `docs/modules/<name>.md`; a line in `CHANGELOG.md`; links checked by `docs/check_links.py` |
| Neutral examples | kit code, tests and examples use neutral domains (recipes, product reviews, documentation) and nothing else. |
| Config | settings via the app's config module and environment variables; no secrets in code |
| Infra | CDK constructs in `python/apps/infra`, turned on by a config flag, default off |
