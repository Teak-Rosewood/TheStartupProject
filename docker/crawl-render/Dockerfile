# Browser render worker (docs/modules/crawl.md). Build context: python/
#   docker build -f ../docker/crawl-render/Dockerfile python/        (linux/arm64 for Graviton Fargate)
# Not built in CI yet: Chromium and Crawl4AI versions need a first real build to pin.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable --package tsp-crawl --extra browser

FROM python:3.13-slim
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
# Chromium and its system libraries. The worker holds no database access and no AWS permissions.
RUN playwright install --with-deps chromium && chmod -R a+rX /opt/browsers
USER app
CMD ["python", "-m", "tsp.crawl.render_worker"]
