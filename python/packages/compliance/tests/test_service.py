import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from tsp.audit import AuditLog
from tsp.compliance import (
    Compliance,
    ConsentError,
    DataRequest,
    Document,
    LocalExportStore,
    Manifest,
)

TERMS = Document(
    id="terms", title="Terms", version="v1", sha256="x", url="/legal/terms", requires_consent=True
)
PRIV = Document(
    id="privacy",
    title="Privacy",
    version="p1",
    sha256="y",
    url="/legal/privacy",
    requires_consent=True,
)
SUB = "11111111-1111-1111-1111-111111111111"


class FakeIdentity:
    def __init__(self):
        self.calls = []

    async def sign_out(self, sub):
        self.calls.append(("sign_out", sub))

    async def disable(self, sub):
        self.calls.append(("disable", sub))

    async def delete(self, sub):
        self.calls.append(("delete", sub))


def make(tmp_path, identity=None, manifest=None):
    m = manifest or Manifest(documents={"terms": TERMS, "privacy": PRIV})
    return Compliance(
        m, store=LocalExportStore(tmp_path / "exports"), identity=identity or FakeIdentity()
    )


def req():
    return SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.9", "user-agent": "pytest"},
        client=SimpleNamespace(host="10.0.0.1"),
        state=SimpleNamespace(request_id="r1"),
    )


async def test_consent_flow_and_new_versions_require_reacceptance(db, tmp_path):
    c = make(tmp_path)
    async with db.session() as s:
        assert {d.id for d in await c.stale_documents(s, SUB)} == {"terms", "privacy"}
        await c.accept(s, SUB, {"terms": "v1", "privacy": "p1"}, request=req())
    async with db.session() as s:
        assert await c.stale_documents(s, SUB) == []
    c.manifest = Manifest(
        documents={"terms": TERMS.model_copy(update={"version": "v2"}), "privacy": PRIV}
    )
    async with db.session() as s:
        assert [d.id for d in await c.stale_documents(s, SUB)] == ["terms"]
        await c.accept(s, SUB, {"terms": "v2"})
    async with db.session() as s:
        assert await c.stale_documents(s, SUB) == []
        row = (
            await s.execute(
                text("select ip, user_agent from consents where version = 'v1' limit 1")
            )
        ).one()
        assert tuple(row) == ("203.0.113.9", "pytest")


async def test_accepting_a_stale_or_unknown_version_is_rejected(db, tmp_path):
    c = make(tmp_path)
    async with db.session() as s:
        with pytest.raises(ConsentError, match="not current"):
            await c.accept(s, SUB, {"terms": "old"})
        with pytest.raises(ConsentError, match="unknown"):
            await c.accept(s, SUB, {"nope": "v1"})


async def test_consent_is_audited(db, tmp_path):
    c = make(tmp_path)
    async with db.session() as s:
        await c.accept(s, SUB, {"terms": "v1"}, request=req())
    async with db.session() as s:
        row = (await s.execute(select(AuditLog))).scalar_one()
    assert (row.action, row.actor_sub, row.ip, row.request_id) == (
        "consent.accepted",
        SUB,
        "203.0.113.9",
        "r1",
    )


async def test_export_collects_registered_exporters(db, tmp_path):
    c = make(tmp_path)

    async def notes(session, sub):
        return [{"text": "hello"}]

    c.register_exporter("notes", notes)
    async with db.session() as s:
        await c.accept(s, SUB, {"terms": "v1"})
    async with db.session() as s:
        result = await c.request_export(s, SUB, request=req())
    assert result.request.status == "completed" and result.url
    path = tmp_path / "exports" / result.request.result_key
    data = json.loads(path.read_text())
    assert data["data"]["notes"] == [{"text": "hello"}]
    assert data["data"]["compliance"]["consents"][0]["document"] == "terms"
    assert SUB not in result.request.result_key  # the object key does not reveal the user id


async def test_expired_export_has_no_link(db, tmp_path):
    c = make(tmp_path)
    async with db.session() as s:
        result = await c.request_export(s, SUB)
        result.request.completed_at = datetime.now(timezone.utc) - timedelta(days=8)
        assert await c.export_url(result.request) is None


async def test_deletion_grace_cancel_and_execution(db, tmp_path):
    ident = FakeIdentity()
    c = make(tmp_path, identity=ident)
    erased = []

    async def eraser(session, sub):
        erased.append(sub)

    c.register_eraser("notes", eraser)
    async with db.session() as s:
        await c.accept(s, SUB, {"terms": "v1"})
        r1 = await c.request_delete(s, SUB, request=req())
        r2 = await c.request_delete(s, SUB)  # idempotent
        assert r1.id == r2.id and ("sign_out", SUB) in ident.calls
        assert r1.scheduled_for - r1.requested_at.replace(tzinfo=timezone.utc) > timedelta(days=29)
    async with db.session() as s:
        assert await c.cancel_delete(s, SUB) is True
        assert await c.pending_deletion(s, SUB) is None
        assert await c.cancel_delete(s, SUB) is False
    # request again, then run it after the grace period
    async with db.session() as s:
        await c.request_delete(s, SUB)
    async with db.session() as s:
        assert await c.run_due_deletions(s) == 0  # still in grace
        later = datetime.now(timezone.utc) + timedelta(days=31)
        assert await c.run_due_deletions(s, now=later) == 1
    assert erased == [SUB] and ("delete", SUB) in ident.calls
    async with db.session() as s:
        remaining = (await s.execute(text("select user_sub from consents"))).scalars().all()
        done = (
            (await s.execute(select(DataRequest).where(DataRequest.status == "completed")))
            .scalars()
            .all()
        )
        actions = (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).scalars().all()
    assert remaining and all(
        r.startswith("deleted:") and SUB not in r for r in remaining
    )  # proof kept, person detached
    assert len(done) == 1
    assert actions[-1] == "account.deleted" and "account.deletion_cancelled" in actions


async def test_failed_erasure_is_recorded_not_lost(db, tmp_path):
    c = make(tmp_path)

    async def boom(session, sub):
        raise RuntimeError("disk on fire")

    c.register_eraser("bad", boom)
    async with db.session() as s:
        req_ = await c.request_delete(s, SUB)
    async with db.session() as s:
        got = await s.get(DataRequest, req_.id)
        with pytest.raises(RuntimeError):
            await c.execute_deletion(s, got)
        assert got.status == "failed" and "disk on fire" in got.error
