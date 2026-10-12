from types import SimpleNamespace

import httpx
import pytest
from crawl_support import drain
from fastapi import FastAPI
from sqlalchemy import select
from tsp.crawl import CrawlSource, build_router, register_with_compliance

ORG = "org1"


class FakeCompliance:
    def __init__(self):
        self.exporters, self.erasers = {}, {}

    def register_exporter(self, name, fn):
        self.exporters[name] = fn

    def register_eraser(self, name, fn):
        self.erasers[name] = fn


@pytest.fixture
async def client(db, make_service, site):
    svc, registry = make_service()
    site.html("/recipes", "list_1.html")
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")

    async def session():
        async with db.session() as s:
            yield s

    def member(role):
        async def dep(org_id: str):
            if org_id != ORG:
                from fastapi import HTTPException

                raise HTTPException(403, "not a member")

        return dep

    app = FastAPI()
    app.include_router(build_router(svc, lambda: SimpleNamespace(sub="u1"), member, session))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, svc, registry, db


async def test_api_roundtrip(client):
    http, svc, registry, db = client
    base = f"/orgs/{ORG}/crawl/sources"
    r = await http.post(
        base, json={"collection": "docs", "seed_url": "https://help.test/recipes", "max_depth": 0}
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert (await http.get(base)).json()[0]["id"] == sid
    paused = await http.patch(f"{base}/{sid}", json={"status": "paused"})
    assert paused.json()["status"] == "paused"
    assert (await http.patch(f"{base}/{sid}", json={"status": "bogus"})).status_code == 422
    assert (await http.post(f"{base}/{sid}/recrawl")).status_code == 422  # paused
    assert (await http.get(f"{base}/nope/pages")).status_code == 404
    assert (await http.delete(f"{base}/{sid}")).status_code == 202
    assert (await http.get(base)).json() == []


async def test_api_rejects_internal_seed_and_other_orgs(client):
    http, *_ = client
    bad = await http.post(
        f"/orgs/{ORG}/crawl/sources",
        json={"collection": "docs", "seed_url": "http://169.254.169.254/"},
    )
    assert bad.status_code == 422, bad.text
    assert "non-public" in bad.json()["detail"]["message"]
    assert (await http.get("/orgs/other/crawl/sources")).status_code == 403


async def test_compliance_export_and_erase(db, make_service, site):
    svc, registry = make_service()
    site.html("/recipes", "list_1.html")
    async with db.session() as s:
        await svc.vector.create_collection(s, ORG, "docs")
        from tsp.audit import Actor

        await svc.create_source(
            s,
            ORG,
            collection="docs",
            seed_url="https://help.test/recipes",
            max_depth=0,
            user_sub="leaver",
            actor=Actor("leaver"),
        )
    await drain(db, registry)
    comp = FakeCompliance()
    register_with_compliance(comp, svc)
    async with db.session() as s:
        exported = await comp.exporters["crawl"](s, "leaver")
    assert exported[0]["seed_url"] == "https://help.test/recipes" and exported[0]["pages"] == [
        "https://help.test/recipes"
    ]
    async with db.session() as s:
        await comp.erasers["crawl"](s, "leaver")
    await drain(db, registry)
    async with db.session() as s:
        assert (await s.execute(select(CrawlSource))).first() is None
