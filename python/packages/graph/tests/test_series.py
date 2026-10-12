from datetime import date

import pytest
from graph_support import ORG, OTHER_ORG
from tsp.graph import GraphService, SeriesService
from tsp.graph.enrich import EnrichContext, enrich_on_demand

D = date


def year(y, v, **kw):
    return {"period_start": D(y, 1, 1), "period_end": D(y, 12, 31), "value": v, **kw}


@pytest.fixture
def sv(session):
    return SeriesService(session)


async def test_series_upsert_and_observations_never_overwrite(sv):
    s = await sv.upsert_series(ORG, "price.widget", "Widget price", unit="USD", frequency="yearly")
    assert (await sv.upsert_series(ORG, "price.widget", "Widget list price")).id == s.id
    first = year(2020, 100, estimate_type="estimate", vintage=D(2020, 6, 1))
    assert await sv.add_observations(ORG, s.id, [first]) == 1
    assert await sv.add_observations(ORG, s.id, [{**first, "value": 999}]) == 0  # same key: kept
    got = await sv.get(ORG, "price.widget")
    assert [float(o.value) for o in got] == [100.0]


async def test_a_revision_keeps_the_earlier_vintage(sv):
    s = await sv.upsert_series(ORG, "gdp", "GDP")
    await sv.add_observations(
        ORG, s.id, [year(2020, 100, estimate_type="estimate", vintage=D(2021, 1, 1))]
    )
    await sv.add_observations(
        ORG, s.id, [year(2020, 110, estimate_type="revised", vintage=D(2022, 1, 1))]
    )
    latest = await sv.get(ORG, "gdp")
    assert [(float(o.value), o.estimate_type) for o in latest] == [(110.0, "revised")]
    known_in_2021 = await sv.get(ORG, "gdp", as_of=D(2021, 6, 1))
    assert [float(o.value) for o in known_in_2021] == [100.0]
    only_estimates = await sv.get(ORG, "gdp", estimate_type="estimate")
    assert [float(o.value) for o in only_estimates] == [100.0]
    assert await sv.get(OTHER_ORG, "gdp") == []


async def test_get_filters_by_range(sv):
    s = await sv.upsert_series(ORG, "k", "K")
    await sv.add_observations(ORG, s.id, [year(y, y) for y in range(2018, 2023)])
    got = await sv.get(ORG, "k", D(2019, 6, 1), D(2021, 3, 1))
    assert [o.period_start.year for o in got] == [2019, 2020, 2021]


async def test_compare_maths(sv):
    s = await sv.upsert_series(ORG, "rev", "Revenue")
    await sv.add_observations(ORG, s.id, [year(2020, 100), year(2022, 150), year(2024, 225)])
    c = await sv.compare(
        ORG, "rev", [(D(2020, 1, 1), D(2020, 12, 31)), (D(2024, 1, 1), D(2024, 12, 31))]
    )
    assert c.values == [100.0, 225.0] and c.absolute == 125.0 and c.pct == pytest.approx(125.0)
    assert c.cagr == pytest.approx(((225 / 100) ** (1 / 4.0) - 1) * 100, rel=1e-3)
    with pytest.raises(ValueError):
        await sv.compare(ORG, "rev", [(D(2020, 1, 1), D(2020, 12, 31))])
    with pytest.raises(LookupError):
        await sv.compare(
            ORG, "rev", [(D(2020, 1, 1), D(2020, 12, 31)), (D(1999, 1, 1), D(1999, 12, 31))]
        )


async def test_compare_adjusts_for_inflation_with_a_deflator_series(sv):
    price = await sv.upsert_series(ORG, "rent", "Rent")
    cpi = await sv.upsert_series(ORG, "cpi", "Price index")
    await sv.add_observations(ORG, price.id, [year(2020, 100), year(2024, 150)])
    await sv.add_observations(ORG, cpi.id, [year(2020, 100), year(2024, 125)])
    c = await sv.compare(
        ORG,
        "rent",
        [(D(2020, 1, 1), D(2020, 12, 31)), (D(2024, 1, 1), D(2024, 12, 31))],
        deflator_key="cpi",
    )
    assert (
        c.values == [100.0, 120.0] and c.adjusted_by == "cpi"
    )  # 150 * 100/125: real growth is 20%


# -- enrichment --------------------------------------------------------------------------------------


class Counting:
    name, version = "lookup", "1"

    def __init__(self, fail=False):
        self.runs, self.fail = 0, fail

    async def run(self, ctx: EnrichContext, target):
        self.runs += 1
        if self.fail:
            raise RuntimeError("upstream down")
        await ctx.graph.upsert_entity(ctx.org_id, "note", f"enriched {target[1]}")


async def test_enrichment_runs_once_then_is_cached_and_a_new_version_reruns(session):
    g = GraphService(session)
    e = await g.upsert_entity(ORG, "company", "Acme")
    en = Counting()
    assert await enrich_on_demand(session, ORG, ("entity", e.id), en) is True
    assert await enrich_on_demand(session, ORG, ("entity", e.id), en) is False
    assert en.runs == 1
    en.version = "2"
    assert await enrich_on_demand(session, ORG, ("entity", e.id), en) is True
    assert (
        await enrich_on_demand(session, OTHER_ORG, ("entity", e.id), en) is True
    )  # per organization
    assert en.runs == 3


async def test_a_failed_enrichment_is_retried(session):
    g = GraphService(session)
    e = await g.upsert_entity(ORG, "company", "Acme")
    bad = Counting(fail=True)
    with pytest.raises(RuntimeError):
        await enrich_on_demand(session, ORG, ("entity", e.id), bad)
    good = Counting()
    assert await enrich_on_demand(session, ORG, ("entity", e.id), good) is True
    assert await enrich_on_demand(session, ORG, ("entity", e.id), good) is False
