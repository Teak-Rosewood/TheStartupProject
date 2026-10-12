# Build context: python/   (docker build -f apps/api/Dockerfile python/)
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api
RUN uv sync --frozen --no-dev --no-editable --package tsp-api-app

FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY apps/api/migrations /app/migrations
# Rendered legal documents (docs/legal -> documents.json); CI renders them before the build.
COPY apps/api/legal /app/legal
COPY apps/api/plans.toml /app/plans.toml
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 TSP_MIGRATIONS=/app/migrations TSP_MIGRATE_MODULES=tsp.audit,tsp.compliance,tsp.accounts,tsp.payments,tsp.jobs,tsp.notify,tsp.growth,tsp.flags,tsp.storage,tsp.metering,tsp.vector,tsp.paywall TSP_LEGAL_MANIFEST=/app/legal/documents.json TSP_PLANS_FILE=/app/plans.toml
USER app
EXPOSE 8000
CMD ["tsp-api"]
