# TheStartupProject

A cloneable kit of building blocks for starting a company's software: pick the modules you need and wire them into your app. Python modules install under one `tsp.*` namespace; JavaScript packages sit beside them.

| Module | Packages | Docs |
|---|---|---|
| Multi-provider LLM client, router, verified model catalog, OpenAI-compatible server | tsp-ai | [docs/modules/ai.md](docs/modules/ai.md) |
| FastAPI app factory, Cognito JWT verification, async SQL and migrations, Valkey helpers, DynamoDB helper | tsp-server, tsp-auth, tsp-db, tsp-cache, tsp-nosql | [docs/aws/13-library-modules.md](docs/aws/13-library-modules.md) |
| AWS CDK constructs: edge, server, auth, SQL, NoSQL, cache, CI/CD | tsp-infra | [docs/aws/README.md](docs/aws/README.md) |
| Users, organizations, invites, API keys; staff console with its own MFA login | tsp-accounts, tsp-admin | [docs/modules/accounts-admin.md](docs/modules/accounts-admin.md) |
| Append-only audit log; consent, data export and deletion, legal document rendering | tsp-audit, tsp-compliance | [docs/modules/compliance.md](docs/modules/compliance.md) |
| Razorpay and Stripe, GST invoices, credits ledger, entitlements | tsp-payments | [docs/modules/payments.md](docs/modules/payments.md) |
| Postgres job queue and schedules; email, push, SMS, in-app notifications | tsp-jobs, tsp-notify | [docs/modules/jobs-notify.md](docs/modules/jobs-notify.md) |
| Waitlist, first-party events, attribution, referrals, metrics | tsp-growth | [docs/modules/growth.md](docs/modules/growth.md) |
| Feature flags and kill switches, direct-to-S3 uploads, usage metering and the AI gateway | tsp-flags, tsp-storage, tsp-metering | [docs/modules/product-ops.md](docs/modules/product-ops.md) |
| Vector and hybrid search, document ingestion | `tsp-vector` | [docs/modules/vector.md](docs/modules/vector.md) |
| Web app (React) and landing site (Astro) | `js/packages/web-app`, `js/packages/landing` | [js/README.md](js/README.md) |
| Mobile starter (Expo, source only) | `js/packages/mobile` | [js/README.md](js/README.md) |

Planned work is in [docs/roadmap.md](docs/roadmap.md).

## Quick start

```bash
git clone <this repo> TheStartupProject
cd TheStartupProject/python
uv sync --all-extras
cp ../.env.example ../.env       # add the provider keys you have
uv run tsp-ai verify --env-file ../.env --free-only
```

Use it from another project:

```bash
uv add ../TheStartupProject/python/packages/ai      # or: pip install -e <path>
```

```python
from tsp.ai import LLMClient

client = LLMClient.from_env(".env", catalog="~/.cache/tsp-ai/catalog.json")
resp = await client.chat.create(model="auto", messages=[{"role": "user", "content": "Hello"}])
```

More: [Getting started](docs/getting-started.md) · [Startup guide](docs/startup/README.md) · [Legal templates](docs/legal/README.md) · [Docs index](docs/README.md)

## Layout

```
python/            uv workspace: packages/<module>/src/tsp/<module>
js/                web app, landing site, mobile starter
docs/              guides per module, hosting
.github/           CI
```

## License

MIT
