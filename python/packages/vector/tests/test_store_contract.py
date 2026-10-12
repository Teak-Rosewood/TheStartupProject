"""One suite, every backend: whatever a VectorStore does, it does for these tests."""

import pytest
from fake_s3vectors import FakeS3Vectors
from sqlalchemy import text
from tsp.vector import PgVectorStore, S3VectorStore
from tsp.vector.pg import scope
from vector_support import DIMS, embed_text

ORG_A, ORG_B = "orga", "orgb"
TEXTS = {
    "cat": "the cat sat on the mat",
    "dog": "a dog barked at the mailman",
    "fin": "quarterly revenue grew by ten percent",
}


@pytest.fixture(params=["pgvector", "s3vectors"])
async def backend(request, db):
    """Returns (store, ensure): ensure(org, collection, document) creates the rows a backend needs."""

    async def ensure_pg(org, col, doc):
        async with db.session() as s:
            await s.execute(
                text(
                    "INSERT INTO vector_collections (id, org_id, name, embed_model, dims) VALUES (:c, :o, :c, 'm1', :d) ON CONFLICT DO NOTHING"
                ),
                {"c": col, "o": org, "d": DIMS},
            )
            await s.execute(
                text(
                    "INSERT INTO vector_documents (id, org_id, collection_id, checksum) VALUES (:d, :o, :c, :d) ON CONFLICT DO NOTHING"
                ),
                {"d": doc, "o": org, "c": col},
            )

    if request.param == "pgvector":
        return PgVectorStore(db), ensure_pg

    async def noop(*a):
        return None

    return S3VectorStore("bucket", client=FakeS3Vectors(), dims=DIMS), noop


def item(doc, ordinal, content, model="m1", meta=None):
    return {
        "id": f"{doc}-{ordinal}-{model}",
        "document_id": doc,
        "ordinal": ordinal,
        "content": content,
        "embedding": embed_text(content),
        "token_count": 5,
        "embed_model": model,
        "metadata": meta or {},
    }


async def load(backend, org="orga", col="col1", doc="doc1", texts=TEXTS, meta=None):
    store, ensure = backend
    await ensure(org, col, doc)
    await store.upsert(org, col, [item(doc, i, t, meta=meta) for i, t in enumerate(texts.values())])
    return store


async def test_nearest_chunk_comes_first(backend):
    store = await load(backend)
    hits = await store.query(ORG_A, "col1", embed_text("where is the cat"), 3)
    assert [h["content"] for h in hits][0] == TEXTS["cat"] and len(hits) == 3
    assert hits[0]["score"] > hits[1]["score"] and hits[0]["document_id"] == "doc1"


async def test_upsert_twice_does_not_duplicate(backend):
    store = await load(backend)
    await load(backend)
    assert len(await store.query(ORG_A, "col1", embed_text("cat"), 10)) == 3


async def test_organizations_never_see_each_other_even_with_crafted_filters(backend):
    store, ensure = backend
    await load(backend, org=ORG_A, col="col1", doc="docA")
    await ensure(ORG_B, "col2", "docB")
    await store.upsert(ORG_B, "col2", [item("docB", 0, "secret merger plans for the cat company")])
    for flt in (None, {"document_id": "docA"}, {"org_id": ORG_A}):
        for hit in await store.query(ORG_B, "col2", embed_text("cat"), 10, flt):
            assert hit["document_id"] == "docB"
    # same collection id from another org's point of view finds nothing
    assert await store.query(ORG_B, "col1", embed_text("cat"), 10) == []


async def test_metadata_filter(backend):
    store = await load(backend, meta={"lang": "en"})
    await load(backend, doc="doc2", texts={"x": "the cat is french"}, meta={"lang": "fr"})
    hits = await store.query(ORG_A, "col1", embed_text("cat"), 10, {"lang": "fr"})
    assert [h["content"] for h in hits] == ["the cat is french"]


async def test_replace_document_leaves_only_the_new_chunks(backend):
    store = await load(backend)
    await store.replace_document(
        ORG_A, "col1", "doc1", [item("doc1", 0, "entirely different words about gardens")]
    )
    hits = await store.query(ORG_A, "col1", embed_text("cat"), 10)
    assert [h["content"] for h in hits] == ["entirely different words about gardens"]


async def test_delete_ids_document_and_org(backend):
    store, ensure = backend
    await load(backend)
    await load(backend, doc="doc2", texts={"x": "another cat"})
    await store.delete(ORG_A, "col1", ["doc1-0-m1"])
    assert TEXTS["cat"] not in [
        h["content"] for h in await store.query(ORG_A, "col1", embed_text("cat"), 10)
    ]
    await store.delete_document(ORG_A, "doc2")
    assert "another cat" not in [
        h["content"] for h in await store.query(ORG_A, "col1", embed_text("cat"), 10)
    ]
    await load(backend, org=ORG_B, col="col9", doc="docB")
    await store.delete_org(ORG_A)
    assert await store.query(ORG_A, "col1", embed_text("cat"), 10) == []
    assert len(await store.query(ORG_B, "col9", embed_text("cat"), 10)) == 3  # other orgs untouched


async def test_models_coexist_then_old_ones_are_dropped(backend):
    store = await load(backend)
    await store.upsert(ORG_A, "col1", [item("doc1", 0, TEXTS["cat"], model="m2")])
    assert len(await store.query(ORG_A, "col1", embed_text("cat"), 10)) == 4
    assert len(await store.query(ORG_A, "col1", embed_text("cat"), 10, model="m2")) == 1
    assert [r["content"] for r in await store.chunks(ORG_A, "col1", "doc1", "m1")] == list(
        TEXTS.values()
    )
    await store.delete_other_models(ORG_A, "col1", "m2")
    assert [h["content"] for h in await store.query(ORG_A, "col1", embed_text("cat"), 10)] == [
        TEXTS["cat"]
    ]


async def test_full_text_search_on_stores_that_have_it(backend):
    store = await load(backend)
    if not hasattr(store, "text_query"):
        pytest.skip("no keyword search in this backend")
    hits = await store.text_query(ORG_A, "col1", "mailman", 5)
    assert [h["content"] for h in hits] == [TEXTS["dog"]]
    assert await store.text_query(ORG_B, "col1", "mailman", 5) == []


async def test_row_level_security_blocks_unscoped_reads_for_ordinary_roles(db):
    store = PgVectorStore(db)

    async def ensure(*a):
        await _ensure(db, *a)

    await load((store, ensure))
    async with db.session() as s:
        await s.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='tsp_rls_probe') THEN CREATE ROLE tsp_rls_probe NOLOGIN; END IF; END $$"
            )
        )
        await s.execute(text("GRANT SELECT ON vector_chunks TO tsp_rls_probe"))
    async with db.session() as s:
        await s.execute(text("SET LOCAL ROLE tsp_rls_probe"))
        assert (
            await s.execute(text("SELECT count(*) FROM vector_chunks"))
        ).scalar_one() == 0  # no org bound
        await scope(s, ORG_B)
        assert (
            await s.execute(text("SELECT count(*) FROM vector_chunks"))
        ).scalar_one() == 0  # wrong org
        await scope(s, ORG_A)
        assert (
            await s.execute(text("SELECT count(*) FROM vector_chunks"))
        ).scalar_one() == 3  # right org


async def _ensure(db, org, col, doc):
    async with db.session() as s:
        await s.execute(
            text(
                "INSERT INTO vector_collections (id, org_id, name, embed_model, dims) VALUES (:c, :o, :c, 'm1', :d) ON CONFLICT DO NOTHING"
            ),
            {"c": col, "o": org, "d": DIMS},
        )
        await s.execute(
            text(
                "INSERT INTO vector_documents (id, org_id, collection_id, checksum) VALUES (:d, :o, :c, :d) ON CONFLICT DO NOTHING"
            ),
            {"d": doc, "o": org, "c": col},
        )
