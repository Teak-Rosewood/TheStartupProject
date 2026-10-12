# 13 · Library modules

| Package (import) | Kind | Purpose |
|---|---|---|
| `tsp-infra` (`tsp.infra`) | infra | CDK constructs and stacks that build this whole stack from one `EnvConfig` |
| `tsp-server` (`tsp.server`) | runtime | `create_app(settings)` FastAPI factory: health and readiness, origin check, CORS, JSON logs, error envelope; FastAPI dependencies `current_user`, `require_groups`, `db_session`, `get_cache`, `get_table` |
| `tsp-auth` (`tsp.auth`) | runtime | verify Cognito JWTs (JWKS cache, key rotation); framework-free |
| `tsp-db` (`tsp.db`) | runtime | async SQLAlchemy engine and session, Alembic helper, `tsp-db migrate` |
| `tsp-nosql` (`tsp.nosql`) | runtime | typed DynamoDB single-table helper |
| `tsp-cache` (`tsp.cache`) | runtime | Valkey/Redis client, `@cached`, rate limiter, lock |
| `tsp-accounts` (`tsp.accounts`) | runtime | users, organizations, roles, invites, customer API keys |
| `tsp-admin` (`tsp.admin`) | runtime | staff console on `/api/admin` with its own MFA login |
| `tsp-growth` (`tsp.growth`) | runtime | waitlist (double opt-in), first-party events, attribution, referrals, business metrics; see [growth](../modules/growth.md) |
| `tsp-jobs` (`tsp.jobs`) | runtime | Postgres-backed background jobs and schedules |
| `tsp-notify` (`tsp.notify`) | runtime | email (SES), push (FCM), SMS (SNS), in-app; see [jobs and notifications](../modules/jobs-notify.md) |
| `tsp-payments` (`tsp.payments`) | runtime | Razorpay/Stripe, GST invoices, credits ledger, entitlements; see [payments](../modules/payments.md) |
| `tsp-audit` (`tsp.audit`) | runtime | append-only audit log (database trigger) |
| `tsp-compliance` (`tsp.compliance`) | runtime | consent by document version, data export and deletion, legal rendering; see [compliance](../modules/compliance.md) |
| `tsp-flags` (`tsp.flags`) | runtime | feature flags, rollouts, kill switches; see [product operations](../modules/product-ops.md) |
| `tsp-storage` (`tsp.storage`) | runtime | presigned uploads with size/type limits, per-org prefixes and quotas |
| `tsp-metering` (`tsp.metering`) | runtime | usage records and token-to-credit pricing |
| `tsp-vector` (`tsp.vector`) | runtime | pgvector / S3 Vectors stores, hybrid search, chunking, ingestion jobs; see [vector search](../modules/vector.md) |
| `tsp-crawl` (`tsp.crawl`) | runtime | SSRF-safe HTTP fetching, robots.txt, sitemaps, per-domain limits, Markdown extraction; see [web ingestion plan](../plans/08b-crawl.md) |
| `tsp-sources` (`tsp.sources`) | runtime | external data sources and connectors with strategy fallback, cursors and health checks; see [sources](../modules/sources.md) |
| `tsp-trends` (`tsp.trends`) | runtime | interest signals scored daily into an editor's candidate list; see [trends](../modules/trends.md) |
| `tsp-paywall` (`tsp.paywall`) | runtime | metered and hard paywalls, signed anonymous meter, shared-network-safe device signal, verified crawlers, gifts, credits; see [paywall](../modules/paywall.md) |
| `tsp-ai` (`tsp.ai`) | runtime | multi-provider LLM client and server (already built) |

Runtime modules take explicit URLs and parameters and know nothing about each other, so you can use any one alone. Only `tsp.server` reads environment settings and wires the others together. The deployable sample service lives in `python/apps/api`, and the CDK app in `python/apps/infra`.

Each module follows the layout in [getting started](../getting-started.md): `python/packages/<name>/src/tsp/<name>/`, no `src/tsp/__init__.py`.
