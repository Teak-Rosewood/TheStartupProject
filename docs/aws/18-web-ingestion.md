# 18 · Web ingestion: scraping into vector search

Fetch pages from the internet, turn them into clean text, and feed them into [vector search](17-vector-search.md) so RAG answers can cite the web. This page picks the open-source tools, the AWS runtime and the guardrails, and estimates costs. The build plan is [phase 8b in the roadmap](../roadmap.md#phase-8b-web-ingestion).

> Prices are planning estimates (us-east-1 list prices, ap-south-1 within about 10%), marked **(verify)** where they move. Licenses are as published by each project at the time of writing **(verify the LICENSE file before adopting)**.

## Is there something better than Scrapy?

Not one tool that wins everywhere. Scrapy is still the best open-source framework for **large, rule-based crawls of structured data** (thousands of product pages, a known site layout, millions of requests). It was not built for **LLM ingestion**: it does not render JavaScript without a plugin, and it has no built-in conversion of pages to clean Markdown.

| Tool | Language, license | Best at | Weak at | Verdict for this kit |
|---|---|---|---|---|
| **Crawl4AI** | Python, Apache-2.0 | page → clean Markdown for LLMs, JavaScript rendering (Playwright), deep crawl strategies, optional LLM-based extraction, resumable crawls | very large crawls need your own queue and scheduling | **default fetcher for RAG ingestion** |
| **Scrapy** (+ `scrapy-playwright`) | Python, BSD-3 | high-volume crawls, mature middleware (retries, throttling, pipelines), structured extraction | no LLM-ready output; Twisted reactor fits awkwardly into an asyncio app | keep for big structured crawls of known sites |
| **Crawlee for Python** | Python, Apache-2.0 | crawler framework with request queue, autoscaling, session and fingerprint management, HTTP and browser crawlers in one API | younger than Scrapy; Markdown conversion is yours to add | strong alternative to Scrapy if you start fresh |
| **Firecrawl** (self-hosted) | TypeScript, **AGPL-3.0** core | all-in-one API: scrape, crawl, map, Markdown, structured extraction | AGPL obligations when you run a modified version as a network service; the hosted anti-bot layer is not open source | use the hosted API if you want zero ops; avoid embedding the self-hosted core in a commercial product without legal review |
| **Trafilatura** | Python, Apache-2.0 **(verify)** | fast main-content extraction from HTML (strips menus, ads, footers), metadata, sitemaps and feeds | no JavaScript rendering | **cheap path for static pages**, and the clean-up step after any fetcher |
| **Playwright** | Python/Node, Apache-2.0 | a real browser for sites that need JavaScript or clicks | heavy: ~300–500 MB RAM per browser | used underneath Crawl4AI and Crawlee |
| **Docling** / **Unstructured** | Python, MIT / Apache-2.0 | PDFs, Office files, tables from linked documents | slow on scanned files | for documents found while crawling |

**Recommendation:** use **Crawl4AI** as the fetcher behind a small `tsp.crawl` interface. Try a plain HTTP fetch with **Trafilatura** first and fall back to the browser only when a page needs it. Use **Docling** for linked PDFs. Keep Scrapy (or Crawlee) as an optional backend for big crawls of one site. Because the interface is the same either way, changing tools later touches only the backend.

## Two ways the product uses the web

| | Indexed crawl | Live web tools |
|---|---|---|
| What | crawl sites ahead of time on a schedule, store and embed them | at question time the model calls `web_search` and `fetch_url` and reads the results |
| Good for | a customer's own docs and help center, known sources, competitors, catalogs | fresh news and one-off questions |
| Storage | S3 + vector store | none, or a short cache |
| Latency | fast at query time | several seconds per question |
| Search provider | not needed (seed URLs, sitemaps) | needed: self-hosted **SearXNG** (open source, aggregates engines), or a paid search API such as Brave Search or Tavily **(verify pricing)** |

Build the indexed crawl first. It reuses the whole phase-8 pipeline. Live tools come after, through `tsp.ai` tool calling.

## Architecture

```
seed URLs / sitemaps / customer request
        │
        ▼
crawl.plan job (tsp.jobs) ── robots.txt, sitemap, URL filters, per-org limits
        │  enqueues one crawl.fetch job per URL (frontier lives in Postgres at stage 0)
        ▼
crawl.fetch job
  1. SSRF guard: resolve DNS, refuse private, loopback and link-local addresses (169.254.169.254!)
  2. per-domain rate limit (tsp.cache), conditional GET (ETag / Last-Modified)
  3. HTTP fetch + Trafilatura → enough text? done
  4. else render in a browser (Crawl4AI) on the render worker
  5. PDFs and Office files → Docling
        │  Markdown + metadata (url, title, fetched_at, content hash)
        ▼
S3: crawl/<org>/<hash>.md (raw pages are kept so you can re-chunk and re-embed without re-crawling)
        │
        ▼
vector.ingest job (phase 8.4): chunk → embed → upsert, skipped when the content hash has not changed
```

### Where it runs on AWS

| Workload | Stage 0 | Later |
|---|---|---|
| HTTP fetch + Trafilatura | inside the existing jobs worker on the host (light, I/O-bound) | separate `tsp-jobs worker` service |
| Browser rendering | **not on the 2 GB host.** One-off ECS **Fargate Spot** task (Graviton, 1 vCPU / 2–4 GB), started when the render queue has work and stopped when it is empty | an always-on small service or an autoscaled Fargate service on queue depth; SQS instead of Postgres for the queue above ~1M URLs a day |
| Scheduling | `tsp.jobs` schedules (`crawl.refresh` daily or weekly per source) | EventBridge Scheduler |
| Storage | S3 bucket (private, lifecycle to Infrequent Access after 30 days) | same |

Network: the stack has no NAT gateway on purpose. Run the render task in a public subnet with a public IP (about $3.6 per month per always-on task, prorated for Spot runs) and a security group with **no inbound** rules. Give it an IAM role that can only write `crawl/*` in the bucket and read its queue, never the database or secrets, so a hostile page that escapes the browser reaches nothing useful.

## Cost estimates

Assumptions: an average page becomes ~1,500 tokens of text (about 4 chunks); 70% of pages need only HTTP and 30% need a browser; an HTTP fetch takes ~0.2 vCPU-seconds and a browser render ~3–4 vCPU-seconds; Fargate Graviton on demand ≈ $0.04 per hour for 1 vCPU and 2 GB, and Spot is about 70% cheaper **(verify)**; Titan Text Embeddings V2 ≈ $0.02 per 1M tokens **(verify)**.

| Pages crawled / month | 10k | 100k | 1M | 10M |
|---|---|---|---|---|
| Compute: HTTP on the host, browser on Fargate Spot | ~$0 | ~$1–2 | ~$5–15 | ~$50–150 |
| Public IPv4 for render tasks | <$1 | ~$1 | ~$2–4 | ~$4–15 |
| S3 storage for Markdown (~20 KB/page, cumulative) | <$0.01 | ~$0.05 | ~$0.50 | ~$5 |
| Embeddings (first crawl; recrawls only re-embed changed pages) | ~$0.30 | ~$3 | ~$30 | ~$300 |
| Vectors stored (×4 chunks) | 40k | 400k | 4M | 40M |
| Vector store, see [17](17-vector-search.md#estimated-monthly-cost-by-tier) | ~$0 (pgvector on host) | ~$0–40 | ~$100–400 (RDS) or ~$20 (S3 Vectors, per-org) | S3 Vectors per-org ~$60–150; one shared index or RDS costs far more |
| Live search API (if used), per 1,000 searches | — | — | — | ~$0 with self-hosted SearXNG; a few dollars per 1,000 with paid APIs **(verify)** |
| **Rough total** | **~$1** | **~$5–50** | **~$60–450** | **~$400–650** with per-org S3 Vectors |

What can blow this up: **residential proxies** and anti-bot unblocking services, billed per GB or per request, often cost more than everything above combined. Treat a site that blocks you as a "no", not a challenge. Also watch LLM-based extraction (Crawl4AI's LLM strategy calls a model for every page); use it only for the pages that need structured fields.

## Guardrails (required before launch)

1. **SSRF.** Any URL a customer can submit is an attack on your network. Resolve DNS yourself, refuse private, loopback, link-local and metadata addresses (including after redirects), allow only `http` and `https`, and cap size, redirects and time. Require IMDSv2 on the host. This is the most important item on this list.
2. **Respect robots.txt and rate limits.** Honour `robots.txt` and `Crawl-delay`, send an identifying user agent with a contact URL, default to one request per second per domain, and back off on 429 and 503.
3. **Terms and copyright.** Index only what the customer has the right to use: their own sites, open-licensed content, or sources whose terms allow it. Store the source URL and fetch time with every chunk, show citations, and honour removal requests. Update the [acceptable use policy](../legal/acceptable-use.md) so customers promise they have the right to crawl what they submit.
4. **Personal data.** Scraped pages can contain names, emails and phone numbers. That is personal data under India's DPDP Act and GDPR. Keep only the text the feature needs, apply the same deletion and export paths as other customer data (`tsp.compliance`), and list any hosted crawl or search API in [sub-processors](../legal/subprocessors.md).
5. **Prompt injection.** Web text is hostile input. Pass it to the model as quoted data, never as instructions, and do not let an answer built from web content trigger actions without a separate check.
6. **Per-organization limits.** Plans set pages per month, crawl depth and refresh frequency. Crawls draw credits through `tsp.metering` (kinds `crawl.page`, `crawl.render`), and the `crawl` flag is a kill switch.
