# `tsp.crawl` — website ingestion

Keeps a customer's website indexed in [`tsp.vector`](vector.md): safe fetching, robots.txt and rate limits, sitemaps, Markdown extraction, change detection, and optional browser rendering. Plan: [docs/plans/08b-crawl.md](../plans/08b-crawl.md); design and costs: [docs/aws/18-web-ingestion.md](../aws/18-web-ingestion.md). `tsp.sources` builds on the fetchers here.

## Safe fetching (the library part)

- `safe_url(url)` allows only `http` and `https` on ports 80 and 443, refuses credentials in URLs, resolves DNS and rejects any non-public answer (private, loopback, link-local including `169.254.169.254` and `fd00:ec2::254`, multicast, IPv4-mapped and NAT64 forms, legacy decimal/hex/octal IPv4). It returns the IP to connect to; fetchers connect to that pinned address and never resolve again, which stops DNS rebinding.
- `HttpFetcher` follows redirects by hand so every hop goes through `safe_url`, caps the body size, supports conditional GET (`ETag`, `Last-Modified`), and extracts Markdown with Trafilatura. Custom headers (an API key) are sent only to the first host, never to a redirect target.
- `RobotsCache` follows RFC 9309 (4xx means allow, 5xx or unreachable means disallow, cached briefly), `parse_sitemap` reads index files and gzip and refuses DTDs and decompression bombs, `DomainLimiter` paces requests per host through `tsp.cache` and backs off on 429/503.
- `BrowserFetcher` (extra `browser`, Crawl4AI) and `PdfFetcher` (extra `pdf`, Docling) are written but have not been run against the real libraries; their tests use fakes. `BrowserFetcher` checks the start and final URLs; requests the page makes from inside the browser are not filtered, so the render worker's network must block private ranges.

## Sources, pages and jobs

An organization adds a **source** (seed URL, optional sitemap, include/exclude glob patterns, depth, page cap, `render` mode, refresh interval) bound to a `tsp.vector` collection. Crawling never leaves the seed's host.

| Job | What it does |
|---|---|
| `crawl.plan` | Seed plus sitemap URLs become `crawl_pages` rows (idempotent), up to `max_pages`; queues a fetch for each. |
| `crawl.fetch` | robots.txt, limiter, conditional fetch. Unchanged content is not re-embedded; changed content replaces the old vector document; 404/410 deletes it; links within depth and scope are added. |
| `crawl.refresh` | Hourly; plans each active source whose `refresh_hours` have passed. |
| `crawl.purge` | Deletes a source's documents in batches, then the source. `DELETE` on a source hides it at once and queues this. |

Pages that fail transiently (timeouts, 5xx, 429) retry with the job queue's backoff; permanent failures are recorded on the page. Each fetched page writes a `crawl.page` usage record. Two pages with identical text share one vector document (the store de-duplicates by content), and the document is deleted only when no page points at it any more.

**Plan limits** come from a `limits(session, org_id)` callable (the sample API reads them from the plan's features): `crawl_pages_month`, `crawl_depth`, `crawl_refresh_min_hours`. The monthly page cap is checked before each fetch, so concurrent workers can overshoot it by up to (concurrency − 1) pages.

**API** (`build_router`, under `/api/orgs/{org_id}/crawl`): `POST/GET /sources`, `GET /sources/{id}/pages?status=`, `POST /sources/{id}/recrawl`, `PATCH /sources/{id}` (`active` or `paused`), `DELETE /sources/{id}`. Members read, admins change. The kill switch is the flag `kill:crawl`. Seeds that resolve to internal addresses are refused with 422.

**Compliance:** the sources a user added are exported with their page URLs and erased (through the purge job) when the user leaves.

## Rendering JavaScript pages

`render` is `never` (plain HTTP), `auto` (plain HTTP first; a thin or "enable JavaScript" page is re-queued for a browser) or `always`. Browser work uses its own job queue, `crawl.render`, which only the render worker reads (the queue column in `tsp.jobs`; upgrade all workers before enabling this, because an old worker would claim any queue).

The render worker (`python -m tsp.crawl.render_worker`, image in `docker/crawl-render`) runs a real browser on pages we do not control, so it has **no database access and no secrets** except `CRAWL_RENDER_TOKEN`. It asks the API for a page (`POST /api/internal/crawl/render/claim`), renders it, and posts the text back (`.../{job_id}/result`). The API checks robots.txt and the host limiter before handing a page out, applies the result through the same path as a normal fetch, and refuses a result whose final URL is not public. It exits after `CRAWL_IDLE_EXIT_MINUTES` (default 10) with nothing to do.

Turn it on in the API with `CRAWL_RENDER=fargate` and `CRAWL_RENDER_TOKEN` (at least 24 random characters; the routes answer 401 without it and are not mounted at all unless both are set). Without it, an `always` source fails each page with "rendering is not configured" rather than fetching unrendered text.

### Infrastructure

Set `crawl: {enabled: true, render: fargate}` in the environment config (`CrawlConfig`). That adds an ECR repository `crawl-render`, a Fargate Spot ARM task definition (1 vCPU, 2 GB), a security group with **no inbound rules** and egress only to ports 80, 443 and DNS, a task role with no permissions, and passes `CRAWL_RENDER=fargate` plus the token to the API. Create the token by hand before deploying: a secret `/tsp-<env>/crawl/render-token` holding at least 24 random characters. Build and push `docker/crawl-render/Dockerfile` to the new repository with the same tag as the API.

Start a worker with the `RunTaskCommand` output of the backend stack (`aws ecs run-task ... FARGATE_SPOT`); it renders until the queue has been empty for `idle_exit_minutes`, then exits. The task's public IP is the only network exposure; block your own VPC ranges and the metadata address from it at the subnet level if you can, because Chromium follows links inside pages.

Upgrade every job worker (the API and any `tsp-jobs worker`) before turning rendering on: workers from before the `queue` column existed claim jobs from every queue.

## Setup

```python
from tsp.crawl import ChainFetcher, CrawlService, CrawlSettings, HttpFetcher, RobotsCache, build_router, register_jobs
http = HttpFetcher()
svc = CrawlService(vector, ChainFetcher(http), http, robots, cache, db, CrawlSettings(), limits)
register_jobs(registry, svc)
```

Migrate with `modules=[..., "tsp.crawl"]` (after `tsp.vector`). `python/apps/api/src/tsp_api/main.py` is the reference wiring.

## Not done yet

- A job that starts the render worker when `crawl.render` has pages waiting (today you start it by hand, see above), and alarms for queue age and render failures.
- The render image has not been built or run: pin the Chromium and Crawl4AI versions on its first real build.
- Live tools for `tsp.ai` tool calling (`web_search`, `fetch_url`), the extraction evaluation set, and crawl alarms.
