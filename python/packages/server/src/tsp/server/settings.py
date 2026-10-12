from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import quote

from pydantic import BaseModel, Field


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _database_url(e: Mapping[str, str]) -> str | None:
    """``DATABASE_URL``, or one built from ``DB_HOST/PORT/NAME/USER/PASSWORD``.

    ECS injects the credentials from Secrets Manager as separate variables (it cannot compose a
    URL), and the same variables work for the host container, RDS and Aurora.
    """
    if e.get("DATABASE_URL"):
        return e["DATABASE_URL"]
    if not (e.get("DB_HOST") and e.get("DB_USER") and e.get("DB_PASSWORD")):
        return None
    return (
        f"postgresql://{quote(e['DB_USER'], safe='')}:{quote(e['DB_PASSWORD'], safe='')}"
        f"@{e['DB_HOST']}:{e.get('DB_PORT', '5432')}/{e.get('DB_NAME', 'app')}"
    )


class Settings(BaseModel):
    """Everything the app needs, read from environment variables (ECS injects them from SSM)."""

    env: str = "dev"
    api_prefix: str = "/api"
    # When set, every request except /health must carry `X-Origin-Verify: <value>` (set by CloudFront).
    origin_verify: str | None = Field(default=None, repr=False)
    database_url: str | None = Field(default=None, repr=False)
    cache_url: str | None = Field(default=None, repr=False)
    dynamodb_table: str | None = None
    dynamodb_endpoint: str | None = None
    aws_region: str | None = None
    auth_issuer: str | None = None
    cognito_user_pool_id: str | None = None
    export_bucket: str | None = None
    legal_manifest: str | None = None
    auth_client_ids: list[str] = []
    cors_origins: list[str] = []

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if environ is None else environ
        return cls(
            env=e.get("TSP_ENV", "dev"),
            api_prefix=e.get("TSP_API_PREFIX", "/api"),
            origin_verify=e.get("TSP_ORIGIN_VERIFY") or None,
            database_url=_database_url(e),
            cache_url=e.get("CACHE_URL") or None,
            dynamodb_table=e.get("DYNAMODB_TABLE") or None,
            dynamodb_endpoint=e.get("DYNAMODB_ENDPOINT") or None,
            aws_region=e.get("AWS_REGION") or e.get("AWS_DEFAULT_REGION") or None,
            auth_issuer=e.get("AUTH_ISSUER") or None,
            cognito_user_pool_id=e.get("COGNITO_USER_POOL_ID") or None,
            export_bucket=e.get("EXPORT_BUCKET") or None,
            legal_manifest=e.get("TSP_LEGAL_MANIFEST") or None,
            auth_client_ids=_split(e.get("AUTH_CLIENT_IDS")),
            cors_origins=_split(e.get("CORS_ORIGINS")),
        )
