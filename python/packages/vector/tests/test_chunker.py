import pytest
from tsp.vector import chunk, estimate_tokens, extract, rrf
from tsp.vector.extract import UnsupportedDocument


def test_short_text_is_one_chunk():
    cs = chunk("Hello world.")
    assert [c.content for c in cs] == ["Hello world."] and cs[0].ordinal == 0


def test_chunks_respect_the_size_and_overlap():
    paras = [f"Paragraph {i}. " + "word " * 60 for i in range(20)]  # ~65 tokens each
    cs = chunk("\n\n".join(paras), target_tokens=150, overlap=100)
    assert len(cs) > 3
    assert all(c.tokens <= 150 + 100 for c in cs)
    assert [c.ordinal for c in cs] == list(range(len(cs)))
    for a, b in zip(cs, cs[1:], strict=False):  # the tail of one chunk opens the next
        last = a.content.split("\n\n")[-1]
        assert last in b.content
    # every paragraph survives somewhere
    joined = "\n".join(c.content for c in cs)
    assert all(p.strip() in joined for p in paras)


def test_headings_travel_with_their_chunks():
    text = (
        "# Setup\n\n"
        + "\n\n".join("step " * 80 for _ in range(6))
        + "\n\n# Usage\n\n"
        + "use it " * 40
    )
    cs = chunk(text, target_tokens=120, overlap=0)
    assert any(c.heading == "Usage" and c.content.startswith("# Usage") for c in cs)
    assert all(c.content.startswith("# ") for c in cs)  # later "Setup" chunks get the heading back
    assert not any(c.content.startswith("# Setup\n\n# Setup") for c in cs)


def test_a_giant_paragraph_is_cut_and_nothing_is_lost():
    text = ("A sentence that goes on. " * 400).strip()
    cs = chunk(text, target_tokens=100, overlap=0)
    assert len(cs) > 5 and all(c.tokens <= 100 for c in cs)
    assert sum(c.content.count("A sentence") for c in cs) == 400


def test_empty_text_has_no_chunks():
    assert chunk("  \n\n ") == [] and estimate_tokens("") == 0


def test_rrf_rewards_agreement():
    def h(i):
        return {"id": i, "document_id": "d", "score": 0.0, "content": i, "metadata": {}}

    fused = rrf([[h("a"), h("b"), h("c")], [h("c"), h("b"), h("d")]])
    assert [x["id"] for x in fused] == [
        "c",
        "b",
        "a",
        "d",
    ]  # b and c are in both lists; c edges ahead (ranks 1 and 3 beat 2 and 2)
    assert fused[0]["score"] == pytest.approx(1 / 61 + 1 / 63)


def test_extract_plain_html_and_refusals():
    assert extract(b"plain", "text/plain") == "plain"
    out = extract(
        b"<html><script>x()</script><h1>Title</h1><p>Body text here.</p></html>", "text/html"
    )
    assert "Body text" in out and "x()" not in out
    with pytest.raises(UnsupportedDocument):
        extract(b"\x00", "image/png")
