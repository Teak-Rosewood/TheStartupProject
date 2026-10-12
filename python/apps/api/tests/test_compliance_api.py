import asyncio
import os
from pathlib import Path

import pytest
from api_support import ALL_TABLES
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.auth import Claims
from tsp.compliance import Compliance, LocalExportStore, build_router, make_guards
from tsp.compliance.render import render_legal
from tsp.db import Database, upgrade
from tsp.server import Settings, create_app, current_user, db_session
from tsp_api.routes import erase_notes, export_notes, gated, router

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
CACHE = os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0")
LEGAL = Path(__file__).parents[4] / "docs" / "legal"
MIGRATIONS = Path(__file__).parents[1] / "migrations"
SUB = "22222222-2222-2222-2222-222222222222"


class FakeIdentity:
    def __init__(self):
        self.calls = []

    async def sign_out(self, sub):
        self.calls.append(("sign_out", sub))

    async def disable(self, sub):
        self.calls.append(("disable", sub))

    async def delete(self, sub):
        self.calls.append(("delete", sub))


@pytest.fixture
def ctx(tmp_path):
    async def setup():
        db = Database(DB)
        try:
            await db.ping()
        except Exception:
            pytest.skip("local services not running (docker compose up -d)")
        async with db.session() as s:
            await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
            await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
        await db.dispose()
        await asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=["tsp.audit", "tsp.compliance"])

    asyncio.run(setup())
    manifest, _ = render_legal(LEGAL / "company.example.toml", LEGAL, tmp_path / "legal")
    c = Compliance(manifest, store=LocalExportStore(tmp_path / "exports"), identity=FakeIdentity())
    c.register_exporter("notes", export_notes)
    c.register_eraser("notes", erase_notes)
    app = create_app(Settings(database_url=DB, cache_url=CACHE))
    consent, active = make_guards(c, current_user, db_session)
    app.include_router(build_router(c, current_user, db_session), prefix="/api")
    app.include_router(router, prefix="/api", dependencies=[Depends(active)])
    app.include_router(
        gated, prefix="/api", dependencies=[Depends(active), Depends(consent("terms", "privacy"))]
    )
    app.dependency_overrides[current_user] = lambda: Claims(sub=SUB, token_use="access")
    with TestClient(app) as client:
        client.c, client.manifest = c, manifest
        yield client

    async def cleanup():
        db = Database(DB)
        async with db.session() as s:
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
            await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
            await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
        await db.dispose()

    asyncio.run(cleanup())


def accept_all(client):
    versions = {d.id: d.version for d in client.manifest.required()}
    r = client.post("/api/me/consents", json={"documents": versions})
    assert r.status_code == 201, r.text


def test_public_legal_endpoints(ctx):
    docs = ctx.get("/api/legal/documents").json()["documents"]
    assert {d["id"] for d in docs} >= {"terms", "privacy"} and "sha256" not in docs[0]
    cfg = ctx.get("/api/legal/config").json()
    assert cfg["consent_required"] == ["terms", "privacy"]
    assert {c["id"] for c in cfg["cookie_categories"]} == {
        "necessary",
        "preferences",
        "analytics",
        "marketing",
    }


def test_writes_are_blocked_until_the_current_documents_are_accepted(ctx):
    r = ctx.post("/api/notes", json={"text": "hi"})
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["type"] == "consent_required" and {d["id"] for d in err["documents"]} == {
        "terms",
        "privacy",
    }
    assert ctx.get("/api/me/consents").json()["up_to_date"] is False
    accept_all(ctx)
    assert ctx.get("/api/me/consents").json()["up_to_date"] is True
    assert ctx.post("/api/notes", json={"text": "hi"}).status_code == 201


def test_accepting_an_old_version_is_a_conflict(ctx):
    r = ctx.post("/api/me/consents", json={"documents": {"terms": "stale"}})
    assert r.status_code == 409 and r.json()["error"]["type"] == "consent_version_mismatch"


def test_export_contains_my_notes_and_consents(ctx):
    accept_all(ctx)
    ctx.post("/api/notes", json={"text": "remember me"})
    r = ctx.post("/api/me/export")
    assert r.status_code == 201 and r.json()["status"] == "completed"
    again = ctx.get(f"/api/me/export/{r.json()['id']}").json()
    assert again["status"] == "completed" and again["url"].startswith("file://")
    assert ctx.get("/api/me/export/nope").status_code == 404


def test_deleting_an_account_blocks_it_then_erases_after_grace(ctx):
    accept_all(ctx)
    ctx.post("/api/notes", json={"text": "bye"})
    r = ctx.post("/api/me/delete")
    assert r.status_code == 202
    blocked = ctx.get("/api/notes")
    assert (
        blocked.status_code == 403 and blocked.json()["error"]["type"] == "account_pending_deletion"
    )
    assert ctx.get("/api/me/delete").json()["pending"] is True
    # cancelling restores access
    assert ctx.post("/api/me/delete/cancel").json() == {"cancelled": True}
    assert ctx.get("/api/notes").status_code == 200
    # request again and let the grace period pass
    ctx.post("/api/me/delete")

    async def purge():
        from datetime import datetime, timedelta, timezone

        db = Database(DB)
        async with db.session() as s:
            n = await ctx.c.run_due_deletions(
                s, now=datetime.now(timezone.utc) + timedelta(days=31)
            )
            left = (await s.execute(text("select count(*) from notes"))).scalar_one()
        await db.dispose()
        return n, left

    assert asyncio.run(purge()) == (1, 0)
    assert ("delete", SUB) in ctx.c.identity.calls
