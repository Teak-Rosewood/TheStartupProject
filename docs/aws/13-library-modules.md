# 13 · Library modules

| Package (import) | Kind | Purpose |
|---|---|---|
| `tsp-infra` (`tsp.infra`) | infra | CDK constructs and stacks that build this whole stack from one `EnvConfig` |
| `tsp-server` (`tsp.server`) | runtime | `create_app(settings)` FastAPI factory: health and readiness, origin check, CORS, JSON logs, error envelope; FastAPI dependencies `current_user`, `require_groups`, `db_session`, `get_cache`, `get_table` |
| `tsp-auth` (`tsp.auth`) | runtime | verify Cognito JWTs (JWKS cache, key rotation); framework-free |
| `tsp-db` (`tsp.db`) | runtime | async SQLAlchemy engine and session, Alembic helper, `tsp-db migrate` |
| `tsp-nosql` (`tsp.nosql`) | runtime | typed DynamoDB single-table helper |
| `tsp-cache` (`tsp.cache`) | runtime | Valkey/Redis client, `@cached`, rate limiter, lock |
| `tsp-ai` (`tsp.ai`) | runtime | multi-provider LLM client and server (already built) |

Runtime modules take explicit URLs and parameters and know nothing about each other, so you can use any one alone. Only `tsp.server` reads environment settings and wires the others together. The deployable sample service lives in `python/apps/api`, and the CDK app in `python/apps/infra`.

Each module follows the layout in [getting started](../getting-started.md): `python/packages/<name>/src/tsp/<name>/`, no `src/tsp/__init__.py`.
