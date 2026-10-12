from datetime import date

import pytest
from conftest import ORG, OTHER_ORG
from sqlalchemy import select
from tsp.graph import Claim, Entity, GraphService, normalise

D = date


def test_normalise_strips_latin_accents_but_keeps_devanagari_marks():
    assert normalise("  Café  MÜLLER ") == "cafe muller"
    assert normalise("दिल्ली") == "दिल्ली"
    assert normalise("") == ""


async def make(g, kind, *names):
    return [await g.upsert_entity(ORG, kind, n) for n in names]


@pytest.fixture
def g(session):
    return GraphService(session, exclusive={"holds_role": "dst"})


# -- entities and resolution ---------------------------------------------------------------------


async def test_upsert_entity_is_idempotent_and_merges(g):
    a = await g.upsert_entity(ORG, "company", "Acme Corp", aliases=["ACME"], attrs={"hq": "Pune"})
    b = await g.upsert_entity(
        ORG, "company", "acme corp", aliases=["Acme Corporation"], attrs={"size": 10}
    )
    assert a.id == b.id and b.attrs == {"hq": "Pune", "size": 10}
    assert {c.entity.id for c in await g.resolve(ORG, "Acme Corporation")} == {a.id}


async def test_upsert_finds_by_external_id_even_when_the_name_changed(g):
    a = await g.upsert_entity(ORG, "company", "Acme", external_ids={"lei": "X1"})
    b = await g.upsert_entity(ORG, "company", "Acme Holdings", external_ids={"lei": "X1"})
    assert a.id == b.id and (await g.resolve(ORG, "Acme Holdings"))[0].entity.id == a.id


async def test_resolve_matches_aliases_in_two_languages(g):
    city = await g.upsert_entity(
        ORG, "place", "Delhi", aliases=[("दिल्ली", "hi"), ("Dilli", "hi"), ("Déli", "es")]
    )
    other = await g.upsert_entity(ORG, "place", "Dehradun")
    hi = await g.resolve(ORG, "दिल्ली", lang="hi")
    es = await g.resolve(ORG, "deli", lang="es")  # accent-insensitive exact match
    assert hi[0].entity.id == city.id and hi[0].how == "exact" and hi[0].score == 1.0
    assert es[0].entity.id == city.id
    assert other.id not in {c.entity.id for c in hi}


async def test_resolve_falls_back_to_trigram_and_respects_kind_and_org(g):
    prod = await g.upsert_entity(ORG, "product", "Widget Pro Max")
    await g.upsert_entity(ORG, "company", "Widget Pro Max Ltd")
    await g.upsert_entity(OTHER_ORG, "product", "Widget Pro Max")
    got = await g.resolve(ORG, "widget pro maks", kind="product")
    assert [c.entity.id for c in got] == [prod.id] and got[0].how == "trigram" and got[0].score < 1
    assert await g.resolve(ORG, "completely different") == []
    assert await g.resolve(ORG, "   ") == []


async def test_search_entities_is_scoped_to_the_organization(g):
    await g.upsert_entity(ORG, "product", "Blue Widget")
    await g.upsert_entity(OTHER_ORG, "product", "Blue Gadget")
    assert [e.name for e in await g.search_entities(ORG, "blue")] == ["Blue Widget"]
    assert await g.search_entities(ORG, "100%") == []  # LIKE wildcards are literal


# -- links, validity, holders ----------------------------------------------------------------------


async def ceo_fixture(g):
    role = await g.upsert_entity(ORG, "role", "CEO of Acme")
    ann = await g.upsert_entity(ORG, "person", "Ann")
    bo = await g.upsert_entity(ORG, "person", "Bo")
    cy = await g.upsert_entity(ORG, "person", "Cy")
    await g.link(ORG, ann, "holds_role", role, valid_from=D(2018, 1, 1))
    await g.link(ORG, bo, "holds_role", role, valid_from=D(2021, 6, 1))  # closes Ann's link
    await g.link(ORG, cy, "holds_role", role, valid_from=D(2024, 3, 15))  # closes Bo's link
    return role, ann, bo, cy


async def test_holders_is_correct_on_three_dates_across_two_handovers(g):
    role, ann, bo, cy = await ceo_fixture(g)

    async def who(at):
        return [e.name for e in await g.holders(ORG, role, at)]

    assert await who(D(2017, 12, 31)) == []
    assert await who(D(2019, 5, 5)) == ["Ann"]
    assert await who(D(2021, 5, 31)) == ["Ann"]
    assert await who(D(2021, 6, 1)) == [
        "Bo"
    ]  # valid_to is exclusive: the handover day belongs to Bo
    assert await who(D(2024, 3, 14)) == ["Bo"]
    assert await who(D(2030, 1, 1)) == ["Cy"]


async def test_valid_at_before_on_and_after_a_change_date(g):
    role, ann, bo, _ = await ceo_fixture(g)
    before = await g.valid_at(ORG, ann, "holds_role", D(2021, 5, 31))
    on = await g.valid_at(ORG, ann, "holds_role", D(2021, 6, 1))
    after = await g.valid_at(ORG, ann, "holds_role", D(2022, 1, 1))
    assert [e.dst for e in before] == [("entity", role.id)]
    assert on == [] and after == []
    assert before[0].valid_to == D(2021, 6, 1)
    assert [e.src for e in await g.valid_at(ORG, role, "holds_role", D(2021, 6, 1))] == [
        ("entity", bo.id)
    ]


async def test_without_an_exclusive_rel_links_overlap(session):
    g = GraphService(session)  # nothing opted in
    role = await g.upsert_entity(ORG, "role", "Reviewer")
    a, b = await make(g, "person", "A", "B")
    await g.link(ORG, a, "holds_role", role, valid_from=D(2020, 1, 1))
    await g.link(ORG, b, "holds_role", role, valid_from=D(2021, 1, 1))
    assert {e.name for e in await g.holders(ORG, role, D(2022, 1, 1))} == {"A", "B"}


async def test_link_validates_input(g):
    a = await g.upsert_entity(ORG, "x", "a")
    with pytest.raises(ValueError):
        await g.link(ORG, a, "r", a, valid_from=D(2020, 1, 2), valid_to=D(2020, 1, 1))
    with pytest.raises(ValueError):
        await g.link(ORG, ("planet", a.id), "r", a)


async def test_links_of_other_organizations_are_invisible(g):
    role = await g.upsert_entity(ORG, "role", "r")
    p = await g.upsert_entity(ORG, "person", "p")
    await g.link(ORG, p, "holds_role", role, valid_from=D(2020, 1, 1))
    assert await g.holders(OTHER_ORG, role, D(2021, 1, 1)) == []
    assert await g.neighbours(OTHER_ORG, p) == []


# -- neighbours and paths --------------------------------------------------------------------------


async def chain(g, n=5):
    """a0 - a1 - a2 - ... with a back edge a2 -> a0 (a cycle)."""
    ents = [await g.upsert_entity(ORG, "org", f"a{i}") for i in range(n)]
    for x, y in zip(ents, ents[1:], strict=False):
        await g.link(ORG, x, "partner_of", y)
    await g.link(ORG, ents[2], "partner_of", ents[0])
    return ents


async def test_neighbours_by_depth_with_cycles_and_rel_filter(g):
    a = await chain(g)
    d1 = await g.neighbours(ORG, a[0])
    assert {(e.src[1], e.dst[1]) for e in d1} == {(a[0].id, a[1].id), (a[2].id, a[0].id)}
    d3 = await g.neighbours(ORG, a[0], depth=3)
    assert len(d3) == 5 and len({e.id for e in d3}) == 5  # every edge once, despite the cycle
    assert max(e.depth for e in d3) == 3 and d3[0].depth == 1
    assert await g.neighbours(ORG, a[0], rels=["cites"], depth=3) == []


async def test_neighbours_respect_validity_dates(g):
    a, b = await make(g, "org", "a", "b")
    await g.link(ORG, a, "partner_of", b, valid_from=D(2020, 1, 1), valid_to=D(2022, 1, 1))
    assert len(await g.neighbours(ORG, a, at=D(2021, 1, 1))) == 1
    assert await g.neighbours(ORG, a, at=D(2022, 1, 1)) == []
    assert len(await g.neighbours(ORG, a)) == 1  # no date: every edge


async def test_paths_finds_a_three_hop_path_and_stops_at_cycles(g):
    a = await chain(g)
    paths = await g.paths(ORG, a[0], a[3], max_hops=3)
    assert [len(p.edges) for p in paths] == [2, 3]  # via the back edge a0-a2-a3, and a0-a1-a2-a3
    assert paths[1].nodes == [("entity", x.id) for x in a[:4]]
    assert all(len(set(p.nodes)) == len(p.nodes) for p in paths)  # never revisits a node
    assert await g.paths(ORG, a[0], a[4], max_hops=2) == []
    assert (await g.paths(ORG, a[0], a[4], max_hops=3))[0].edges[-1].dst == ("entity", a[4].id)


async def test_paths_between_different_node_kinds(g):
    e = await g.upsert_entity(ORG, "org", "acme")
    doc = await g.add_document(ORG, title="Press release")
    claim = await g.add_claim(ORG, e.id, "announced", "Acme announced X", document_id=doc.id)
    await g.link(ORG, claim, "extracted_from", doc)
    await g.link(ORG, e, "subject_of", claim)
    (path,) = await g.paths(ORG, e, doc, max_hops=2)
    assert [k for k, _ in path.nodes] == ["entity", "claim", "document"]


# -- claims and stances ----------------------------------------------------------------------------


async def test_claims_keep_provenance_and_reject_ambiguous_objects(g, session):
    p = await g.upsert_entity(ORG, "product", "Widget")
    doc = await g.add_document(ORG, title="Review", kind="review", lang="en")
    claim = await g.add_claim(
        ORG, p.id, "battery_life_hours", "Battery lasts 12 hours", object_value=12,
        document_id=doc.id, quote=(10, 31), confidence=0.8, extracted_by="m@v3",
    )  # fmt: skip
    got = (await session.execute(select(Claim).where(Claim.id == claim.id))).scalar_one()
    assert (got.quote_start, got.quote_end, got.status, got.object_value) == (10, 31, "auto", 12)
    with pytest.raises(ValueError):
        await g.add_claim(ORG, p.id, "x", "x", object_id=p.id, object_value=1)


async def test_stances_return_each_holders_latest_as_of_a_date(g):
    a, b = await make(g, "person", "A", "B")
    await g.set_stance(ORG, a.id, "pricing", -0.5, D(2020, 1, 1))
    await g.set_stance(ORG, a.id, "pricing", 0.5, D(2022, 1, 1))
    await g.set_stance(ORG, b.id, "pricing", 1.0, D(2021, 1, 1))
    now = {s.holder_id: float(s.position) for s in await g.stances(ORG, "pricing")}
    then = {s.holder_id: float(s.position) for s in await g.stances(ORG, "pricing", D(2021, 6, 1))}
    assert now == {a.id: 0.5, b.id: 1.0} and then == {a.id: -0.5, b.id: 1.0}


async def test_delete_org_removes_only_that_org(g, session):
    await ceo_fixture(g)
    other = await g.upsert_entity(OTHER_ORG, "x", "keep")
    await g.delete_org(ORG)
    left = (await session.execute(select(Entity))).scalars().all()
    assert [e.id for e in left] == [other.id]
