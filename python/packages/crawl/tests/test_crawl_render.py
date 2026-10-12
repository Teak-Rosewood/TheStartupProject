import httpx
import pytest
from crawl_support import drain
from fastapi import FastAPI
from sqlalchemy import select, text
from tsp.crawl import CrawlPage, build_render_router, render_worker
from tsp.crawl.fetch_browser import Rendered
from tsp.vector import Document

ORG = "org1"
TOKEN = "t" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
GOOD = "A fully rendered page about soup and bread, with plenty of readable text. " * 6


async def setup(db, make_service, site, *, render="always", paths=("/app",)):
    svc, registry = make_service(render_remote=True)
    for p in paths:
        site.html(p, "js_only.html")
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")
    from tsp.audit import Actor

    async with db.session() as s:
        src = await svc.create_source(
            s, ORG, collection="docs", seed_url="https://help.test/app", user_sub="u1",
            actor=Actor("u1"), render=render, max_depth=0,
        )  # fmt: skip
    return svc, registry, src.id


@pytest.fixture
def worker_api(db):
    """Run the internal render API in-process; returns a client for the worker to talk to."""

    def build(svc):
        app = FastAPI()
        app.include_router(build_render_router(svc, TOKEN))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=HEADERS
        )

    return build


async def queue_of(db):
    async with db.session() as s:
        rows = (await s.execute(text("SELECT name, queue, status FROM jobs ORDER BY id"))).all()
    return [tuple(r) for r in rows]


async def page_of(db, sid):
    async with db.session() as s:
        return (
            (await s.execute(select(CrawlPage).where(CrawlPage.source_id == sid))).scalars().all()
        )


def renderer_for(html_text=GOOD, calls=None):
    async def render(url):
        if calls is not None:
            calls.append(url)
        return Rendered(url, 200, "App", "# App\n" + html_text, ["https://help.test/other"])

    return render


async def resolve(host):
    return ["93.184.216.34"]


async def test_always_source_goes_to_the_render_queue_and_is_not_fetched_by_api_workers(
    db, make_service, site
):
    svc, registry, sid = await setup(db, make_service, site)
    await drain(db, registry)
    assert ("crawl.render", "crawl.render", "queued") in await queue_of(db)
    assert site.fetched() == []  # the default workers left it alone
    assert (await page_of(db, sid))[0].status == "queued"


async def test_render_worker_renders_and_the_page_is_indexed(db, make_service, site, worker_api):
    svc, registry, sid = await setup(db, make_service, site)
    await drain(db, registry)
    calls = []
    client = worker_api(svc)
    n = await render_worker.run(
        "http://t", TOKEN, renderer=renderer_for(calls=calls), resolver=resolve, client=client,
        poll_seconds=0.01, idle_exit_seconds=0.3,
    )  # fmt: skip
    assert n == 1 and calls == ["https://help.test/app"]
    await drain(db, registry)  # vector.ingest
    (page,) = await page_of(db, sid)
    assert page.status == "ok" and page.vector_document_id
    async with db.session() as s:
        doc = (await s.execute(select(Document))).scalar_one()
        assert doc.created_by == "u1" and doc.status == "ready"
        assert (await s.execute(text("SELECT count(*) FROM vector_chunks"))).scalar_one() > 0
    assert ("crawl.render", "crawl.render", "done") in await queue_of(db)


async def test_auto_source_defers_thin_pages_but_keeps_good_ones(db, make_service, site):
    svc, registry, sid = await setup(db, make_service, site, render="auto", paths=("/app", "/good"))
    site.html("/good", "help_article.html")
    from tsp.audit import Actor

    async with db.session() as s:
        good = await svc.create_source(
            s, ORG, collection="docs", seed_url="https://help.test/good", actor=Actor("u1"),
            render="auto", max_depth=0,
        )  # fmt: skip
    await drain(db, registry)
    assert (await page_of(db, sid))[0].status == "queued"  # thin: waiting for the browser
    assert ("crawl.render", "crawl.render", "queued") in await queue_of(db)
    assert (await page_of(db, good.id))[0].status == "ok"  # fine as plain HTML


async def test_token_is_required_and_checked(db, make_service, site):
    svc, _, _ = await setup(db, make_service, site)
    app = FastAPI()
    app.include_router(build_render_router(svc, TOKEN))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/internal/crawl/render/claim")).status_code == 401
        bad = {"Authorization": "Bearer " + "x" * 32}
        assert (await c.post("/internal/crawl/render/claim", headers=bad)).status_code == 401
        assert (
            await c.post("/internal/crawl/render/1/result", json={}, headers=bad)
        ).status_code == 401
        assert (await c.post("/internal/crawl/render/claim", headers=HEADERS)).status_code == 204
    with pytest.raises(ValueError):
        build_render_router(svc, "short")


async def test_robots_disallow_is_applied_at_claim(db, make_service, site, worker_api):
    svc, registry, sid = await setup(db, make_service, site)
    site.routes["/robots.txt"] = (200, "text/plain", b"User-agent: *\nDisallow: /app\n")
    await drain(db, registry)
    client = worker_api(svc)
    assert (await client.post("/internal/crawl/render/claim")).status_code == 204
    assert (await page_of(db, sid))[0].status == "skipped_robots"


async def test_result_for_unknown_or_unclaimed_job_is_404(db, make_service, site, worker_api):
    svc, registry, sid = await setup(db, make_service, site)
    await drain(db, registry)
    client = worker_api(svc)
    body = {"final_url": "https://help.test/app", "status": 200, "markdown": GOOD}
    assert (await client.post("/internal/crawl/render/999/result", json=body)).status_code == 404
    async with db.session() as s:
        jid = (await s.execute(text("SELECT id FROM jobs WHERE queue='crawl.render'"))).scalar_one()
    # queued, not running: nobody has claimed it
    assert (await client.post(f"/internal/crawl/render/{jid}/result", json=body)).status_code == 404


async def claimed(client):
    r = await client.post("/internal/crawl/render/claim")
    assert r.status_code == 200
    return r.json()


async def test_redirect_to_internal_address_in_result_is_refused(
    db, make_service, site, worker_api
):
    svc, registry, sid = await setup(db, make_service, site)
    await drain(db, registry)
    client = worker_api(svc)
    work = await claimed(client)
    body = {"final_url": "http://169.254.169.254/latest/", "status": 200, "markdown": GOOD}
    assert (
        await client.post(f"/internal/crawl/render/{work['job_id']}/result", json=body)
    ).status_code == 204
    (page,) = await page_of(db, sid)
    assert (
        page.status == "error"
        and page.error.startswith("blocked:")
        and page.vector_document_id is None
    )


async def test_transient_error_retries_and_permanent_error_does_not(
    db, make_service, site, worker_api
):
    svc, registry, sid = await setup(db, make_service, site)
    await drain(db, registry)
    client = worker_api(svc)
    work = await claimed(client)
    url = f"/internal/crawl/render/{work['job_id']}/result"
    await client.post(url, json={"error": "browser timeout", "transient": True})
    assert ("crawl.render", "crawl.render", "queued") in await queue_of(db)  # back in line
    async with db.session() as s:
        await s.execute(
            text("UPDATE jobs SET run_at = now(), attempts = 0 WHERE queue='crawl.render'")
        )
    work = await claimed(client)
    await client.post(
        f"/internal/crawl/render/{work['job_id']}/result", json={"error": "blocked: nope"}
    )
    assert ("crawl.render", "crawl.render", "done") in await queue_of(db)
    assert (await page_of(db, sid))[0].status == "error"


async def test_always_without_any_renderer_still_fails_clearly(db, make_service, site):
    svc, registry = make_service()  # render_remote off, no render_fetcher
    site.html("/app", "js_only.html")
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")
    from tsp.audit import Actor

    async with db.session() as s:
        src = await svc.create_source(
            s, ORG, collection="docs", seed_url="https://help.test/app", actor=Actor("u1"),
            render="always", max_depth=0,
        )  # fmt: skip
    await drain(db, registry)
    (page,) = await page_of(db, src.id)
    assert page.status == "error" and "rendering is not configured" in page.error
