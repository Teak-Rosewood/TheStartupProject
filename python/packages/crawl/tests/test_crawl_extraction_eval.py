"""Run the 10 saved pages through the HTTP fetcher's extractor and score them against references."""

import json
from pathlib import Path

import httpx
import pytest
from tsp.crawl import HttpFetcher, NotModified
from tsp.crawl.evaluate import score_extraction, table_lines

DIR = Path(__file__).parent / "extraction"
CASES = json.loads((DIR / "cases.json").read_text())
HTML = {"content-type": "text/html; charset=utf-8"}


async def extract(case) -> str:
    async def resolve(host):
        return ["93.184.216.34"]

    def handler(req):
        return httpx.Response(200, headers=HTML, content=(DIR / case["html"]).read_bytes())

    fetcher = HttpFetcher(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolver=resolve
    )
    page = await fetcher.fetch(f"https://site.example.com/{case['id']}")
    assert not isinstance(page, NotModified)
    return page.markdown


def test_scorer_rewards_good_output_and_punishes_bad_output():
    ref = "# Plans\n\nPick a plan today.\n\n| Plan | Price |\n|---|---|\n| Team | $49 |\n"
    boiler = ["Accept all cookies", "Subscribe to our newsletter"]
    good = score_extraction(ref, ref, boiler)
    assert (good.recall, good.boilerplate_removed, good.table_retention) == (1.0, 1.0, 1.0)
    noisy = score_extraction(
        ref + "\nAccept all cookies\nSubscribe to our newsletter\n", ref, boiler
    )
    assert noisy.boilerplate_removed == 0.0 and noisy.recall == 1.0
    flat = score_extraction("# Plans\n\nPick a plan today.\n\nPlan Price Team $49\n", ref, boiler)
    assert flat.recall == 1.0 and flat.table_retention == 0.0  # cells present but no longer a table
    missing = score_extraction("# Plans\n", ref, boiler)
    assert missing.recall < 0.5 and missing.table_retention == 0.0
    assert table_lines("| a | b |\n|---|---|\n| 1 | 2 |") == ["| a | b |", "| 1 | 2 |"]


def test_the_fixture_set_is_complete():
    assert len(CASES) == 10
    for c in CASES:
        assert (DIR / c["html"]).exists() and (DIR / c["reference"]).exists()
        assert c["boilerplate"] and ("|" in (DIR / c["reference"]).read_text()) == bool(c["tables"])
        assert all(
            b in (DIR / c["html"]).read_text() for b in c["boilerplate"][:3]
        )  # the chrome is on the page


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_each_page_extracts_its_content_without_the_chrome(case):
    md = await extract(case)
    score = score_extraction(md, (DIR / case["reference"]).read_text(), case["boilerplate"])
    assert score.boilerplate_removed == 1.0, md
    assert score.recall >= 0.9, (score, md)
    if case["tables"]:
        assert score.table_retention >= 0.9, (score, md)


async def test_the_whole_set_meets_the_quality_bar():
    scores = [
        score_extraction(await extract(c), (DIR / c["reference"]).read_text(), c["boilerplate"])
        for c in CASES
    ]
    n = len(scores)
    assert sum(s.recall for s in scores) / n >= 0.95
    assert sum(s.boilerplate_removed for s in scores) / n >= 0.98
    tabled = [s for s, c in zip(scores, CASES, strict=True) if c["tables"]]
    assert sum(s.table_retention for s in tabled) / len(tabled) >= 0.95
