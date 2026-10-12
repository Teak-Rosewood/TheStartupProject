# `tsp.sources` — external data sources and connectors

A registry of external sources and a framework that fetches from each one by the cheapest allowed method first, falls back when a method fails, resumes from a cursor, and tells you when a source's rules change. Plan: [docs/plans/K1-sources.md](../plans/K1-sources.md). Depends on [`tsp.crawl`](../plans/08b-crawl.md) for safe fetching.

## The pieces

| Piece | What it is |
|---|---|
| `src_sources` row | One source: URL, licence, terms URL, allowed uses, schedule, rate limit, ordered `strategies`, monthly paid cap, `enabled`. Change rows with `tsp-sources import`, not code. |
| `Connector` subclass | Lives in your application repository. Names its `streams` and returns its strategies; `next_cursor` says how the cursor moves. Register it with `@register_connector`. |
| Strategy | One way to get the data: `open_data`, `bulk`, `scrape`, `api_free`, `api_paid`. The row says in which order to try them. |
| `SourceRunner` | Runs the strategies, stores raw bytes, upserts items, advances the cursor, records the run. |
| `SourceChecks` | Reachability, robots.txt, and a hash of the terms page. |

## Writing a connector

```python
@register_connector
class RecipesConnector(Connector):
    key = "recipes-example"          # matches src_sources.key
    streams = ["recipes"]

    def strategies(self):
        return {
            "scrape": HttpListStrategy(start_url, parse_listing),
            "api_free": JsonApiStrategy(api_url, parse_api, auth_key="recipes_api_key"),
        }
```

A complete version with saved-fixture tests is in `python/packages/sources/examples/recipes_connector.py`. The strategies you can build from:

- `HttpListStrategy(start_url, parse)`: a paginated HTML listing. You supply the start URL and a function from a `Page` to the entries and the next-page link; each entry's page is fetched as Markdown.
- `SitemapStrategy`: every URL in the source's sitemap, index files followed two levels deep.
- `BulkFileStrategy(manifest_url)`: download the files named in a JSON manifest.
- `JsonApiStrategy(url_for, parse, auth_key=...)`: a paged JSON API. The key comes from the runner's `config` and is sent only to the API's own host, never to robots.txt or to a redirect target.
- `PaidApiStrategy(inner, cost_per_item)`: wraps any strategy and bills each item against the source's `paid_cap_monthly`.

## How a sync runs

1. Load the source row; a disabled source is refused (`SourceDisabled`).
2. For each stream, start from the stored cursor (or `--since`).
3. Walk the row's `strategies` in order. A strategy the connector does not provide, or whose `available()` is false (missing key, robots disallow recorded, paid cap spent), is skipped. If one raises `StrategyFailed` (blocked, HTTP error, robots.txt disallows the page, a rate-limit wait longer than `max_wait_seconds`), the runner tries the next.
4. Items are processed in batches (`SourcesConfig.batch_size`, default 50). In one transaction a batch stores each changed item's raw bytes at `sources/<key>/<year>/<external_id>.<ext>`, upserts `src_items` by `(source_id, external_id)`, and advances the cursor. An item whose checksum has not changed is not rewritten.
5. The cursor moves only when a batch commits. A crash repeats at most one batch, and the repeat is harmless because writes are upserts. A fallback strategy starts from the cursor the failed one left, so a connector's strategies must agree on what its cursor means.
6. The run row records `strategy_used`, counts, cost and error summary, and a `manifest.jsonl` (one line per item: new, updated or unchanged) is written next to the raw files.

Run status: `ok` when a strategy finished the stream (even after a fallback; the fallback shows in `errors` and `error_summary`), `partial` when it stopped after committing some items, `failed` when it produced nothing.

`sync(..., dry_run=True)` fetches and counts but writes nothing: no files, items, cursor or run row.

## Politeness and safety

Every network call goes through the connector context: robots.txt is checked (and its Crawl-delay honoured), the per-domain limiter in `tsp.cache` paces requests at the source's `rate_limit_rps`, 429 and 5xx are retried with the limiter's backoff, and `tsp.crawl.safe_url` blocks private and metadata addresses, including after redirects. Strategies cannot skip these by accident.

## Health checks

`SourceChecks.check(key)` fetches the site, re-reads robots.txt (bypassing its cache) and hashes the terms page. It switches the source off (`enabled=false`) and writes an audit entry `sources.disabled` when robots.txt now disallows our crawler or the terms page changed since the last check. The first check only records the baseline. An unreadable robots.txt or an unreachable site is recorded but never disables a source, so an outage on their side does not stop your ingestion.

## Paid sources

`paid_cap_monthly` is opt-in: with no cap a paid strategy is unavailable. The runner sums this calendar month's `src_runs.cost`, a `PaidApiStrategy` stops the moment the next item would pass the cap, and each run with a cost writes one `tsp.metering` usage record of kind `sources.paid_call`.

## CLI

```bash
export DATABASE_URL=... TSP_SOURCES_CONNECTORS=my_app.connectors TSP_SOURCES_DIR=./sources-data
tsp-sources import sources.csv          # upsert rows; blank cells never wipe a field
tsp-sources list
tsp-sources check --all
tsp-sources sync --source recipes-example [--stream S] [--since X] [--strategy api_free] [--dry-run]
tsp-sources backfill --source recipes-example --since 2025-01-01
tsp-sources status
tsp-sources reconcile --source recipes-example --stream recipes
```

CSV columns: `key,name,url` (required), `tier,country,languages,licence,terms_url,allowed_uses,strategies,schedule,rate_limit_rps,owner,paid_cap_monthly,notes,enabled`. List columns use `;` between values. Use `TSP_SOURCES_BUCKET` instead of `TSP_SOURCES_DIR` for S3 (install the `s3` extra). API keys: `TSP_SOURCES_SECRET_RECIPES_API_KEY` becomes config key `recipes_api_key`.

`reconcile` compares the first N items from two strategies: ids only in one, and ids whose own modification time disagrees.

## Jobs, API, admin

- `register_jobs(registry, runner, checks)` adds `sources.sync`, `sources.check`, `sources.reconcile` and `sources.dispatch`. Dispatch runs every five minutes and enqueues a sync for each enabled source whose `schedule` (`every 6h`, `daily 02:00`) is due, once per slot. Checks fan out per source once a week; reconcile runs on Mondays for sources with two or more strategies and logs differences.
- `build_router(account, session, admin)`: `GET /api/sources`, `GET /api/sources/{key}/runs`, `POST /api/sources/{key}/sync` (queues a job). All three require your `admin` dependency.
- `tsp.sources.admin` (extra `admin`) has read-only `SourceView`, `RunView` and `CheckView` for sqladmin.

## Setup

`upgrade(url, app_migrations, modules=["tsp.sources"])` creates the tables. The runner needs a `Database`, a `RawStore` (`LocalRawStore` or `S3RawStore`), a `Cache`, and an `HttpFetcher`.

## Limits

- Browser rendering and PDF conversion are available through `tsp.crawl` fetchers, not wired by default: pass a `ChainFetcher` with a browser as `fetcher=`.
- Reconcile compares ids and modification times, not content, because the same record fetched as HTML and as JSON never has the same bytes.
