from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI
from tsp.accounts import Accounts, User
from tsp.accounts import api as accounts_api
from tsp.admin import AdminSettings, mount_admin
from tsp.ai import LLMClient
from tsp.cache import NullCache
from tsp.compliance import (
    CognitoIdentity,
    Compliance,
    LocalExportStore,
    Manifest,
    S3ExportStore,
    build_router,
    make_guards,
)
from tsp.flags import Flags
from tsp.flags import build_router as flags_router
from tsp.growth import api as growth_api
from tsp.jobs import worker_lifespan
from tsp.metering import Rates
from tsp.metering import build_router as metering_router
from tsp.notify import api as notify_api
from tsp.notify import register_with_compliance as notify_register
from tsp.payments import api as payments_api
from tsp.paywall import Paywall
from tsp.paywall import build_router as paywall_router
from tsp.paywall import register_jobs as paywall_jobs
from tsp.paywall import register_with_compliance as paywall_compliance
from tsp.server import (
    Settings,
    configure_logging,
    create_app,
    current_user,
    db_session,
    get_cache,
)
from tsp.storage import LocalObjectStore, S3ObjectStore, UploadPolicy, Uploads
from tsp.storage import build_router as storage_router

from .ai import AiGateway, catalog_lifespan, register_catalog_jobs
from .ai import build_router as build_ai_router
from .billing import build_payments, plans_path, sync_plans_on_start
from .growth import build_leads, optional_sub, set_first_touch
from .notifications import Hooks, build_notifier, build_registry
from .routes import erase_notes, export_notes, gated, router


def migrate_on_start(settings: Settings) -> None:
    """Apply migrations before serving. A failure crashes the container, so ECS's deployment
    circuit breaker rolls the release back and the previous version keeps running."""
    from tsp.db import upgrade

    if os.environ.get("TSP_MIGRATE_ON_START") == "1" and settings.database_url:
        modules = [m for m in os.environ.get("TSP_MIGRATE_MODULES", "").split(",") if m]
        upgrade(
            settings.database_url, os.environ.get("TSP_MIGRATIONS", "migrations"), modules=modules
        )


def build_compliance(settings: Settings, **hooks: Any) -> Compliance:
    manifest_path = Path(settings.legal_manifest or "/app/legal/documents.json")
    manifest = Manifest.load(manifest_path) if manifest_path.exists() else None
    store = (
        S3ExportStore(settings.export_bucket, settings.aws_region)
        if settings.export_bucket
        else (LocalExportStore("/tmp/tsp-exports") if settings.env == "dev" else None)
    )
    identity = (
        CognitoIdentity(settings.cognito_user_pool_id, settings.aws_region)
        if settings.cognito_user_pool_id
        else None
    )
    c = Compliance(manifest, store=store, identity=identity, **hooks)
    c.register_exporter("notes", export_notes)
    c.register_eraser("notes", erase_notes)
    return c


def build_uploads(settings: Settings) -> Uploads:
    bucket = os.environ.get("UPLOAD_BUCKET")
    policy = UploadPolicy(
        max_bytes=int(os.environ.get("UPLOAD_MAX_MB", "10")) * 1024 * 1024,
        org_quota_bytes=int(os.environ.get("UPLOAD_ORG_QUOTA_MB", "1024")) * 1024 * 1024,
    )
    store = (
        S3ObjectStore(bucket, settings.aws_region)
        if bucket
        else LocalObjectStore("/tmp/tsp-uploads")
    )
    return Uploads(store, policy)


def build_rates() -> Rates:
    return Rates(
        in_per_1k=float(os.environ.get("AI_CREDITS_PER_1K_IN", "1")),
        out_per_1k=float(os.environ.get("AI_CREDITS_PER_1K_OUT", "3")),
        min_charge=int(os.environ.get("AI_MIN_CREDITS_PER_REQUEST", "1")),
    )


def build_llm() -> LLMClient | None:
    """The model client, if any provider key is configured. TSP_AI_CATALOG points at a `tsp-ai verify` catalog."""
    catalog = os.environ.get("TSP_AI_CATALOG")
    if catalog and not Path(catalog).exists():
        raise FileNotFoundError(
            f"TSP_AI_CATALOG={catalog} does not exist; run `tsp-ai verify` or unset it"
        )
    client = LLMClient.from_env(catalog=catalog)
    return client if client._router._providers else None


@asynccontextmanager
async def llm_lifespan_cm(client: LLMClient) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await client.close()


def llm_lifespan(client: LLMClient) -> Any:
    return lambda _app: llm_lifespan_cm(client)


def paywall_lifespan(pw: Paywall) -> Any:
    """Use the app's cache for the shared-network detection once the app has created it."""

    @asynccontextmanager
    async def run(app: Any) -> AsyncIterator[None]:
        pw.cache = app.state.cache
        yield

    return run


def build_admin_settings(env: dict[str, str] | None = None) -> AdminSettings | None:
    """Staff console settings from the environment; None (console disabled) if not all are set."""
    e = dict(os.environ) if env is None else env
    keys = (
        "ADMIN_ISSUER",
        "ADMIN_CLIENT_ID",
        "ADMIN_CLIENT_SECRET",
        "ADMIN_DOMAIN",
        "ADMIN_EXTERNAL_URL",
    )
    if not all(e.get(k) for k in keys):
        return None
    return AdminSettings(
        issuer=e["ADMIN_ISSUER"],
        client_id=e["ADMIN_CLIENT_ID"],
        client_secret=e["ADMIN_CLIENT_SECRET"],
        domain=e["ADMIN_DOMAIN"],
        external_url=e["ADMIN_EXTERNAL_URL"],
        allowed_cidrs=[c.strip() for c in e.get("ADMIN_ALLOWED_CIDRS", "").split(",") if c.strip()],
    )


def build_app() -> FastAPI:
    settings = Settings.from_env()
    notifier = build_notifier()
    hooks = Hooks(notifier, notifier.base_url)
    compliance = build_compliance(settings, on_deletion_requested=hooks.deletion_requested)
    accounts = Accounts(invite_hook=hooks.invite, user_created_hook=hooks.user_created)
    accounts.register_with_compliance(compliance)
    payments = build_payments(on_receipt=hooks.receipt, on_payment_failed=hooks.payment_failed)
    llm = build_llm()
    registry = build_registry(notifier, compliance=compliance, payments=payments)
    if llm is not None:
        register_catalog_jobs(
            registry, llm, free_only=os.environ.get("AI_VERIFY_FREE_ONLY", "1") == "1"
        )
    flags = Flags()
    uploads = build_uploads(settings)
    uploads.register_with_compliance(compliance)
    # Stage 0 runs jobs inside the API process (no memory for a second container); set
    # TSP_JOBS_INLINE=0 when you run `tsp-jobs worker` separately.
    inline = os.environ.get("TSP_JOBS_INLINE", "1") == "1"
    background: list[Any] = [worker_lifespan(registry)] if inline else []
    if llm is not None:
        background.insert(0, llm_lifespan(llm))
        background.append(catalog_lifespan(llm))
    paywall = (
        Paywall(os.environ["PAYWALL_SECRET"], NullCache())
        if os.environ.get("PAYWALL_SECRET")
        else None
    )
    if paywall is not None:
        paywall_compliance(compliance)
        paywall_jobs(registry, paywall)
        background.insert(0, paywall_lifespan(paywall))
    app = create_app(settings, background=background)
    app.state.compliance, app.state.accounts, app.state.payments, app.state.notifier = (
        compliance,
        accounts,
        payments,
        notifier,
    )

    async def email_of(session: Any, sub: str) -> str | None:
        user = await session.get(User, sub)
        return user.email if user else None

    notify_register(compliance, email_of)
    leads, captcha = build_leads(notifier)
    consent, active = make_guards(compliance, current_user, db_session)
    app.include_router(
        build_router(compliance, current_user, db_session), prefix=settings.api_prefix
    )
    api = accounts_api.build(accounts, current_user, db_session)
    app.include_router(api.router, prefix=settings.api_prefix, dependencies=[Depends(active)])
    app.include_router(
        notify_api.build_router(notifier, api.account, db_session), prefix=settings.api_prefix
    )
    app.include_router(
        growth_api.build_router(
            leads,
            captcha,
            get_cache,
            api.account,
            optional_sub,
            db_session,
            set_attribution=set_first_touch,
        ),
        prefix=settings.api_prefix,
    )
    if payments:

        async def managed_orgs(session, sub):  # billing is managed by owners and admins
            return [
                o.id
                for o, role in await accounts.list_orgs(session, sub)
                if role in ("owner", "admin")
            ]

        payments.register_with_compliance(compliance, managed_orgs)
        # The webhook route has no user; the rest need a signed-in account and an org role.
        app.include_router(
            payments_api.build_router(payments, api.account, api.org_member, db_session),
            prefix=settings.api_prefix,
        )
    app.include_router(flags_router(flags, api.account, db_session), prefix=settings.api_prefix)
    app.include_router(
        storage_router(uploads, api.account, api.org_member, db_session), prefix=settings.api_prefix
    )
    app.include_router(metering_router(api.org_member, db_session), prefix=settings.api_prefix)
    if llm is not None:
        gateway = AiGateway(
            llm,
            accounts,
            flags,
            payments,
            rates=build_rates(),
            default_rpm=int(os.environ.get("AI_DEFAULT_RPM", "20")),
        )
        app.state.ai_gateway = gateway
        app.include_router(build_ai_router(gateway), prefix=settings.api_prefix)
    if paywall is not None:
        app.state.paywall = paywall
        app.include_router(
            paywall_router(
                paywall, api.account, optional_sub, db_session, secure_cookie=settings.env != "dev"
            ),
            prefix=settings.api_prefix,
        )
    app.state.flags = flags
    # Demo routes: refuse accounts pending deletion; writes also need accepted terms and privacy.
    app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(active)])
    app.include_router(
        gated,
        prefix=settings.api_prefix,
        dependencies=[Depends(active), Depends(consent("terms", "privacy"))],
    )
    admin = build_admin_settings()
    if admin and settings.database_url:
        mount_admin(
            app,
            settings=admin,
            database_url=settings.database_url,
            accounts=accounts,
            compliance=compliance,
            payments=payments,
            flags=flags,
        )
    return app


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    migrate_on_start(settings)
    payments, plans = build_payments(), plans_path()
    if payments and plans and settings.database_url:
        import asyncio

        asyncio.run(sync_plans_on_start(payments, settings.database_url, plans))
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
