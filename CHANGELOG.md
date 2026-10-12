# Changelog
Format: [Keep a Changelog](https://keepachangelog.com). Versioning: [SemVer](https://semver.org).

## Unreleased
### Added
- `tsp.ai`: multi-provider LLM client, router, verified model catalog, OpenAI-compatible server.
- AWS backend stack: `tsp.infra`, `tsp.server`, `tsp.auth`, `tsp.db`, `tsp.cache`, `tsp.nosql`, CI/CD workflows, runbooks (`docs/aws`).
- Legal templates and startup guides (`docs/legal`, `docs/startup`).
- Accounts, admin console, payments (Razorpay/Stripe, GST invoices), jobs, notifications, growth, flags, storage, metering, AI gateway, landing site, web app, Expo starter, and the infra to run them (`docs/modules`).
- `tsp.pipeline`: durable multi-step AI workflows on `tsp.jobs` (checkpoints, retries, per-run budgets, traces and costs, human steps, kill switch, versioned prompt registry and `tsp-pipeline prompts` CLI), plus `tsp.ai.structured` (validated JSON output with one repair retry).
- Vector search design and costs on AWS (`docs/aws/17-vector-search.md`), roadmap with phase 8 and 8b (`docs/roadmap.md`), web ingestion plan (`docs/aws/18-web-ingestion.md`), and the October 2026 project review (`docs/review-2026-10.md`).
