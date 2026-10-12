# K1 Sources and connectors (`tsp.sources`)

**Goal:** a registry of external data sources and a connector framework that fetches from each source by the cheapest allowed method first (open data → bulk → scrape → free API → paid API), falls back on failure, resumes from a cursor, and reports health. Generic: example sources are recipe sites and open product datasets.

**Depends on:** 8b (`tsp.crawl` for the scrape rung and `safe_url`). **Branch:** `feat/k1-sources`. **Size:** medium, one or two pull requests.

## Files
`python/packages/sources/` with `src/tsp/sources/{__init__,models,registry,connector,strategies,runner,checks,cli,jobs,api,admin,config}.py`, `migrations/versions/sources_0001_init.py`, `examples/recipes_connector.py`, `tests/`.

## Data model
| Table | Columns |
|---|---|
| `src_sources` | id, org_id (nullable for global sources), key (unique slug), name, url, tier (free text, e.g. `primary`), country, languages text[], licence, terms_url, robots_ok bool, terms_checked_at, allowed_uses text[] (`index`, `quote`, `classify_only`), strategies jsonb (ordered list, e.g. `["bulk","scrape","api_free"]`), schedule (cron), rate_limit_rps, owner, enabled, paid_cap_monthly numeric, notes |
| `src_cursors` | source_id, stream (e.g. `bills`), value text, updated_at; pk (source_id, stream) |
| `src_runs` | id, source_id, stream, strategy_used, started_at, finished_at, status (`ok`/`partial`/`failed`), items_new, items_updated, errors, cost numeric, manifest_uri, error_summary |
| `src_items` | id, source_id, stream, external_id, url, checksum, raw_uri (S3), content_type, fetched_at, metadata jsonb; unique (source_id, external_id) |
| `src_checks` | id, source_id, checked_at, robots_ok, terms_hash, reachable, notes |

## Interfaces
```python
class Item(BaseModel): external_id: str; url: str | None; content: bytes | None; content_type: str; metadata: dict; updated_at: datetime | None

class Strategy(Protocol):
    name: ClassVar[Literal["open_data","bulk","scrape","api_free","api_paid"]]
    async def available(self, ctx: ConnectorContext) -> bool          # e.g. key present, terms allow
    def fetch(self, ctx: ConnectorContext, stream: str, since: str | None) -> AsyncIterator[Item]

class Connector:                     # subclass per source in the app repository
    key: ClassVar[str]
    streams: ClassVar[list[str]]
    def strategies(self) -> dict[str, Strategy]
    def next_cursor(self, item: Item, current: str | None) -> str | None

register_connector(cls); get_connector(key)

class SourceRunner:
    async def sync(self, source_key, stream=None, since=None, *, strategy=None, dry_run=False) -> RunResult
    # tries strategies in the source row's order, skips unavailable, falls back on StrategyFailed,
    # writes raw content to tsp.storage at sources/<key>/<yyyy>/<external_id>.<ext>, upserts src_items
    # by checksum, advances the cursor only after a batch commits, writes src_runs + manifest.jsonl
    async def reconcile(self, source_key, stream, sample=50) -> ReconcileReport   # compares two strategies

class SourceChecks: async def check(source_key) -> CheckResult   # robots.txt, terms page hash, reachability
```
Built-in strategies: `HttpListStrategy` (paginated listing + `tsp.crawl` fetchers), `SitemapStrategy`, `BulkFileStrategy` (S3/HTTP manifest of files), `JsonApiStrategy` (base with paging, auth header from config, retry), `PaidApiStrategy` (wraps any API strategy, enforces `paid_cap_monthly` via `tsp.metering` meter `sources.paid_call`).

Rules: every network call goes through `safe_url` and the per-domain limiter; a source whose check finds robots or terms changed is set `enabled=false` and an audit entry is written; strategies are configuration (row order), not code.

## CLI `tsp-sources`
`import <csv>` (upsert rows from a source sheet), `list`, `check [--all|--source]`, `sync --source X [--stream] [--since] [--strategy]`, `backfill --source X --since DATE`, `status`, `reconcile --source X --stream Y`.

## Jobs
`sources.sync` (per source on its schedule), `sources.check` (weekly), `sources.reconcile` (weekly where two strategies exist).

## API and admin
`build_router(...)`: `GET /api/sources`, `GET /api/sources/{key}/runs`, `POST /api/sources/{key}/sync` (admin role). Admin views: sources, runs, checks (read-only).

## Tests
- Fallback: scrape strategy raises → runner uses API strategy → run records `strategy_used="api_free"`.
- Resume: crash mid-run (exception after batch 2) → next run starts from the committed cursor, no duplicate items.
- Checksum: unchanged item is not re-written.
- Paid cap: cap reached → paid strategy reports unavailable.
- Terms change: `check` flips `enabled` off.
- Example recipes connector against saved fixtures.

## Done when
A sample source syncs by scraping, falls back to an API when scraping fails, resumes from its cursor; docs page `docs/modules/sources.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k1-sources.
Read docs/plans/K1-sources.md and docs/plans/README.md. Read tsp.crawl, tsp.jobs, tsp.storage,
tsp.metering and the flags package (layout template). Implement the brief: tables, interfaces,
built-in strategies, runner, checks, CLI, jobs, API, admin views, tests and docs. Use neutral
example data only (recipes). No live network in tests. Run lint, types and tests, then open a PR.
```
