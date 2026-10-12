import json
from pathlib import Path

import pytest
from tsp.vector import PgVectorStore, VectorConfig, VectorService
from tsp.vector.cli import ingest
from tsp.vector.evaluate import Scores, evaluate
from vector_support import DIMS

EVAL = Path(__file__).parent / "eval"


async def test_metrics_on_hand_made_results():
    answers = {"q1": ["a", "b"], "q2": ["x", "a"], "q3": ["x"]}

    async def search(q, k):
        return [{"title": t} for t in answers[q]][:k]

    qs = [
        {"question": "q1", "expected": "a"},
        {"question": "q2", "expected": "a"},
        {"question": "q3", "expected": "a"},
    ]
    s = await evaluate(search, qs, k=2)
    assert (
        s.questions == 3
        and s.recall_at_k == pytest.approx(2 / 3)
        and s.mrr == pytest.approx((1 + 0.5 + 0) / 3)
    )
    assert s.drop_from(Scores(3, 1.0, 1.0)) == pytest.approx(50.0, abs=0.1)


async def test_sample_corpus_is_found_by_the_pipeline(db, embedder):
    svc = VectorService(PgVectorStore(db), embedder, db, VectorConfig(dims=DIMS, embed_model="m1"))
    out = await ingest(svc, "org1", "recipes", EVAL / "corpus")
    assert len(out) == 10 and all(status == "ready" for _, status in out)
    questions = json.loads((EVAL / "questions.json").read_text())
    assert len(questions) == 20

    async def search(q, k):
        async with db.session() as s:
            return await svc.search(s, "org1", "recipes", q, k=k)

    scores = await evaluate(search, questions, k=5)
    # a crude word-hash embedder is enough for the right recipe to come back almost always
    assert scores.recall_at_k >= 0.85 and scores.mrr >= 0.6, scores
