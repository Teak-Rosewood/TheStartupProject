import asyncio
import base64
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from tsp.db import Database, upgrade
from tsp.storage import (
    LocalObjectStore,
    QuotaExceeded,
    S3ObjectStore,
    StoredFile,
    UploadPolicy,
    UploadRejected,
    Uploads,
    safe_filename,
)

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    drop = "DROP TABLE IF EXISTS files, audit_log, alembic_version CASCADE"
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(drop))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.storage"])
    yield d
    async with d.session() as s:
        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
        await s.execute(text(drop))
    await d.dispose()


@pytest.fixture
def store(tmp_path):
    return LocalObjectStore(tmp_path / "bucket")


def uploads(store, **policy):
    return Uploads(store, UploadPolicy(**{"max_bytes": 1000, "org_quota_bytes": 2500, **policy}))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\a b.png", "a_b.png"),
        (".htaccess", "htaccess"),
        ("", "file"),
        ("   ", "file"),
        ("naïve résumé.pdf", "na_ve_r_sum_.pdf"),
        ("a" * 300 + ".txt", ("a" * 96) + ".txt"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


async def test_policy_blocks_bad_types_and_sizes(db, store):
    up = uploads(store)
    async with db.session() as s:
        for ctype, size, msg in (
            ("application/x-msdownload", 10, "not accepted"),
            ("image/png", 5000, "between 1 byte"),
            ("image/png", 0, "between 1 byte"),
        ):
            with pytest.raises(UploadRejected, match=msg):
                await up.request_upload(s, "org1", "u1", "a.png", ctype, size)


async def test_keys_are_scoped_to_the_organization_and_unguessable(db, store):
    up = uploads(store)
    async with db.session() as s:
        a = await up.request_upload(s, "org1", "u1", "../../x/photo.png", "image/png", 100)
        b = await up.request_upload(s, "org1", "u1", "photo.png", "image/png", 100)
    ka, kb = a["upload"]["fields"]["key"], b["upload"]["fields"]["key"]
    assert ka.startswith("uploads/org1/") and ka.endswith("/photo.png") and ".." not in ka
    assert ka != kb  # same filename, different object


async def test_quota_counts_pending_and_ready_but_not_deleted(db, store):
    up = uploads(store)
    async with db.session() as s:
        first = await up.request_upload(s, "org1", "u1", "a.png", "image/png", 1000)
        await up.request_upload(s, "org1", "u1", "b.png", "image/png", 1000)
        with pytest.raises(QuotaExceeded):
            await up.request_upload(s, "org1", "u1", "c.png", "image/png", 1000)  # 3000 > 2500
        await up.request_upload(
            s, "org2", "u9", "c.png", "image/png", 1000
        )  # another organization has its own allowance
        store.put(
            next(iter(s.new)).key
            if False
            else (
                await s.execute(select(StoredFile.key).where(StoredFile.id == first["file_id"]))
            ).scalar_one(),
            b"x" * 10,
        )
        await up.complete(s, "org1", first["file_id"])
        await up.delete(s, "org1", first["file_id"], "u1")
        await up.request_upload(
            s, "org1", "u1", "d.png", "image/png", 1000
        )  # deleting freed the space


async def test_complete_checks_what_actually_arrived(db, store):
    up = uploads(store)
    async with db.session() as s:
        r = await up.request_upload(s, "org1", "u1", "a.png", "image/png", 100)
        fid = r["file_id"]
        key = r["upload"]["fields"]["key"]
        with pytest.raises(UploadRejected, match="not arrived"):
            await up.complete(s, "org1", fid)
        store.put(key, b"x" * 150)  # bigger than declared
        with pytest.raises(UploadRejected, match="does not match"):
            await up.complete(s, "org1", fid)
        assert (await s.get(StoredFile, fid)).status == "rejected"
        assert await store.head(key) is None  # the offending object was removed
        r2 = await up.request_upload(s, "org1", "u1", "b.png", "image/png", 100)
        store.put(r2["upload"]["fields"]["key"], b"x" * 100)
        done = await up.complete(s, "org1", r2["file_id"])
        assert (done.status, done.size) == ("ready", 100)
        assert (await up.complete(s, "org1", r2["file_id"])).status == "ready"  # idempotent


async def test_other_organizations_cannot_touch_a_file(db, store):
    up = uploads(store)
    async with db.session() as s:
        r = await up.request_upload(s, "org1", "u1", "a.png", "image/png", 10)
        store.put(r["upload"]["fields"]["key"], b"x" * 10)
        await up.complete(s, "org1", r["file_id"])
        for op in (
            up.complete(s, "org2", r["file_id"]),
            up.download_url(s, "org2", r["file_id"]),
            up.delete(s, "org2", r["file_id"], "u2"),
        ):
            with pytest.raises(LookupError):
                await op
        assert (await up.download_url(s, "org1", r["file_id"])).startswith("file://")


async def test_scanner_can_reject_files(db, store):
    async def scan(f):
        return False

    up = Uploads(store, UploadPolicy(max_bytes=1000), scan=scan)
    async with db.session() as s:
        r = await up.request_upload(s, "org1", "u1", "a.png", "image/png", 10)
        store.put(r["upload"]["fields"]["key"], b"x" * 10)
        with pytest.raises(UploadRejected, match="content check"):
            await up.complete(s, "org1", r["file_id"])
        assert await store.head(r["upload"]["fields"]["key"]) is None


async def test_compliance_export_and_erasure(db, store):
    up = uploads(store)
    reg = SimpleNamespace(ex={}, er={})
    reg.register_exporter = lambda n, f: reg.ex.__setitem__(n, f)
    reg.register_eraser = lambda n, f: reg.er.__setitem__(n, f)
    up.register_with_compliance(reg)
    async with db.session() as s:
        r = await up.request_upload(s, "org1", "u1", "a.png", "image/png", 10)
        store.put(r["upload"]["fields"]["key"], b"x" * 10)
        await up.complete(s, "org1", r["file_id"])
        assert (await reg.ex["files"](s, "u1"))[0]["filename"] == "a.png"
        await reg.er["files"](s, "u1")
        assert await store.head(r["upload"]["fields"]["key"]) is None
        assert (await s.get(StoredFile, r["file_id"])).status == "deleted"


async def test_s3_signed_post_carries_the_limits_s3_will_enforce(monkeypatch):
    """Signing is offline, so the real policy document can be inspected without AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTTESTTESTTEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    post = await S3ObjectStore("my-bucket", "ap-south-1").presign_post(
        "uploads/org1/abc/a.png", "image/png", 5000, 300
    )
    assert post["url"].startswith("https://") and "my-bucket" in post["url"]
    policy = json.loads(base64.b64decode(post["fields"]["policy"]))
    conds = policy["conditions"]
    assert ["content-length-range", 1, 5000] in conds
    assert {"Content-Type": "image/png"} in conds and {"key": "uploads/org1/abc/a.png"} in conds
    assert {"x-amz-server-side-encryption": "AES256"} in conds
    assert post["fields"]["key"] == "uploads/org1/abc/a.png"
    url = await S3ObjectStore("my-bucket", "ap-south-1").presign_get(
        "uploads/org1/abc/a.png", 300, "../evil name.png"
    )
    assert (
        "X-Amz-Signature" in url
        and "attachment" in url
        and "evil_name.png" in url
        and ".." not in url.split("?")[1]
    )
