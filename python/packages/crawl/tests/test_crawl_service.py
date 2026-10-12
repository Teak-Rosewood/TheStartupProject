import pytest
from crawl_support import drain
from sqlalchemy import select, text
from tsp.audit import Actor
from tsp.crawl import CrawlPage, CrawlSource
from tsp.vector import BadRequest, Document, LimitExceeded, NotFound

ORG, OTHER = "org1", "org2"
ADMIN = Actor("u1", "user")
SEED = "https://help.test/recipes"


async def setup(db, make_service, site, *, org=ORG, **kw):
    svc, registry = make_service(**kw.pop("service", {}))
    site.html("/recipes", "list_1.html")
    site.html("/recipes?page=2", "list_2.html")
    for n in (101, 102, 103):
        site.html(f"/recipes/{n}", f"recipe_{n}.html")
    async with db.session() as s:
        await svc.vector.create_collection(s, org, "docs")
    return svc, registry


async def add(db, svc, org=ORG, **kw):
    async with db.session() as s:
        src = await svc.create_source(
            s, org, collection="docs", seed_url=SEED, user_sub="u1", actor=ADMIN, **kw
        )
    return src.id


async def pages(db, source_id):
    async with db.session() as s:
        rows = (
            await s.execute(
                select(CrawlPage).where(CrawlPage.source_id == source_id).order_by(CrawlPage.url)
            )
        ).scalars()
        return {p.url.rsplit("help.test", 1)[1]: p for p in rows}


async def test_crawl_follows_links_and_indexes_pages(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc)
    await drain(db, registry)
    got = await pages(db, sid)
    assert sorted(got) == [
        "/recipes",
        "/recipes/101",
        "/recipes/102",
        "/recipes/103",
        "/recipes?page=2",
    ]
    assert {p.status for p in got.values()} == {"ok"}
    assert (
        got["/recipes"].depth == 0
        and got["/recipes/101"].depth == 1
        and got["/recipes/103"].depth == 2
    )
    async with db.session() as s:
        docs = (await s.execute(select(Document))).scalars().all()
        assert len(docs) == 5 and {d.status for d in docs} == {"ready"}
        assert all(d.created_by == "u1" and d.org_id == ORG for d in docs)


async def test_depth_include_exclude_and_host_limits(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    site.text(
        "/recipes",
        "<html><body><main><p>"
        + "Welcome to the kitchen index page, with plenty of words to extract. " * 6
        + '</p><a href="/recipes/101">a</a> <a href="/recipes/102">b</a> <a href="https://other.test/x">off</a>'
        ' <a href="/private/1">p</a></main></body></html>',
    )
    sid = await add(
        db,
        svc,
        max_depth=1,
        exclude=["https://help.test/recipes/102"],
        include=["https://help.test/recipes*"],
    )
    await drain(db, registry)
    assert sorted(await pages(db, sid)) == [
        "/recipes",
        "/recipes/101",
    ]  # off-host, excluded and out-of-include dropped
    sid2 = await add(db, svc, max_depth=0)
    await drain(db, registry)
    assert sorted(await pages(db, sid2)) == ["/recipes"]


async def test_plan_is_idempotent(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc)
    await svc.plan(sid)
    await svc.plan(sid)
    async with db.session() as s:
        assert len((await s.execute(select(CrawlPage))).scalars().all()) == 1
        n = (
            await s.execute(text("SELECT count(*) FROM jobs WHERE name='crawl.fetch'"))
        ).scalar_one()
    assert n == 1  # two plans in the same minute queue one fetch


async def test_refetch_with_same_content_does_not_reingest(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc, max_depth=0)
    await drain(db, registry)
    first = (await pages(db, sid))["/recipes"]
    async with db.session() as s:
        await svc.plan(sid)  # same minute: nothing new queued, so force a second fetch directly
    await svc.fetch_page(first.id)
    again = (await pages(db, sid))["/recipes"]
    assert again.status == "not_modified"  # the fake site answers 304 to our etag
    async with db.session() as s:
        assert (
            await s.execute(text("SELECT count(*) FROM jobs WHERE name='vector.ingest'"))
        ).scalar_one() == 1
        assert len((await s.execute(select(Document))).scalars().all()) == 1


async def test_changed_page_replaces_its_document(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc, max_depth=0)
    await drain(db, registry)
    before = (await pages(db, sid))["/recipes"]
    site.text(
        "/recipes",
        "<html><body><main><p>"
        + "A completely rewritten page about soup and bread. " * 8
        + "</p></main></body></html>",
    )
    site.routes["/recipes"] = (*site.routes["/recipes"],)  # no etag match: content changed
    async with db.session() as s:
        p = await s.get(CrawlPage, before.id)
        p.etag = None
    await svc.fetch_page(before.id)
    await drain(db, registry)
    after = (await pages(db, sid))["/recipes"]
    assert after.vector_document_id != before.vector_document_id
    async with db.session() as s:
        ids = (await s.execute(select(Document.id))).scalars().all()
    assert ids == [after.vector_document_id]  # the old document and its chunks are gone


async def test_removed_page_deletes_its_document(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc)
    await drain(db, registry)
    victim = (await pages(db, sid))["/recipes/102"]
    site.routes["/recipes/102"] = (410, "text/plain", b"")
    async with db.session() as s:
        (await s.get(CrawlPage, victim.id)).etag = None
    await svc.fetch_page(victim.id)
    gone = (await pages(db, sid))["/recipes/102"]
    assert (gone.status, gone.vector_document_id, gone.http_status) == ("gone", None, 410)
    async with db.session() as s:
        assert (
            victim.vector_document_id not in (await s.execute(select(Document.id))).scalars().all()
        )
        assert len((await s.execute(select(Document))).scalars().all()) == 4


async def test_robots_disallow_skips_page(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    site.routes["/robots.txt"] = (200, "text/plain", b"User-agent: *\nDisallow: /recipes/102\n")
    sid = await add(db, svc)
    await drain(db, registry)
    got = await pages(db, sid)
    assert got["/recipes/102"].status == "skipped_robots" and got["/recipes/101"].status == "ok"
    assert "/recipes/102" not in site.fetched()


async def test_monthly_page_limit_stops_fetching(db, make_service, site):
    async def limits(session, org):
        return {"crawl_pages_month": 2}

    svc, registry = await setup(db, make_service, site, service={"limits": limits})
    sid = await add(db, svc)
    # The cap is checked before each fetch, so concurrent workers can overshoot by up to
    # (concurrency - 1) pages; one worker makes it exact.
    await drain(db, registry, concurrency=1)
    statuses = [p.status for p in (await pages(db, sid)).values()]
    assert statuses.count("ok") == 2
    assert "monthly page limit reached" in {p.error for p in (await pages(db, sid)).values()}


async def test_max_pages_caps_the_frontier(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc, max_pages=3)
    await drain(db, registry)
    assert len(await pages(db, sid)) == 3


async def test_plan_limits_on_create(db, make_service, site):
    async def limits(session, org):
        return {"crawl_depth": 1, "crawl_refresh_min_hours": 24}

    svc, _ = await setup(db, make_service, site, service={"limits": limits})
    with pytest.raises(LimitExceeded, match="depth"):
        await add(db, svc, max_depth=2)
    with pytest.raises(LimitExceeded, match="24 hours"):
        await add(db, svc, max_depth=1, refresh_hours=1)
    await add(db, svc, max_depth=1, refresh_hours=24)


@pytest.mark.parametrize(
    "seed",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/",
        "ftp://help.test/",
        "http://localhost/",
    ],
)
async def test_internal_seed_is_refused(db, make_service, site, seed):
    svc, _ = make_service(resolver=None)
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")
    async with db.session() as s:
        with pytest.raises(BadRequest):
            await svc.create_source(s, ORG, collection="docs", seed_url=seed, actor=ADMIN)


async def test_page_redirecting_inside_is_refused_at_fetch(db, make_service, site):
    async def resolver(host):
        return ["10.0.0.5"] if host == "internal.corp" else ["93.184.216.34"]

    svc, registry = make_service(resolver=resolver)
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")
    site.routes["/recipes"] = (302, "text/plain", b"")
    orig = site.handler

    def redirect(request):
        if request.url.path == "/recipes":
            import httpx

            return httpx.Response(302, headers={"location": "http://internal.corp/admin"})
        return orig(request)

    svc.http._client._transport = __import__("httpx").MockTransport(redirect)
    sid = await add(db, svc, max_depth=0)
    await drain(db, registry)
    page = (await pages(db, sid))["/recipes"]
    assert page.status == "error" and page.error.startswith("blocked:")


async def test_missing_collection_and_foreign_org(db, make_service, site):
    svc, _ = await setup(db, make_service, site)
    async with db.session() as s:
        with pytest.raises(NotFound):
            await svc.create_source(s, ORG, collection="nope", seed_url=SEED, actor=ADMIN)
    sid = await add(db, svc)
    async with db.session() as s:
        with pytest.raises(NotFound):
            await svc.source(s, OTHER, sid)
        with pytest.raises(NotFound):
            await svc.pages(s, OTHER, sid)
        with pytest.raises(NotFound):
            await svc.delete_source(s, OTHER, sid, actor=ADMIN)
        assert await svc.sources(s, OTHER) == []


async def test_delete_source_removes_pages_and_documents(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc)
    await drain(db, registry)
    async with db.session() as s:
        await svc.delete_source(s, ORG, sid, actor=ADMIN)
        assert await svc.sources(s, ORG) == []  # hidden at once
    await drain(db, registry)
    async with db.session() as s:
        assert (await s.execute(select(CrawlSource))).first() is None
        assert (await s.execute(select(CrawlPage))).first() is None
        assert (await s.execute(select(Document))).first() is None
        assert (await s.execute(text("SELECT count(*) FROM vector_chunks"))).scalar_one() == 0


async def test_paused_source_does_not_fetch_and_recrawl_needs_active(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc)
    async with db.session() as s:
        await svc.set_status(s, ORG, sid, "paused", actor=ADMIN)
    await drain(db, registry)
    assert site.fetched() == []
    async with db.session() as s:
        with pytest.raises(BadRequest):
            await svc.recrawl(s, ORG, sid)
        await svc.set_status(s, ORG, sid, "active", actor=ADMIN)
        await svc.recrawl(s, ORG, sid)
    await drain(db, registry)
    assert len(await pages(db, sid)) == 5


async def test_refresh_queues_only_due_sources(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    sid = await add(db, svc, refresh_hours=24)
    await drain(db, registry)
    assert await svc.refresh_due() == 0  # just crawled
    async with db.session() as s:
        from datetime import datetime, timedelta, timezone

        (await s.get(CrawlSource, sid)).last_run_at = datetime.now(timezone.utc) - timedelta(
            hours=25
        )
    assert await svc.refresh_due() == 1


async def test_sitemap_adds_urls_and_bad_sitemap_is_ignored(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    site.text(
        "/sitemap.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://help.test/recipes/101</loc></url>'
        "<url><loc>https://elsewhere.test/x</loc></url></urlset>",
        "application/xml",
    )
    sid = await add(db, svc, sitemap_url="https://help.test/sitemap.xml", max_depth=0)
    await drain(db, registry)
    assert sorted(await pages(db, sid)) == ["/recipes", "/recipes/101"]
    site.text("/sitemap.xml", "not xml", "application/xml")
    sid2 = await add(db, svc, sitemap_url="https://help.test/sitemap.xml", max_depth=0)
    await drain(db, registry)
    assert sorted(await pages(db, sid2)) == ["/recipes"]


async def test_metering_counts_fetched_pages(db, make_service, site):
    svc, registry = await setup(db, make_service, site)
    await add(db, svc)
    await drain(db, registry)
    async with db.session() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM usage_records WHERE kind='crawl.page' AND org_id='org1'")
            )
        ).scalar_one()
    assert n == 5
