from types import SimpleNamespace

import httpx
from paywall_support import SECRET, URL, MemCache, NoDns
from tsp.paywall import Paywall, build_router
from tsp.server import Settings, create_app, db_session

ORG = "pub1"


async def test_http_flow_sets_a_cookie_counts_reads_and_walls(db):
    pw = Paywall(SECRET, MemCache(), resolver=NoDns())
    async with db.session() as s:
        await pw.set_policy(s, ORG, "weekly", free_items=1, window_days=7, applies_to=["recipe"])
    user = {"id": None}

    async def optional_user():
        return user["id"]

    async def account():
        return SimpleNamespace(sub=user["id"] or "u1")

    app = create_app(Settings(database_url=URL))
    app.state.db = db
    app.include_router(
        build_router(pw, account, optional_user, db_session, secure_cookie=False), prefix="/api"
    )
    hdr = {"user-agent": "Mozilla/5.0 Firefox/130", "accept-language": "en"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers=hdr
    ) as c:
        r = await c.post(
            "/api/paywall/read", json={"org_id": ORG, "item_id": "a", "type": "recipe"}
        )
        assert r.status_code == 200 and r.json()["allowed"] and "pw_m" in r.cookies
        assert r.headers["cache-control"] == "private, no-store"
        s = await c.get(
            "/api/paywall/status", params={"org_id": ORG, "item": "b", "type": "recipe"}
        )
        assert s.json()["allowed"] is False and s.json()["reason"] == "wall"
        again = await c.post(
            "/api/paywall/read", json={"org_id": ORG, "item_id": "a", "type": "recipe"}
        )
        assert again.json()["allowed"] is True
        # gifts and unlocks need an account
        user["id"] = "u1"
        g = await c.post(
            "/api/paywall/gifts", json={"org_id": ORG, "item_id": "b", "type": "recipe"}
        )
        assert g.status_code == 201
        token = g.json()["token"]
        via_gift = await c.post(
            f"/api/paywall/read?g={token}", json={"org_id": ORG, "item_id": "b", "type": "recipe"}
        )
        assert via_gift.json()["reason"] == "gift"
        no_credits = await c.post(
            "/api/paywall/unlock", json={"org_id": ORG, "item_id": "c", "type": "recipe"}
        )
        assert (
            no_credits.status_code == 402
            and no_credits.json()["error"]["type"] == "insufficient_credits"
        )
        async with db.session() as sess:
            await pw.add_credits(sess, "u1", ORG, 1)
        ok = await c.post(
            "/api/paywall/unlock", json={"org_id": ORG, "item_id": "c", "type": "recipe"}
        )
        assert ok.json() == {"unlocked": True, "newly_unlocked": True, "credits": 0}
