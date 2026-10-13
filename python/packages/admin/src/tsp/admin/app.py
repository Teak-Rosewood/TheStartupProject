from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from tsp.accounts import Accounts
from tsp.auth import CognitoVerifier
from tsp.compliance import Compliance
from tsp.db import normalize_url
from tsp.flags import Flags
from tsp.payments import Payments

from .actions import make_finance_view, make_metrics_view, make_view
from .authz import RoleAuthorization
from .oidc import AdminAuth, AdminSettings
from .views import VIEWS

__all__ = ["AdminSettings", "mount_admin"]


def mount_admin(
    app: FastAPI,
    *,
    settings: AdminSettings,
    database_url: str,
    accounts: Accounts,
    compliance: Compliance | None = None,
    payments: Payments | None = None,
    flags: Flags | None = None,
    extra_views: Sequence[type] = (),
    http: httpx.AsyncClient | None = None,
    engine: AsyncEngine | None = None,
) -> Admin:
    """Mount the console at ``settings.base_path``. Call after the other routes are added.

    ``extra_views`` adds read-only model views from other modules (for example ``tsp.cms.admin.CMS_VIEWS``);
    their identities need a grant in ``roles.GRANTS`` or only superadmins see them.

    Uses its own small engine (no pool: staff traffic is tiny) so it works whatever the API's
    lifespan does. The OIDC endpoints are mounted first so they win over the admin app.
    """
    engine = engine or create_async_engine(normalize_url(database_url), poolclass=NullPool)
    verifier = CognitoVerifier(settings.issuer, [settings.client_id], http=http)
    auth = AdminAuth(settings, verifier, http)
    app.mount(f"{settings.base_path}/oidc", auth.oidc_app())
    admin = Admin(
        app,
        engine,
        base_url=settings.base_path,
        title="Staff console",
        authentication_backend=auth,
        authorization_backend=RoleAuthorization(engine),
        templates_dir=str(Path(__file__).parent / "templates"),
    )
    for view in (*VIEWS, *extra_views):
        admin.add_view(view)
    admin.add_base_view(make_view(engine, accounts, compliance, payments, flags))
    admin.add_base_view(make_finance_view(engine))
    admin.add_base_view(make_metrics_view(engine))
    return admin
