import json
from datetime import UTC, datetime, timedelta

import pytest
from tsp.trends import (
    EmergingTermsSignal,
    FeedVolumeSignal,
    GdeltVolumeSignal,
    SearchTrendsSignal,
    SignalUnavailable,
    Topic,
    TrendsContext,
    parse_feed,
    scrape_rung,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=1)
TOPICS = [
    Topic("t1", "air-fryer", "Air fryer", ("air fryer", "air fryers")),
    Topic("t2", "ramen", "Ramen", ("ramen",)),
]


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://n.test/{i}</link><pubDate>{d}</pubDate></item>"
        for i, (t, d) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'


def rfc(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def ctx(web):
    return TrendsContext(web.fetcher(), None)


async def test_feed_volume_counts_matching_items_per_hour(web):
    web.pages["https://f.test/rss"] = (
        200,
        rss(
            ("Best air fryer recipes", rfc(NOW)),
            ("Air fryers are everywhere", rfc(NOW + timedelta(minutes=5))),
            ("Ramen night", rfc(NOW)),
            ("Unrelated news", rfc(NOW)),
            ("Old air fryer story", rfc(NOW - timedelta(days=5))),
        ),
    )
    pts = await FeedVolumeSignal(["https://f.test/rss"]).collect(ctx(web), TOPICS, SINCE)
    got = {(p.topic_id, p.value) for p in pts}
    assert got == {("t1", 2.0), ("t2", 1.0)}
    assert pts[0].attrs["urls"]


async def test_a_broken_feed_does_not_hide_the_others(web):
    web.pages["https://ok.test/rss"] = (200, rss(("ramen again", rfc(NOW))))
    web.pages["https://bad.test/rss"] = (200, "not xml")
    pts = await FeedVolumeSignal(
        ["https://bad.test/rss", "https://gone.test/rss", "https://ok.test/rss"]
    ).collect(ctx(web), TOPICS, SINCE)
    assert [p.topic_id for p in pts] == ["t2"]


def test_feed_parser_handles_atom_and_refuses_dtd():
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Ramen!</title><link href="https://a.test/1"/>'
        "<updated>2026-03-01T10:00:00Z</updated></entry></feed>"
    )
    (item,) = parse_feed(atom)
    assert (item.title, item.link) == ("Ramen!", "https://a.test/1")
    with pytest.raises(SignalUnavailable):
        parse_feed('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss><channel/></rss>')


async def test_emerging_terms_become_unmatched_points(web):
    titles = [
        "Why everyone loves tamarind candy",
        "Tamarind candy shortage worries shops",
        "Tamarind candy recipe goes viral",
        "Ramen ranking",
        "Weather update tomorrow",
    ]
    web.pages["https://f.test/rss"] = (200, rss(*[(t, rfc(NOW)) for t in titles]))
    pts = await EmergingTermsSignal(["https://f.test/rss"], min_count=3).collect(
        ctx(web), TOPICS, SINCE
    )
    labels = {p.label for p in pts}
    assert "tamarind candy" in labels
    assert "tamarind" not in labels  # the longer phrase wins
    assert all(p.topic_id is None for p in pts)
    assert not any(
        "ramen" in (p.label or "") for p in pts
    )  # topic-matching titles are not "emerging"


async def test_gdelt_timeline_parsed(web):
    doc = {
        "timeline": [
            {
                "series": "Volume",
                "data": [{"date": "20260301T100000Z", "value": 0.5}, {"date": "bad", "value": 1}],
            }
        ]
    }
    web.pages["https://api.gdeltproject.org/api/v2/doc/doc"] = (200, "")
    sig = GdeltVolumeSignal()

    async def get_text(url):
        assert "timelinevol" in url and "%22air%20fryer%22" in url
        return json.dumps(doc)

    c = ctx(web)
    c.get_text = get_text
    pts = await sig.collect(c, TOPICS[:1], datetime(2026, 2, 28, tzinfo=UTC))
    assert [(p.topic_id, p.value) for p in pts] == [("t1", 0.5)]


TRENDING = (
    '<rss xmlns:ht="https://trends.test/ht"><channel><item><title>air fryer chicken</title>'
    "<ht:approx_traffic>200+</ht:approx_traffic><pubDate>%s</pubDate></item></channel></rss>"
)


async def test_search_trends_ladder_falls_back_from_scrape_to_api(web):
    calls = []

    async def api(ctx_, topics, since):
        calls.append("api")
        from tsp.trends import SignalPoint

        return [SignalPoint(NOW, 77.0, topic_id="t1", region="US")]

    scrape = scrape_rung(lambda region: f"https://trends.test/rss?geo={region}", ["US"])
    sig = SearchTrendsSignal([("scrape", scrape), ("api", api)])
    # scrape blocked (404): the ladder moves on
    pts = await sig.collect(ctx(web), TOPICS, SINCE)
    assert sig.last_rung == "api" and pts[0].value == 77.0 and calls == ["api"]
    # scrape answers: the API is not touched
    web.pages["https://trends.test/rss?geo=US"] = (200, TRENDING % rfc(NOW))
    pts = await sig.collect(ctx(web), TOPICS, SINCE)
    assert sig.last_rung == "scrape" and calls == ["api"]
    assert (pts[0].topic_id, pts[0].value, pts[0].region) == ("t1", 200.0, "US")


async def test_ladder_with_every_rung_down_is_unavailable(web):
    sig = SearchTrendsSignal([("scrape", scrape_rung(lambda r: "https://trends.test/x", ["US"]))])
    with pytest.raises(SignalUnavailable, match="scrape"):
        await sig.collect(ctx(web), TOPICS, SINCE)
