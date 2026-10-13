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

The API starts workers itself: the `crawl.render_scale` job (every minute, registered when the `CRAWL_RENDER_*` variables below are set) counts due jobs on the `crawl.render` queue and calls `ecs:RunTask` (Fargate Spot, public subnet, no inbound) when pages wait and fewer than `max_render_tasks` render tasks run: one task, plus one more for every 20 waiting pages, up to the cap. A task that is already running or starting counts, so it never starts a second one for the same backlog. A worker renders until the queue has been empty for `idle_exit_minutes`, then exits. The `RunTaskCommand` stack output still starts one by hand. The task's public IP is the only network exposure; block your own VPC ranges and the metadata address from it at the subnet level if you can, because Chromium follows links inside pages.

`CrawlConfig` knobs: `idle_exit_minutes` (default 10), `max_render_tasks` (default 1, at most 10) and `render_queue_alarm_minutes` (default 15). The `Crawl` construct passes the API `CRAWL_RENDER_CLUSTER`, `CRAWL_RENDER_TASK_DEFINITION` (a fixed family name, `<prefix>-crawl-render`), `CRAWL_RENDER_SUBNETS`, `CRAWL_RENDER_SECURITY_GROUPS` and `CRAWL_RENDER_MAX_TASKS`, and gives the API task role only: `ecs:RunTask` on that task definition in that cluster, `ecs:ListTasks` in that cluster, `iam:PassRole` on the task's own task and execution roles (to ECS tasks only), and `cloudwatch:PutMetricData` in the `TSP/Crawl` namespace. AWS errors when starting a task are logged and retried a minute later; they do not kill the job.

**Alarms** (emailed through the usual alarm topic, only when `render: fargate`). The same job publishes `RenderQueueAgeSeconds` (age of the oldest due render job), `RenderJobsDead` (render jobs that died after all retries in the last 24 hours) and `RenderStartFailures` (failed attempts to start a task) to CloudWatch namespace `TSP/Crawl`. Alarms: queue age above `render_queue_alarm_minutes` for two 5-minute periods (the worker is not starting or not keeping up: check Spot capacity and the task's logs), any dead render job (pages needing a browser are not being crawled), and more than two start failures over 15 minutes (`ecs:RunTask` failing or no capacity). Metrics are published only while the scale job runs, so a stopped job leaves the alarms in "not breaching": watch the job queue too.

Upgrade every job worker (the API and any `tsp-jobs worker`) before turning rendering on: workers from before the `queue` column existed claim jobs from every queue.

## Setup

```python
from tsp.crawl import ChainFetcher, CrawlService, CrawlSettings, HttpFetcher, RobotsCache, build_router, register_jobs
http = HttpFetcher()
svc = CrawlService(vector, ChainFetcher(http), http, robots, cache, db, CrawlSettings(), limits)
register_jobs(registry, svc)
```

Migrate with `modules=[..., "tsp.crawl"]` (after `tsp.vector`). `python/apps/api/src/tsp_api/main.py` is the reference wiring.

## Live web tools for LLM tool calling

`tsp.crawl.tools` gives a model two read-only tools. `LiveTools(fetcher, search=None, robots=None, cache=None, on_usage=None)`:

- `fetch_url(url)`: the page as Markdown through your fetcher (normally `ChainFetcher`, so `safe_url` checks the first URL and every redirect). A URL that resolves to a private or metadata address is refused with a generic message, robots.txt is honoured when you pass a `RobotsCache`, and fetched pages are cached for 15 minutes in `tsp.cache` (cache hits are not metered).
- `web_search(query)`: titles, URLs and snippets from a `SearchProvider`. `SearxngSearch("http://searxng:8080")` talks to a SearXNG instance you run (enable its `json` format). The SearXNG URL is configuration you control, so it is not run through `safe_url`. A paid search API plugs in by implementing `async search(query, limit) -> list[SearchResult]`.

Every result is wrapped as `<tool_result tool="..." source="..." untrusted="true">...</tool_result>`, truncated to `max_chars`, and a closing tag inside the page text is defanged so a page cannot end the quote early. `SYSTEM_NOTE` tells the model to treat results as data and never follow instructions in them. There are no tools that act, so nothing read from the web can cause an action. Failures come back as quoted text the model can read; they do not raise.

Metering: `on_usage(kind, units)` is called with `web.search` and `crawl.page` after each successful call; `usage_recorder(session, org_id)` writes them to `usage_records`.

```python
live = LiveTools(ChainFetcher(http), SearxngSearch(os.environ["SEARXNG_URL"]),
                 robots=robots, cache=cache, on_usage=usage_recorder(session, org_id))
answer = await run_with_tools(client, live, "gpt-4o-mini", [{"role": "user", "content": "..."}], max_rounds=4)
```

`run_with_tools` loops until the model answers (at most `max_rounds` tool rounds, then it is asked to answer without tools). Tool calling works with OpenAI-compatible providers. The Anthropic adapter does not yet translate OpenAI-style tools and `tool_use` blocks, so use an OpenAI-compatible provider for this.

## Extraction quality set

`python/packages/crawl/tests/extraction/` holds 10 saved pages (help article, recipe, pricing table, blog post, changelog, FAQ, API reference, news article, spec sheet, tutorial), each with the usual chrome (navigation, cookie banner, newsletter box, share links, related articles, footer), and a hand-written reference Markdown for each. `tsp.crawl.evaluate.score_extraction(markdown, reference, boilerplate)` scores three numbers from 0 to 1: `recall` (how many reference words survived), `boilerplate_removed` (phrases from the page chrome that must not appear) and `table_retention` (reference table cells still present and still in table form). The tests run all 10 pages through the HTTP extractor: every page must have no chrome and recall of at least 0.9, tables at least 0.9, and the set must average 0.95 recall, 0.98 chrome removal and 0.95 table retention. The pages are clean synthetic ones, so this guards against regressions in the extractor settings and a trafilatura upgrade; it is not a benchmark on messy real sites. Add a page and its reference when you find a site that extracts badly.

## Not done yet

- The render image has not been built or run: pin the Chromium and Crawl4AI versions on its first real build.
- The render-start job and the alarms were tested against fake ECS and CloudWatch clients and synthesized with cdk-nag, not against AWS.
- Anthropic tool calling in `tsp.ai` (see above).
