# Phase 8b Web ingestion (`tsp.crawl`)

**Goal:** an organization adds a site (seed URL or sitemap); the kit crawls it politely and safely, keeps it fresh, and feeds changed pages to `tsp.vector`. Design: [../../web-scraping-to-vector-db.md](../aws/18-web-ingestion.md); roadmap 8b.1–8b.9.

**Depends on:** phase 8 (8-A to 8-C). **Branch:** `feat/8b-crawl-<step>`. **Size:** large; three pull requests (8b-A to 8b-C). Do the tool spike (8b.1) by hand first and record an ADR; the brief assumes the expected outcome (HTTP + Trafilatura default, Crawl4AI for JavaScript pages, Docling for PDFs).

## 8b-A Core library (no database)

Files: `python/packages/crawl/` with `pyproject.toml` (deps `httpx`, `trafilatura`, `protego` (robots), `tsp-cache`; extras `browser` → `crawl4ai`, `pdf` → `docling`, `scrapy` → `scrapy`), `src/tsp/crawl/{__init__,safe_url,robots,sitemap,urls,page,fetch_http,fetch_browser,fetch_pdf,politeness,errors}.py`.

Interfaces:
```python
@dataclass
class Page:
    url: str; final_url: str; status: int; title: str | None; markdown: str
    links: list[str]; content_hash: str; etag: str | None; last_modified: str | None
    fetched_at: datetime; content_type: str; fetcher: str

class Fetcher(Protocol):
    async def fetch(self, url: str, *, etag: str | None = None, last_modified: str | None = None) -> Page | NotModified

HttpFetcher(client=None, max_bytes=10_000_000, timeout=20, max_redirects=5, user_agent=...)
BrowserFetcher(...)        # Crawl4AI; same safe_url checks on every navigation and subresource where possible
PdfFetcher(...)            # download via HttpFetcher, convert with Docling, OCR languages configurable
ChainFetcher([HttpFetcher, BrowserFetcher], needs_render=heuristic)  # falls back when text is too short or JS-only markers

safe_url(url: str) -> ResolvedURL      # raises UnsafeURL
RobotsCache(cache).allowed(url, user_agent) -> bool; crawl_delay(host)
parse_sitemap(xml) -> list[SitemapEntry]   # handles index sitemaps and gzip
normalise(url) -> str; canonical(page) -> str
DomainLimiter(cache, default_rps=1.0).acquire(host)   # token bucket in tsp.cache; backoff on 429/503 with Retry-After
```
SSRF rules in `safe_url`: schemes `http`, `https` only; resolve DNS and reject private, loopback, link-local (incl. `169.254.169.254`, `fd00:ec2::254`), multicast, unspecified, IPv4-mapped IPv6 private; pin the resolved IP for the connection (custom httpx transport) to stop DNS rebinding; re-check after every redirect; ports 80 and 443 only unless configured.

Tests: SSRF table (redirect to metadata IP, DNS rebinding via a fake resolver, `[::1]`, `0.0.0.0`, decimal IP `2130706433`, `localhost.` with dot); robots allow and deny and crawl-delay; sitemap index; conditional GET returns `NotModified`; Trafilatura output on 3 saved HTML fixtures; chain falls back to browser on a JS-only fixture (browser mocked).

## 8b-B Crawl jobs, tables, API

Files: `src/tsp/crawl/{models,service,jobs,api,compliance,config}.py`, `migrations/versions/crawl_0001_init.py`.

Tables:
| Table | Columns |
|---|---|
| `crawl_sources` | id, org_id, collection_id (tsp.vector), seed_url, sitemap_url, include jsonb, exclude jsonb, max_depth, max_pages, render (`never`/`auto`/`always`), refresh_hours, status (`active`/`paused`/`error`), last_run_at, created_by |
| `crawl_pages` | id, org_id, source_id fk, url (normalised), final_url, depth, content_hash, etag, last_modified, status (`queued`/`ok`/`not_modified`/`skipped_robots`/`error`/`gone`), error, http_status, fetched_at, vector_document_id; unique (source_id, url) |

Jobs:
- `crawl.plan(source_id)`: seed + sitemap → insert pages (`ON CONFLICT DO NOTHING`) → enqueue `crawl.fetch` with `unique_key=f"crawl:{source_id}:{url}"`.
- `crawl.fetch(page_id)`: robots → limiter → fetch (conditional) → if hash changed: write Markdown to `tsp.storage` under `crawl/<org>/<source>/<sha>.md`, upsert into tsp.vector as a document with `source_uri=url` and metadata `{fetched_at, title}`, enqueue `vector.ingest`; follow links within rules and depth; 404/410 → status `gone` and delete its vector document.
- `crawl.refresh`: scheduled hourly; enqueues `crawl.plan` for sources whose `refresh_hours` elapsed.
- Render work goes to a separate queue name `crawl.render` so only the render worker picks it.

API `build_router(service, account, session)`: `POST /api/crawl/sources`, `GET /api/crawl/sources`, `GET /api/crawl/sources/{id}/pages?status=`, `POST /api/crawl/sources/{id}/recrawl`, `PATCH` (pause), `DELETE` (deletes pages and vector documents).

Limits and safety: plan limits `crawl_pages_month`, `crawl_depth`, `crawl_refresh_min_hours`; flag `crawl`; metering `crawl.page` per fetched page, `crawl.render` per rendered page; audit on create and delete; compliance exporter (sources and page URLs) and eraser.

Tests: plan is idempotent; refetch with same hash does not re-ingest; removed page deletes chunks; plan limits stop at the cap; org isolation.

## 8b-C Render worker, infra, live tools, docs

- `docker/crawl-render/Dockerfile` (Graviton, Chromium + Crawl4AI, runs `tsp-jobs worker --queue crawl.render`).
- `CrawlConfig` in `EnvConfig`: `enabled=False`, `render: "none"|"fargate"`, `max_render_tasks=1`, `bucket_prefix="crawl/"`, `search: "none"|"searxng"|"api"`. CDK: Fargate Spot task definition, public subnet, no inbound, IAM limited to `crawl/*` and its queue, no DB secrets (it calls back via an internal API endpoint with a scoped token, or writes results to S3 for the API to pick up). A small scaler job starts a task when `crawl.render` has work and the worker exits after N idle minutes.
- Live tools for `tsp.ai` tool calling: `web_search` (SearXNG URL in config; paid API adapter optional) and `fetch_url` (via `ChainFetcher` + `safe_url`); results quoted as data; metered `web.search`, `crawl.page`.
- Alarms: crawl queue age, error rate per domain, render task failures.
- Eval: 10 fixture pages with hand-written reference Markdown; score boilerplate removal and table retention.
- Docs: `docs/modules/crawl.md`, runbook (pause a source, block a domain globally via a `crawl_blocklist` config, takedown request), AUP and legal updates, CHANGELOG.

## Done when (phase exit)
A help-center URL is crawled within plan limits respecting robots.txt; answers cite page URLs; recrawl re-embeds only changed pages; deleting the source removes everything; a URL resolving to an internal address is refused.

## Prompt

```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/8b-crawl-X (X = A, B or C).
Read docs/plans/08b-crawl.md section 8b-X, docs/plans/README.md for conventions, the ADR from
the tool spike, and the tsp.vector package. Implement section 8b-X. Security first: write the
SSRF tests before safe_url. No live network in unit tests; use saved fixtures and a fake resolver.
Run lint, types and tests before each commit; open a pull request with the section checklist.
```
