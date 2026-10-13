import uuid
from datetime import UTC
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from growth_support import CACHE, URL
from sqlalchemy import func, select, text
from tsp.growth import Captcha, Event, Lead, Leads, events, metrics, referrals
from tsp.growth.api import build_router
from tsp.server import Settings, create_app, db_session, get_cache

ANON = "anon-12345678"


# -- events -------------------------------------------------------------------------------------


def test_sanitize_drops_personal_data_by_key_and_by_value():
    clean, dropped = events.sanitize(
        {
            "plan": "pro",
            "plan_name": "Pro",
            "page": "/pricing",
            "email": "a@b.co",
            "note": "write me at x@y.zz",
            "phone": "9876543210",
            "contact_number": "1",
            "customer_name": "Asha",
            "name": "Asha",
            "count": 3,
            "nested": {"a": 1},
            "tel": "+91 98765 43210",
            "ok": True,
        }
    )
    assert clean == {"plan": "pro", "plan_name": "Pro", "page": "/pricing", "count": 3, "ok": True}
    assert set(dropped) == {
        "email",
        "note",
        "phone",
        "contact_number",
        "customer_name",
        "name",
        "nested",
        "tel",
    }


def test_validate_rejects_malformed_events():
    good = {"name": "signup_completed", "anonymous_id": ANON, "properties": {"plan": "free"}}
    assert events.validate(good)["name"] == "signup_completed"
    for bad in (
        {**good, "name": "SignupCompleted"},
        {**good, "name": "x"},
        {**good, "anonymous_id": "short"},
        {**good, "properties": {"k": "v" * 5000}},
        {**good, "properties": []},
    ):
        with pytest.raises(events.InvalidEvent):
            events.validate(bad)


def test_client_timestamps_cannot_rewrite_history():
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    base = {"name": "page_viewed", "anonymous_id": ANON}
    recent = (now - timedelta(minutes=5)).isoformat()
    assert events.validate({**base, "ts": recent}, now)["ts"] == now - timedelta(minutes=5)
    assert (
        events.validate({**base, "ts": (now - timedelta(days=30)).isoformat()}, now)["ts"] == now
    )  # too old
    assert (
        events.validate({**base, "ts": (now + timedelta(hours=1)).isoformat()}, now)["ts"] == now
    )  # future
    assert events.validate({**base, "ts": "garbage"}, now)["ts"] == now


async def test_store_is_all_or_nothing_and_identify_stitches(db):
    batch = [
        {"name": "landing_viewed", "anonymous_id": ANON},
        {
            "name": "signup_started",
            "anonymous_id": ANON,
            "properties": {"email": "x@y.zz", "plan": "free"},
        },
    ]
    async with db.session() as s:
        assert await events.store(s, batch) == 2
        with pytest.raises(events.InvalidEvent):
            await events.store(s, [batch[0], {"name": "BAD", "anonymous_id": ANON}])
        with pytest.raises(events.InvalidEvent, match="at most"):
            await events.store(s, [batch[0]] * 51)
    async with db.session() as s:
        rows = (await s.execute(select(Event).order_by(Event.id))).scalars().all()
        assert len(rows) == 2 and rows[1].properties == {
            "plan": "free"
        }  # the email never reached the table
        assert await events.identify(s, ANON, "user-1") == 2
        assert await events.identify(s, ANON, "user-2") == 0  # already claimed: first sign-in wins


# -- referrals ----------------------------------------------------------------------------------


async def test_referral_codes_are_stable_readable_and_credit_once(db):
    credited = []

    async def reward(session, owner, referred):
        credited.append((owner, referred))

    async with db.session() as s:
        code = await referrals.code_for(s, "owner")
        assert (
            code == await referrals.code_for(s, "owner")
            and len(code) == 8
            and not set(code) & set("01OIL")
        )
        assert not await referrals.record(s, code, "owner", reward)  # cannot refer yourself
        assert not await referrals.record(s, "NOSUCHCODE", "newbie", reward)
        assert await referrals.record(
            s, code.lower(), "newbie", reward
        )  # codes are case-insensitive
        assert not await referrals.record(s, code, "newbie", reward)  # one credit per new user
        assert await referrals.count(s, "owner") == 1
    assert credited == [("owner", "newbie")]


# -- leads --------------------------------------------------------------------------------------


def make_leads(sent, confirmed=None):
    async def send(session, lead, token):
        sent.append((lead.email, token))

    async def done(session, lead):
        (confirmed if confirmed is not None else []).append(lead.email)

    return Leads(
        "k" * 32,
        consent_text="I agree to receive launch updates from Acme.",
        send_confirmation=send,
        on_confirmed=done,
    )


async def test_double_opt_in_flow(db):
    sent, confirmed = [], []
    leads = make_leads(sent, confirmed)
    async with db.session() as s:
        assert await leads.join(
            s,
            " Asha@Example.com ",
            source="hero",
            attribution={"utm_source": "x", "evil": "<script>"},
            ip="203.0.113.5",
        )
        assert await leads.join(
            s, "asha@example.com", source="footer", attribution={}, ip="203.0.113.5"
        )  # the same person again
        assert not await leads.join(s, "not-an-email", source=None, attribution={}, ip=None)
    assert [e for e, _ in sent] == ["asha@example.com"] * 2  # re-sent confirmation, one row
    async with db.session() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.confirmed_at is None and lead.attribution == {"utm_source": "x"}
        assert (
            lead.consent_text.startswith("I agree")
            and lead.consent_ip_hash
            and "203.0.113.5" not in lead.consent_ip_hash
        )
        assert await leads.confirm(s, "forged.token") is None
        token = sent[-1][1]
        assert await leads.confirm(s, token.replace(token[:4], "AAAA")) is None
        assert (await leads.confirm(s, token)).confirmed_at is not None
        await leads.confirm(s, token)  # idempotent
    assert confirmed == ["asha@example.com"]
    sent.clear()
    async with db.session() as s:
        await leads.join(s, "asha@example.com", source=None, attribution={}, ip=None)
    assert sent == []  # already confirmed: no more confirmation emails


async def test_captcha_providers_and_failure_modes():
    def handler(req: httpx.Request):
        body = req.content.decode()
        return httpx.Response(200, json={"success": "response=good" in body})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cap = Captcha("turnstile", "sekret", http)
    assert (
        await cap.verify("good", "1.2.3.4")
        and not await cap.verify("bad")
        and not await cap.verify(None)
    )
    assert await Captcha().verify(None)  # off in development
    broken = Captcha(
        "hcaptcha",
        "s",
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    assert not await broken.verify("good")  # fails closed


# -- HTTP ---------------------------------------------------------------------------------------


@pytest.fixture
async def client(db):
    sent = []
    leads = make_leads(sent)
    captcha = Captcha(
        "turnstile",
        "s",
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"success": b"response=ok" in r.content})
            )
        ),
    )
    state = {"sub": None}

    async def account():
        return SimpleNamespace(sub="user-1")

    async def optional_sub(request: Request):
        return state["sub"]

    saved = []

    async def set_attr(session, sub, attr):
        saved.append((sub, attr))

    import redis.asyncio as redis

    r = redis.from_url(CACHE)
    await r.flushdb()  # rate-limit counters from earlier tests must not leak into this one
    await r.aclose()
    app = create_app(Settings(database_url=URL, cache_url=CACHE))
    app.include_router(
        build_router(
            leads, captcha, get_cache, account, optional_sub, db_session, set_attribution=set_attr
        ),
        prefix="/api",
    )
    with TestClient(app) as c:
        c.sent, c.state, c.saved = sent, state, saved
        yield c


def email():
    return f"{uuid.uuid4().hex[:10]}@example.com"


def test_join_requires_consent_captcha_and_ignores_bots(client):
    ok = {"email": email(), "consent": True, "captcha_token": "ok"}
    assert (
        client.post("/api/leads", json={**ok, "consent": False}).json()["error"]["type"]
        == "consent_required"
    )
    assert (
        client.post("/api/leads", json={**ok, "captcha_token": "nope"}).json()["error"]["type"]
        == "captcha_failed"
    )
    assert client.post("/api/leads", json={**ok, "website": "http://spam"}).json() == {
        "status": "check_your_inbox"
    }
    assert client.sent == []  # the honeypot pretended success but did nothing
    assert client.post("/api/leads", json={**ok, "email": "nope"}).status_code == 422
    r = client.post("/api/leads", json=ok)
    assert (
        r.status_code == 202
        and r.json() == {"status": "check_your_inbox"}
        and len(client.sent) == 1
    )


def test_join_is_rate_limited_per_address(client):
    body = {"email": email(), "consent": True, "captcha_token": "ok"}
    codes = [client.post("/api/leads", json=body).status_code for _ in range(5)]
    assert codes[:3] == [202, 202, 202] and codes[3:] == [429, 429]


def test_confirmation_page_does_not_confirm_until_the_button_is_pressed(client):
    addr = email()
    client.post("/api/leads", json={"email": addr, "consent": True, "captcha_token": "ok"})
    token = client.sent[-1][1]
    page = client.get(f"/api/leads/confirm?token={token}")
    assert page.status_code == 200 and "<form method=post" in page.text
    assert client.get("/api/leads/confirm?token=forged").status_code == 400
    assert client.post(f"/api/leads/confirm?token={token}").json() == {"status": "confirmed"}


def test_events_collect_respect_privacy_signals_and_limits(client):
    batch = {"events": [{"name": "page_viewed", "anonymous_id": ANON, "path": "/pricing"}]}
    assert client.post("/api/events", json=batch).json() == {"stored": 1}
    assert client.post("/api/events", json=batch, headers={"Sec-GPC": "1"}).json() == {"stored": 0}
    assert client.post("/api/events", json=batch, headers={"DNT": "1"}).json() == {"stored": 0}
    bad = client.post("/api/events", json={"events": [{"name": "Bad Name", "anonymous_id": ANON}]})
    assert bad.status_code == 422 and bad.json()["error"]["type"] == "invalid_event"
    client.state["sub"] = "user-1"
    client.post("/api/events", json=batch)
    assert client.post("/api/events/identify", json={"anonymous_id": ANON}).json() == {"linked": 1}


def test_attribution_and_referral_endpoints(client):
    code = client.get("/api/me/referral").json()["code"]
    r = client.post(
        "/api/me/attribution",
        json={
            "attribution": {"utm_source": "newsletter", "junk": "x"},
            "referral_code": "NOSUCH12",
        },
    )
    assert r.json() == {"attribution_saved": True, "referral_credited": False}
    assert client.saved == [("user-1", {"utm_source": "newsletter"})]
    assert client.get("/api/me/referral").json() == {"code": code, "referred": 0}


# -- metrics ------------------------------------------------------------------------------------


async def test_metrics_report_revenue_and_funnel(db):
    async with db.session() as s:
        for stmt in (
            "CREATE TABLE users (sub text primary key, created_at timestamptz default now(), deleted_at timestamptz)",
            "CREATE TABLE plans (code text primary key, amount bigint, interval text)",
            "CREATE TABLE subscriptions (id text primary key, plan_code text, status text, created_at timestamptz default now(), updated_at timestamptz default now())",
            "CREATE TABLE payments (id text primary key, amount bigint, refunded_amount bigint default 0, status text, captured_at timestamptz)",
            "CREATE TABLE refunds (id text primary key, amount bigint, created_at timestamptz default now())",
            "INSERT INTO users (sub) VALUES ('a'), ('b'), ('c')",
            "INSERT INTO plans VALUES ('m', 99900, 'month'), ('y', 1198800, 'year')",
            "INSERT INTO subscriptions (id, plan_code, status) VALUES ('1','m','active'), ('2','y','active'), ('3','m','cancelled'), ('4','m','created')",
            "INSERT INTO payments VALUES ('p1', 99900, 0, 'captured', now()), ('p2', 50000, 10000, 'partially_refunded', now())",
            "INSERT INTO refunds VALUES ('r1', 10000)",
        ):
            await s.execute(text(stmt))
        await s.execute(
            text(
                "INSERT INTO leads (email, consent_text, confirmed_at) VALUES ('a@x.co','t',now()), ('b@x.co','t',NULL)"
            )
        )
        for name, anon in (
            ("landing_viewed", "a1"),
            ("landing_viewed", "a2"),
            ("landing_viewed", "a2"),
            ("signup_completed", "a1"),
        ):
            s.add(Event(name=name, anonymous_id=anon * 4))
        await s.flush()
        o = await metrics.overview(s)
        assert (
            o["users_total"] == 3
            and o["signups_30d"] == 3
            and o["leads_total"] == 2
            and o["leads_confirmed"] == 1
        )
        assert (o["active_subscriptions"], o["mrr"], o["arr"], o["arpu"]) == (
            2,
            99900 + 99900,
            (99900 + 99900) * 12,
            99900,
        )
        assert (o["cancelled_30d"], o["churn_30d_percent"]) == (1, 33.3)
        assert (o["revenue_30d"], o["refunded_30d"]) == (99900 + 40000, 10000)
        assert sum(c for _, c in await metrics.signups_by_day(s, 7)) == 3
        assert (await metrics.top_events(s))[0] == ("landing_viewed", 3, 2)
        assert await metrics.funnel(
            s, ["landing_viewed", "signup_completed", "never_happened"]
        ) == [("landing_viewed", 2), ("signup_completed", 1), ("never_happened", 0)]
        for t in ("refunds", "payments", "subscriptions", "plans", "users"):
            await s.execute(text(f"DROP TABLE {t}"))
    _ = (Depends, func)
