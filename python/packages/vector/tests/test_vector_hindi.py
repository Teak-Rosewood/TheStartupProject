"""Hindi full-text search and cross-language retrieval."""

from sqlalchemy import text
from tsp.vector import PgVectorStore, VectorConfig, VectorService, detect_lang, normalize_devanagari
from vector_support import DIMS, FakeEmbedder

# the same word spelled four ways: plain क, precomposed क़ (U+0958), क + nukta, with a joiner
KEEMAT = [
    "\u0915\u0940\u092e\u0924",
    "\u0958\u0940\u092e\u0924",
    "\u0915\u093c\u0940\u092e\u0924",
    "\u0915\u200d\u0940\u092e\u0924",
]


def test_variants_normalise_to_one_spelling():
    assert len({normalize_devanagari(v) for v in KEEMAT}) == 1
    assert normalize_devanagari("हिंदी") == normalize_devanagari("हिँदी")  # anusvara vs chandrabindu
    assert normalize_devanagari("plain text") == "plain text"


def test_language_detection_is_a_script_check():
    assert detect_lang("पनीर टिक्का कैसे बनाएं") == "hi"
    assert detect_lang("how to make paneer tikka") == "en"
    assert detect_lang("paneer tikka पनीर") == "en"  # mostly Latin
    assert detect_lang("123 !!!") == "en"


async def test_sql_and_python_normalisation_agree(db):
    async with db.session() as s:
        for v in KEEMAT + ["हिँदी", "plain"]:
            sql = (await s.execute(text("select tsp_norm_devanagari(:t)"), {"t": v})).scalar_one()
            assert sql == normalize_devanagari(v)


async def ensure(db, org="o", col="c", doc="d"):
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


def item(i, content):
    return {
        "id": f"i{i}",
        "document_id": "d",
        "ordinal": i,
        "content": content,
        "embedding": [1.0] + [0.0] * (DIMS - 1),
        "token_count": 3,
        "embed_model": "m1",
        "metadata": {},
    }


async def test_hindi_word_is_found_whichever_way_it_was_typed(db):
    await ensure(db)
    store = PgVectorStore(db)
    await store.upsert(
        "o",
        "c",
        [item(0, "पनीर की \u0915\u0940\u092e\u0924 आज कम है"), item(1, "टमाटर की चटनी बनाएं")],
    )
    for variant in KEEMAT:
        hits = await store.text_query("o", "c", variant, 5)
        assert [h["content"] for h in hits] == ["पनीर की \u0915\u0940\u092e\u0924 आज कम है"], variant
    # the stored spelling can be the odd one, too
    await store.upsert("o", "c", [item(2, "आटे की क़ीमत")])
    assert {h["id"] for h in await store.text_query("o", "c", KEEMAT[0], 5)} == {"i0", "i2"}


async def test_english_still_stems(db):
    await ensure(db)
    store = PgVectorStore(db)
    await store.upsert("o", "c", [item(0, "Cooking pancakes is easy"), item(1, "पनीर टिक्का")])
    assert [h["id"] for h in await store.text_query("o", "c", "cook pancake", 5)] == ["i0"]
    langs = {r[0] for r in (await db_rows(db, "select lang from vector_chunks"))}
    assert langs == {"en", "hi"}


async def db_rows(db, sql):
    async with db.session() as s:
        return (await s.execute(text(sql))).all()


CONCEPTS = {
    "cat": 0,
    "बिल्ली": 0,
    "mat": 1,
    "चटाई": 1,
    "tomato": 2,
    "टमाटर": 2,
    "water": 3,
    "पानी": 3,
}


class CrossLingualEmbedder(FakeEmbedder):
    """Stands in for a multilingual model: a word and its translation share a dimension. It proves the
    pipeline keeps languages apart only where it should; it says nothing about a real model's quality."""

    async def __call__(self, texts, model, kind):
        from tsp.vector import Embedded

        out = []
        for t in texts:
            v = [0.0] * DIMS
            for w in t.lower().replace(".", " ").split():
                if w in CONCEPTS:
                    v[CONCEPTS[w]] += 1.0
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return Embedded(out, tokens=len(texts))


async def test_a_hindi_question_finds_the_english_passage(db):
    svc = VectorService(
        PgVectorStore(db), CrossLingualEmbedder(), db, VectorConfig(dims=DIMS, embed_model="m1")
    )
    async with db.session() as s:
        await svc.create_collection(s, "o", "kb")
        a = await svc.add_document(s, "o", "kb", text_="The cat sat on the mat.", title="cat")
        b = await svc.add_document(s, "o", "kb", text_="Tomato needs water.", title="tomato")
    for d in (a, b):
        await svc.ingest(d.id)
    async with db.session() as s:
        hits = await svc.search(s, "o", "kb", "बिल्ली चटाई")
    assert hits[0]["title"] == "cat"
    async with db.session() as s:
        hits = await svc.search(s, "o", "kb", "tomato water")
    assert hits[0]["title"] == "tomato"


def test_multilingual_models_are_required():
    # a documentation guard: the guide must say which embedding models work across languages
    from pathlib import Path

    guide = (Path(__file__).parents[4] / "docs/modules/vector.md").read_text().lower()
    assert "multilingual" in guide
