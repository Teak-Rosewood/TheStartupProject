"""Row-level security helpers: one org per transaction, enforced by Postgres.

A table protected with :func:`org_policy_sql` shows a session only the rows of the organization set
with :func:`scope_org` in the current transaction. Maintenance work that must see every organization
(deadline sweeps, account erasure, the staff console) says so with :func:`scope_all_orgs`.

This is defence in depth on top of the ``org_id`` filters every query already has. Postgres does
not apply it to superusers or roles with ``BYPASSRLS``, so run the app as an ordinary role.
Settings are transaction-local (``set_config(..., true)``): call the helper again after a commit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

ORG_SETTING = "app.org_id"
SCOPE_SETTING = "app.org_scope"


async def scope_org(session: AsyncSession, org_id: str) -> None:
    """Rows of ``org_id`` only, for the rest of this transaction."""
    await session.execute(
        text("SELECT set_config(:k, :o, true), set_config(:s, '', true)"),
        {"k": ORG_SETTING, "o": org_id, "s": SCOPE_SETTING},
    )


async def scope_all_orgs(session: AsyncSession) -> None:
    """Every organization's rows, for the rest of this transaction. Only for maintenance code."""
    await session.execute(text("SELECT set_config(:s, 'all', true)"), {"s": SCOPE_SETTING})


def scope_engine_to_all_orgs(engine: AsyncEngine) -> None:
    """Make every transaction on ``engine`` see all organizations (the staff console's engine)."""

    @event.listens_for(engine.sync_engine, "begin")
    def _all(conn: Any) -> None:
        conn.exec_driver_sql(f"SELECT set_config('{SCOPE_SETTING}', 'all', true)")


def org_policy_sql(table: str, using: str | None = None) -> list[str]:
    """Statements for a migration that turn on row-level security for ``table``.

    By default rows match on the table's own ``org_id``. Pass ``using`` for a child table without
    one, for example ``"EXISTS (SELECT 1 FROM parent p WHERE p.id = parent_id)"``, which inherits the
    parent's policy."""
    match = using or f"org_id = current_setting('{ORG_SETTING}', true)"
    rule = f"(current_setting('{SCOPE_SETTING}', true) = 'all' OR {match})"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY org_isolation ON {table} USING {rule} WITH CHECK {rule}",
    ]


def drop_org_policy_sql(table: str) -> list[str]:
    return [
        f"DROP POLICY IF EXISTS org_isolation ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]
