"""The editorial backend as the sample API mounts it: staff routes by organization role, the public
corrections log and complaint intake, the emails they send, and the admin views."""

import asyncio
import os
from pathlib import Path

import pytest
from api_support import ALL_TABLES
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.server import current_user

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
MODULES = [
    "tsp.audit", "tsp.compliance", "tsp.accounts", "tsp.payments", "tsp.jobs", "tsp.notify",
    "tsp.growth", "tsp.flags", "tsp.storage", "tsp.metering", "tsp.cms",
]  # fmt: skip
ANN = "aaaaaaaa-0000-0000-0000-00000000000a"


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_current")
        )
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_published")
        )
        await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


def rows(sql):
    async def go():
        d = Database(DB)
        async with d.session() as s:
            out = (await s.execute(text(sql))).all()
        await d.dispose()
        return out

    return asyncio.run(go())


@pytest.fixture
def client(monkeypatch):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    import tsp_api.main as main

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setattr(main, "build_llm", lambda: None)
    monkeypatch.setenv("CMS_EDITOR_EMAILS", "desk@example.com")
    monkeypatch.setenv("CMS_COMPLAINT_LEVELS", "desk:10,chief:5")
    app = main.build_app()
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=ANN,
        token_use="id",
        email="ann@example.com",
        raw={"email": "ann@example.com", "email_verified": True},
    )
    with TestClient(app) as c:
        yield c
    asyncio.run(_drop())


def test_cms_settings_parse_levels_and_defaults():
    from tsp_api.cms import cms_settings

    s = cms_settings(
        {
            "CMS_COMPLAINT_LEVELS": "desk:30,chief:15, ombudsman:20",
            "CMS_EDITOR_EMAILS": "a@x.com, b@x.com",
            "CMS_REVIEW_AFTER_DAYS": "14",
        }
    )
    assert [(lv.handler, lv.resolve_days) for lv in s.complaints.levels] == [
        ("desk", 30),
        ("chief", 15),
        ("ombudsman", 20),
    ]
    assert (
        s.editors == ["a@x.com", "b@x.com"]
        and s.lifecycle.review_after_days == 14
        and s.public_org is None
    )
    assert cms_settings({}).complaints.ack_hours == 72


def test_staff_flow_and_the_emails_it_sends(client):
    org = client.post("/api/orgs", json={"name": "Food Co"}).json()["id"]
    # staff routes work for the owner, and the roles come from the membership
    item = client.post("/api/cms/items", json={"type": "recipe", "slug": "pancakes"})
    assert item.status_code == 201
    iid = item.json()["id"]
    client.post(
        f"/api/cms/items/{iid}/versions",
        json={"lang": "en", "title": "Pancakes", "body": [{"type": "paragraph", "text": "Whisk."}]},
    )
    assert client.post(f"/api/cms/items/{iid}/submit").status_code == 200
    approve = client.post(f"/api/cms/items/{iid}/approve")
    assert (
        approve.status_code == 409 and "wrote it" in approve.text
    )  # nobody approves their own version
    # complaints: staff list is empty, then a complaint comes in through the desk directly
    assert client.get("/api/cms/complaints").json() == []
    assert client.get("/api/cms/complaints/report?month=2026-03").json()["total"] == 0
    # a second organization makes the header necessary
    client.post("/api/orgs", json={"name": "Other Co"})
    assert client.get("/api/cms/complaints").status_code == 400
    assert client.get("/api/cms/complaints", headers={"X-Org-Id": org}).status_code == 200
    assert client.get("/api/cms/complaints", headers={"X-Org-Id": "nope"}).status_code == 403


def test_public_intake_creates_a_complaint_and_emails_the_reader_and_the_desk(monkeypatch):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    import tsp_api.main as main

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setattr(main, "build_llm", lambda: None)
    monkeypatch.setenv("CMS_EDITOR_EMAILS", "desk@example.com")
    monkeypatch.setenv("CMS_PUBLIC_ORG", "pub-org")
    app = main.build_app()
    with TestClient(app) as c:
        body = {
            "name": "Reader",
            "email": "reader@example.com",
            "category": "accuracy",
            "body": "The page says 5 eggs but the video says 3.",
        }
        r = c.post("/api/public/cms/complaints", json=body)
        assert r.status_code == 201 and r.json()["reference"].startswith("C-")
        assert (
            c.post("/api/public/cms/complaints", json={**body, "category": "rude"}).status_code
            == 422
        )
        assert c.get("/api/public/cms/corrections").json() == {"corrections": []}
        assert (
            c.get("/api/public/cms/corrections.rss")
            .headers["content-type"]
            .startswith("application/rss+xml")
        )
        assert c.get("/api/public/cms/items/nope/corrections").status_code == 404
        mail = rows("select to_addr, template from emails order by to_addr")
        assert [(a, t) for a, t in mail] == [
            ("desk@example.com", "complaint_staff"),
            ("reader@example.com", "complaint_received"),
        ]
        stored = rows(
            "select reference_check from (select 'C-' || extract(year from received_at)::int || '-' || lpad(seq::text, 6, '0') as reference_check from cms_complaints) q"
        )
        assert stored[0][0] == r.json()["reference"]
    asyncio.run(_drop())


def test_cms_admin_views_are_registered_for_staff_roles():
    from tsp.admin.roles import allowed
    from tsp.cms.admin import CMS_VIEWS

    ids = {v.identity for v in CMS_VIEWS}
    assert ids == {
        "cms-item",
        "cms-correction",
        "cms-review",
        "cms-complaint",
        "cms-complaint-event",
        "cms-window",
    }
    assert all(allowed("support", i, "list") and allowed("support", i, "details") for i in ids)
    assert not any(allowed("support", i, "edit") or allowed("finance", i, "list") for i in ids)
    assert all(allowed("superadmin", i, "list") for i in ids)
