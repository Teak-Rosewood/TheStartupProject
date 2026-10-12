from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from graph_support import ORG, URL
from tsp.db import Database
from tsp.graph import GraphService, SeriesService, build_router


async def app_session():
    """The TestClient runs the app on its own event loop, so it needs its own connection."""
    db = Database(URL)
    try:
        async with db.session() as s:
            yield s
    finally:
        await db.dispose()


async def test_read_only_api(session):
    g = GraphService(session)
    a, b = (
        await g.upsert_entity(ORG, "product", "Widget"),
        await g.upsert_entity(ORG, "company", "Acme"),
    )
    await g.link(ORG, b, "makes", a, valid_from=date(2020, 1, 1), valid_to=date(2022, 1, 1))
    sv = SeriesService(session)
    s = await sv.upsert_series(ORG, "price", "Price")
    await sv.add_observations(ORG, s.id, [{"period_start": date(2020, 1, 1), "value": 5}])
    await session.commit()

    app = FastAPI()
    app.include_router(build_router(lambda: ORG, app_session), prefix="/api")
    c = TestClient(app)

    assert [e["name"] for e in c.get("/api/graph/entities", params={"q": "widg"}).json()] == [
        "Widget"
    ]
    url = f"/api/graph/entities/{a.id}/neighbours"
    assert len(c.get(url).json()) == 1
    assert c.get(url, params={"at": "2023-01-01"}).json() == []
    assert c.get(url, params={"depth": 9}).status_code == 422
    assert c.get("/api/graph/series/price").json()[0]["value"] == 5.0
    assert c.get("/api/graph/series/nope").status_code == 404
