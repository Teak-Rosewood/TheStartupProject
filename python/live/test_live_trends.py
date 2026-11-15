"""tsp.trends parsers against the real GDELT and trending-searches feeds."""

import json
import re
from urllib.parse import quote

import httpx
from tsp.trends.signals import GdeltVolumeSignal, Topic, parse_gdelt_timeline, parse_trending_rss

UA = {"User-Agent": "tsp-live-smoke-test (+https://github.com/Teak-Rosewood/TheStartupProject)"}


async def get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=UA) as c:
        r = await c.get(url)
    r.raise_for_status()
    return r


async def test_gdelt_timeline_parses():
    url = GdeltVolumeSignal.URL.format(q=quote('("budget")'), days=3)
    doc = json.loads((await get(url)).text)
    points = parse_gdelt_timeline(doc, "t1")
    assert points, f"GDELT answered but nothing parsed: {str(doc)[:300]}"
    assert all(p.value >= 0 and p.topic_id == "t1" for p in points)


async def test_trending_searches_rss_parses():
    for region in ("US", "IN"):
        body = (await get(f"https://trends.google.com/trending/rss?geo={region}")).content
        titles = re.findall(rb"<item>\s*<title>([^<]+)</title>", body)
        assert titles, f"no items in the {region} feed"
        first = titles[0].decode()
        topic = Topic("t1", "first", first, (first,))
        points = parse_trending_rss(body, [topic], region)
        assert points, f"the {region} feed has items but the parser matched none: {first!r}"
        assert points[0].value > 0 and points[0].region == region
