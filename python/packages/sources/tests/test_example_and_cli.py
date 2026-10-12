import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select
from tsp.jobs import JobRegistry
from tsp.sources import SourceRunner, register_jobs
from tsp.sources.cli import import_csv, parse_source_row
from tsp.sources.models import Item as ItemRow
from tsp.sources.models import Source

EXAMPLE = Path(__file__).parent.parent / "examples" / "recipes_connector.py"


@pytest.fixture(scope="module", autouse=True)
def recipes():
    spec = importlib.util.spec_from_file_location("recipes_connector", EXAMPLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def seed(db, strategies):
    async with db.session() as s:
        s.add(
            Source(
                key="recipes-example",
                name="R",
                url="https://recipes.example.com",
                strategies=strategies,
            )
        )


def routes(site):
    site.html("/recipes", "list_1.html")
    site.html("/recipes?page=2", "list_2.html")
    for n in (101, 102, 103):
        site.html(f"/recipes/{n}", f"recipe_{n}.html")


async def test_example_connector_scrapes_fixtures(db, store, cache, http, site):
    routes(site)
    await seed(db, ["scrape", "api_free"])
    r = (await SourceRunner(db, store, cache, http).sync("recipes-example")).streams[0]
    assert (r.strategy_used, r.status, r.items_new) == ("scrape", "ok", 3)
    async with db.session() as s:
        rows = (await s.execute(select(ItemRow).order_by(ItemRow.external_id))).scalars().all()
    assert [x.external_id for x in rows] == ["101", "102", "103"]
    assert "serves four people" in (store.dir / rows[0].raw_uri).read_text()


async def test_example_connector_falls_back_to_api_when_scrape_blocked(
    db, store, cache, http, site
):
    routes(site)
    site.routes["/robots.txt"] = (200, "text/plain", b"User-agent: *\nDisallow: /recipes\n")
    site.json(
        "/api/recipes",
        {
            "results": [
                {"id": 7, "title": "Stew", "body": "# Stew", "updated": "2026-02-01T00:00:00+00:00"}
            ],
            "next": None,
        },
    )
    await seed(db, ["scrape", "api_free"])
    runner = SourceRunner(db, store, cache, http, config={"recipes_api_key": "k"})
    r = (await runner.sync("recipes-example")).streams[0]
    assert (r.strategy_used, r.items_new, r.errors) == ("api_free", 1, 1)
    assert "robots.txt disallows" in r.error_summary


def test_csv_row_parsing_and_validation():
    row = parse_source_row(
        {
            "key": "k",
            "name": "N",
            "url": "https://x.test",
            "strategies": "scrape; api_free",
            "languages": "en;fr",
            "rate_limit_rps": "0.5",
            "paid_cap_monthly": "12.5",
            "notes": "",
        }
    )
    assert row["strategies"] == ["scrape", "api_free"] and row["languages"] == ["en", "fr"]
    assert row["rate_limit_rps"] == 0.5 and "notes" not in row
    with pytest.raises(ValueError, match="unknown strategies"):
        parse_source_row({"key": "k", "name": "N", "url": "u", "strategies": "magic"})
    with pytest.raises(ValueError, match="missing 'url'"):
        parse_source_row({"key": "k", "name": "N"})


async def test_csv_import_upserts_without_wiping_blank_cells(db, tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("key,name,url,tier,strategies\nk,One,https://x.test,primary,scrape\n")
    assert await import_csv(db, str(f)) == 1
    f.write_text("key,name,url,tier,strategies\nk,Renamed,https://x.test,,scrape;api_free\n")
    await import_csv(db, str(f))
    async with db.session() as s:
        row = (await s.execute(select(Source))).scalar_one()
    assert (row.name, row.tier, row.strategies) == ("Renamed", "primary", ["scrape", "api_free"])


async def test_dispatch_enqueues_due_sources_once_per_slot(db, store, cache, http):
    from sqlalchemy import text
    from tsp.crawl import HttpFetcher  # noqa: F401
    from tsp.jobs import JobContext
    from tsp.sources import SourceChecks

    async with db.session() as s:
        s.add(
            Source(
                key="due",
                name="D",
                url="https://d.test",
                schedule="every 1h",
                strategies=["scrape"],
            )
        )
        s.add(Source(key="manual", name="M", url="https://m.test", strategies=["scrape"]))
        s.add(Source(key="off", name="O", url="https://o.test", schedule="every 1h", enabled=False))
    reg = JobRegistry()
    register_jobs(reg, SourceRunner(db, store, cache, http), SourceChecks(db, http, cache))
    for _ in range(2):  # a second dispatch in the same slot must not double-enqueue
        async with db.session() as s:
            await reg.handlers["sources.dispatch"](JobContext(s, 1, 1, "sources.dispatch"), {})
    async with db.session() as s:
        names = (
            (
                await s.execute(
                    text("SELECT payload->>'source_key' FROM jobs WHERE name='sources.sync'")
                )
            )
            .scalars()
            .all()
        )
    assert names == ["due"]
