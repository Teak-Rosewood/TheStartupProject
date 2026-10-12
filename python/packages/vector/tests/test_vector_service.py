from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from tsp.audit import Actor, AuditLog
from tsp.jobs import Job, JobRegistry, Worker
from tsp.metering import UsageRecord
from tsp.server import Settings, create_app, db_session
from tsp.storage import LocalObjectStore
from tsp.storage.models import StoredFile
from tsp.vector import (
    BadRequest,
    LimitExceeded,
    NotFound,
    PgVectorStore,
    VectorConfig,
    VectorService,
    ai_embedder,
    build_router,
    register_jobs,
    register_with_compliance,
)
from vector_support import DIMS, URL

ORG = "org1"
ACTOR = Actor("u1", "customer")
DOC = "Intro\n\nThe cat sat on the mat and purred.\n\nGardening\n\nTomatoes need sun and water every day."


def make(db, embedder, **kw):
    return VectorService(
        PgVectorStore(db), embedder, db, VectorConfig(dims=DIMS, embed_model="m1"), **kw
    )


async def indexed(db, embedder, text_=DOC, **kw):
    svc = make(db, embedder, **kw)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        doc = await svc.add_document(s, ORG, "kb", text_=text_, title="Pets", user_sub="u1")
    await svc.ingest(doc.id)
    return svc, doc.id


async def test_index_then_search_finds_the_right_passage_with_its_source(db, embedder):
    svc, doc_id = await indexed(db, embedder)
    async with db.session() as s:
        hits = await svc.search(s, ORG, "kb", "what does the cat do on the mat")
    assert (
        hits[0]["document_id"] == doc_id
        and "cat sat" in hits[0]["content"]
        and hits[0]["title"] == "Pets"
    )
    async with db.session() as s:
        kinds = [r.kind for r in (await s.execute(select(UsageRecord))).scalars()]
        d = await s.get(__import__("tsp.vector", fromlist=["Document"]).Document, doc_id)
    assert (
        sorted(kinds) == ["ai.embed", "ai.embed", "vector.search"]
        and d.status == "ready"
        and d.body is None
    )


async def test_same_content_is_one_document_and_ingest_is_idempotent(db, embedder):
    svc, doc_id = await indexed(db, embedder)
    calls = len(embedder.calls)
    async with db.session() as s:
        again = await svc.add_document(s, ORG, "kb", text_=DOC)
    assert again.id == doc_id
    await svc.ingest(doc_id)  # already ready: no work
    assert len(embedder.calls) == calls
    async with db.session() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
    assert len(jobs) == 1


async def test_failed_embed_retries_without_duplicate_chunks(db, embedder):
    svc = make(db, embedder)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        doc = await svc.add_document(s, ORG, "kb", text_=DOC)
    real = svc.embedder
    boom = {"on": True}

    async def flaky(texts, model, kind):
        if boom["on"]:
            raise RuntimeError("provider down")
        return await real(texts, model, kind)

    svc.embedder = flaky
    with pytest.raises(RuntimeError):
        await svc.ingest(doc.id)
    async with db.session() as s:
        assert (
            await s.get(__import__("tsp.vector", fromlist=["Document"]).Document, doc.id)
        ).status == "failed"
    boom["on"] = False
    await svc.ingest(doc.id)
    await svc.ingest(doc.id)
    async with db.session() as s:
        n = (
            await s.execute(
                text("select count(*) from vector_chunks where document_id = :d"), {"d": doc.id}
            )
        ).scalar_one()
    assert n == 1  # DOC is one chunk; two successful runs did not double it


async def test_wrong_vector_size_is_an_error_not_silent_truncation(db, embedder):
    svc = make(db, embedder)

    async def short(texts, model, kind):
        return SimpleNamespace(vectors=[[0.1] * 8 for _ in texts], tokens=1)

    svc.embedder = short
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        doc = await svc.add_document(s, ORG, "kb", text_=DOC)
    with pytest.raises(RuntimeError, match="8-dimension"):
        await svc.ingest(doc.id)


async def test_empty_and_unreadable_documents_fail_cleanly(db, embedder):
    svc = make(db, embedder)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        doc = await svc.add_document(s, ORG, "kb", text_="   ")
    await svc.ingest(doc.id)  # permanent: marked failed, no exception, no endless retry
    async with db.session() as s:
        d = await s.get(__import__("tsp.vector", fromlist=["Document"]).Document, doc.id)
    assert d.status == "failed" and "no text" in d.error


async def test_uploaded_files_are_read_from_object_storage(db, embedder, tmp_path):
    store = LocalObjectStore(tmp_path)
    store.put("uploads/org1/f1.txt", b"Bananas are rich in potassium.")
    async with db.session() as s:
        s.add(
            StoredFile(
                id="f1",
                org_id=ORG,
                user_sub="u1",
                key="uploads/org1/f1.txt",
                filename="fruit.txt",
                content_type="text/plain",
                size=30,
                status="ready",
            )
        )
    svc = make(db, embedder, reader=store.get)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        doc = await svc.add_document(s, ORG, "kb", file_id="f1")
        with pytest.raises(NotFound):  # another organization's file id is invisible
            await svc.add_document(s, "other", "kb", file_id="f1")
    await svc.ingest(doc.id)
    async with db.session() as s:
        assert "potassium" in (await svc.search(s, ORG, "kb", "potassium"))[0]["content"]


async def test_plan_limits(db, embedder):
    async def limits(session, org):
        return {"vector_docs": 1, "vector_chunks": 1}

    svc = make(db, embedder, limits=limits)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        first = await svc.add_document(s, ORG, "kb", text_="one")
        with pytest.raises(LimitExceeded):
            await svc.add_document(s, ORG, "kb", text_="two")
    await svc.ingest(first.id)
    chunky = make(db, embedder, limits=limits)
    async with db.session() as s:
        await s.execute(text("delete from vector_documents"))  # make room for a second doc
        second = await chunky.add_document(s, ORG, "kb", text_="a\n\n" + "word " * 700)
    await chunky.ingest(second.id)
    async with db.session() as s:
        d = await s.get(__import__("tsp.vector", fromlist=["Document"]).Document, second.id)
    assert d.status == "failed" and "passages" in d.error


async def test_delete_document_removes_vectors_and_audits(db, embedder):
    svc, doc_id = await indexed(db, embedder)
    async with db.session() as s:
        await svc.delete_document(s, ORG, doc_id, actor=ACTOR)
    async with db.session() as s:
        assert (await svc.search(s, ORG, "kb", "cat")) == []
        assert (await s.execute(text("select count(*) from vector_chunks"))).scalar_one() == 0
        assert "vector.document_deleted" in [
            a.action for a in (await s.execute(select(AuditLog))).scalars()
        ]
        with pytest.raises(NotFound):
            await svc.delete_document(s, ORG, doc_id, actor=ACTOR)


async def test_reembed_switches_models_and_drops_the_old_vectors(db, embedder):
    svc, doc_id = await indexed(db, embedder)
    async with db.session() as s:
        await svc.request_reembed(s, ORG, "kb", "m2", actor=ACTOR)
        with pytest.raises(BadRequest):
            await svc.request_reembed(
                s, ORG, "kb", "m2", actor=ACTOR
            ) if False else await svc.request_reembed(s, ORG, "kb", "m1", actor=ACTOR)
    cid = (await _collection(db)).id
    await svc.reembed(cid, "m2")
    assert (await _collection(db)).embed_model == "m2"
    async with db.session() as s:
        models = (
            (await s.execute(text("select distinct embed_model from vector_chunks")))
            .scalars()
            .all()
        )
        hits = await svc.search(s, ORG, "kb", "cat mat")
    assert (
        models == ["m2"] and hits and any(c[1] == "m2" and c[2] == "query" for c in embedder.calls)
    )


async def _collection(db):
    from tsp.vector import Collection

    async with db.session() as s:
        return (await s.execute(select(Collection))).scalar_one()


async def test_jobs_run_ingest_end_to_end(db, embedder):
    svc = make(db, embedder)
    reg = JobRegistry()
    register_jobs(reg, svc)
    async with db.session() as s:
        await svc.create_collection(s, ORG, "kb")
        await svc.add_document(s, ORG, "kb", text_=DOC)
    assert await Worker(reg, db, poll_interval=0.05).run_once() == 1
    async with db.session() as s:
        assert (await svc.search(s, ORG, "kb", "tomatoes sun"))[0]["content"].count("Tomatoes") == 1


async def test_compliance_exports_and_erases_a_users_documents(db, embedder):
    svc, doc_id = await indexed(db, embedder)

    class Comp:
        def __init__(self):
            self.ex, self.er = {}, {}

        def register_exporter(self, n, f):
            self.ex[n] = f

        def register_eraser(self, n, f):
            self.er[n] = f

    c = Comp()
    register_with_compliance(c, svc)
    async with db.session() as s:
        assert [d["title"] for d in await c.ex["vector"](s, "u1")] == ["Pets"]
        assert await c.ex["vector"](s, "someone-else") == []
        await c.er["vector"](s, "u1")
    async with db.session() as s:
        assert (await s.execute(text("select count(*) from vector_chunks"))).scalar_one() == 0
        assert (await s.execute(text("select count(*) from vector_documents"))).scalar_one() == 0


async def test_delete_org_clears_everything(db, embedder):
    svc, _ = await indexed(db, embedder)
    async with db.session() as s:
        await svc.delete_org(s, ORG, actor=ACTOR)
    async with db.session() as s:
        for t in ("vector_chunks", "vector_documents", "vector_collections"):
            assert (await s.execute(text(f"select count(*) from {t}"))).scalar_one() == 0


async def test_ai_embedder_adapts_the_tsp_ai_client(db):
    seen = {}

    class Embeddings:
        async def create(self, model, input, *, input_type, batch_size, **kw):
            seen.update(model=model, input_type=input_type, batch=batch_size)
            data = [
                SimpleNamespace(index=i, embedding=[float(i)]) for i in reversed(range(len(input)))
            ]
            return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=7))

    out = await ai_embedder(SimpleNamespace(embeddings=Embeddings()), batch_size=16)(
        ["a", "b"], "m", "query"
    )
    assert (
        out.vectors == [[0.0], [1.0]]
        and out.tokens == 7
        and seen == {"model": "m", "input_type": "query", "batch": 16}
    )


async def test_http_api(db, embedder):
    svc = make(db, embedder)
    killed = {"on": False}

    async def is_killed(s):
        return killed["on"]

    async def account():
        return SimpleNamespace(sub="u1")

    def org_member(role):
        async def dep(org_id: str):
            return SimpleNamespace(org_id=org_id, role="owner")

        return dep

    app = create_app(Settings(database_url=URL))
    app.include_router(
        build_router(svc, account, org_member, db_session, killed=is_killed), prefix="/api"
    )
    base = f"/api/orgs/{ORG}/vector"
    app.state.db = db  # no lifespan under ASGITransport: hand the app the test's database
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post(f"{base}/collections", json={"name": "kb"})).status_code == 201
        assert (await c.post(f"{base}/collections", json={"name": "kb"})).json()["error"][
            "type"
        ] == "bad_request"
        r = await c.post(
            f"{base}/documents", json={"collection": "kb", "text": DOC, "title": "Pets"}
        )
        assert r.status_code == 202 and r.json()["status"] == "pending"
        assert (await c.post(f"{base}/documents", json={"collection": "kb"})).status_code == 422
        await svc.ingest(r.json()["id"])
        res = (
            await c.post(f"{base}/search", json={"collection": "kb", "query": "cat on the mat"})
        ).json()["results"]
        assert "cat sat" in res[0]["content"]
        assert (
            await c.post(f"{base}/search", json={"collection": "nope", "query": "x"})
        ).status_code == 404
        killed["on"] = True
        assert (
            await c.post(f"{base}/search", json={"collection": "kb", "query": "x"})
        ).status_code == 503
        killed["on"] = False
        assert (await c.delete(f"{base}/documents/{r.json()['id']}")).status_code == 204
        assert (await c.get(f"{base}/documents")).json() == []
