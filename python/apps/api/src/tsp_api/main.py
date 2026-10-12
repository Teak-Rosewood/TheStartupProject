from __future__ import annotations

import uvicorn
from tsp.server import Settings, configure_logging, create_app

from .routes import router


def migrate_on_start(settings: Settings) -> None:
    """Apply migrations before serving. A failure crashes the container, so ECS's deployment
    circuit breaker rolls the release back and the previous version keeps running."""
    import os

    from tsp.db import upgrade

    if os.environ.get("TSP_MIGRATE_ON_START") == "1" and settings.database_url:
        upgrade(settings.database_url, os.environ.get("TSP_MIGRATIONS", "migrations"))


def build_app():
    settings = Settings.from_env()
    app = create_app(settings)
    app.include_router(router, prefix=settings.api_prefix)
    return app


def main() -> None:
    import os

    configure_logging()
    migrate_on_start(Settings.from_env())
    uvicorn.run(
        build_app(),
        host=os.environ.get(
            "TSP_HOST", "0.0.0.0"
        ),  # reachable only via the CloudFront-restricted SG
        port=int(os.environ.get("TSP_PORT", "8000")),
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
