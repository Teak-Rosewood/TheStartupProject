import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from admin_support import CLIENT, DOMAIN, URL, sign_in
from sqlalchemy import select
from tsp.accounts import User
from tsp.audit import AuditLog
from tsp.compliance import DataRequest
from tsp.db import Database

SUB = "bbbbbbbb-0000-0000-0000-000000000002"


def run(coro):
    return asyncio.run(coro)


async def _seed():
    d = Database(URL)
    async with d.session() as s:
        s.add(User(sub=SUB, email="victim@example.com", name="Vic"))
        await s.flush()
    await d.dispose()


async def _q(stmt):
    d = Database(URL)
    async with d.session() as s:
        rows = (await s.execute(stmt)).all()
    await d.dispose()
    return rows


def csrf_of(client):
    page = client.get("/api/admin/staff-actions")
    assert page.status_code == 200
    import re

    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)


def test_anonymous_visitors_are_sent_to_sign_in(env):
    for path in ("/api/admin/", "/api/admin/user/list", "/api/admin/staff-actions"):
        r = env.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/api/admin/oidc/start"


def test_sign_in_uses_pkce_state_nonce_and_a_confidential_client(env, staff):
    r, q = sign_in(env, staff)
    assert r.status_code == 303 and r.headers["location"] == "/api/admin/"
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT]
    assert q["redirect_uri"] == ["https://app.example.com/api/admin/oidc/callback"]
    req = staff.token_requests[0]
    body = parse_qs(req.content.decode())
    verifier = body["code_verifier"][0]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == q["code_challenge"][0]  # PKCE verifier matches the challenge sent earlier
    assert req.headers["authorization"].startswith("Basic ")  # client secret, server side only
    assert env.get("/api/admin/").status_code == 200


def test_bad_callbacks_are_rejected(env, staff):
    env.get("/api/admin/oidc/start")
    assert (
        env.get("/api/admin/oidc/callback", params={"code": "abc", "state": "forged"}).status_code
        == 400
    )
    assert (
        env.get("/api/admin/oidc/callback", params={"code": "abc", "state": "x"}).status_code == 400
    )  # state consumed
    env.get("/api/admin/oidc/start")
    assert env.get("/api/admin/oidc/callback", params={"error": "access_denied"}).status_code == 400
    r = env.get("/api/admin/oidc/start")
    q = parse_qs(urlparse(r.headers["location"]).query)
    staff.nonce = "attacker-chosen"  # id token minted for a different login attempt
    assert (
        env.get(
            "/api/admin/oidc/callback", params={"code": "abc", "state": q["state"][0]}
        ).status_code
        == 400
    )
    env.get("/api/admin/oidc/start")
    staff.token_status = 400
    assert (
        env.get("/api/admin/oidc/callback", params={"code": "abc", "state": "z"}).status_code == 400
    )
    assert env.get("/api/admin/").status_code == 303  # never signed in


def test_users_without_a_staff_group_cannot_sign_in(env, staff):
    r, _ = sign_in(env, staff, groups=["customers"])
    assert r.status_code == 403
    assert env.get("/api/admin/").status_code == 303


def test_ip_allowlist(env, staff):
    env.settings.allowed_cidrs = ["203.0.113.0/24"]
    assert (
        env.get("/api/admin/oidc/start", headers={"x-forwarded-for": "198.51.100.1"}).status_code
        == 403
    )
    assert (
        env.get("/api/admin/oidc/start", headers={"x-forwarded-for": "203.0.113.7"}).status_code
        == 303
    )
    assert (
        env.get("/api/admin/oidc/start").status_code == 403
    )  # no forwarded address: not on the list
    assert env.get("/api/admin/").status_code == 403
    env.settings.allowed_cidrs = []


def test_roles_limit_what_each_view_shows(env, staff):
    sign_in(env, staff, ["finance"])
    assert env.get("/api/admin/user/list").status_code == 200
    assert env.get("/api/admin/audit-log/list").status_code == 403
    assert env.get("/api/admin/api-key/list").status_code == 403
    env.cookies.clear()
    sign_in(env, staff, ["support"])
    assert env.get("/api/admin/audit-log/list").status_code == 200
    env.cookies.clear()
    sign_in(env, staff, ["support", "superadmin"])  # highest role wins
    assert env.get("/api/admin/consent/list").status_code == 200


def test_views_are_read_only(env, staff):
    sign_in(env, staff, ["superadmin"])
    for path in ("/api/admin/user/create", "/api/admin/user/edit/x"):
        assert env.get(path).status_code in (403, 404)
    assert env.post("/api/admin/user/delete?pks=x").status_code in (403, 404, 405)


def test_details_views_are_audited_and_hide_secrets(env, staff):
    run(_seed())
    sign_in(env, staff, ["support"])
    r = env.get(f"/api/admin/user/details/{SUB}")
    assert r.status_code == 200 and "victim@example.com" in r.text
    rows = run(
        _q(select(AuditLog.action, AuditLog.actor_sub, AuditLog.target_type, AuditLog.target_id))
    )
    assert ("admin.viewed", staff.sub, "user", SUB) in [tuple(x) for x in rows]


def test_staff_action_needs_csrf_reason_and_valid_target(env, staff):
    run(_seed())
    sign_in(env, staff, ["support"])
    token = csrf_of(env)
    base = {"csrf": token, "target": SUB, "action": "disable_user", "reason": "abuse report #123"}
    assert (
        "invalid form token"
        in env.post("/api/admin/staff-actions", data={**base, "csrf": "nope"}).text
    )
    assert (
        "at least 10 characters"
        in env.post("/api/admin/staff-actions", data={**base, "reason": "short"}).text
    )
    assert (
        "not a valid user id"
        in env.post("/api/admin/staff-actions", data={**base, "target": "../etc"}).text
    )
    assert (
        run(_q(select(User.disabled_at).where(User.sub == SUB)))[0][0] is None
    )  # nothing happened yet


def test_disable_and_enable_are_audited_with_the_reason(env, staff):
    run(_seed())
    sign_in(env, staff, ["support"])
    token = csrf_of(env)
    r = env.post(
        "/api/admin/staff-actions",
        data={
            "csrf": token,
            "target": SUB,
            "action": "disable_user",
            "reason": "abuse report #123",
        },
    )
    assert "disable user done" in r.text
    assert run(_q(select(User.disabled_at).where(User.sub == SUB)))[0][0] is not None
    assert ("disable", SUB) in env.identity.calls
    env.post(
        "/api/admin/staff-actions",
        data={
            "csrf": token,
            "target": SUB,
            "action": "enable_user",
            "reason": "appeal accepted #124",
        },
    )
    assert run(_q(select(User.disabled_at).where(User.sub == SUB)))[0][0] is None
    rows = run(
        _q(
            select(AuditLog.action, AuditLog.actor_role, AuditLog.meta)
            .where(AuditLog.action.like("user.%"))
            .order_by(AuditLog.id)
        )
    )
    assert [(r[0], r[1]) for r in rows] == [
        ("user.disabled", "support"),
        ("user.enabled", "support"),
    ]
    assert rows[0][2]["reason"] == "abuse report #123"


def test_support_cannot_execute_a_deletion_but_superadmin_can(env, staff):
    run(_seed())

    async def request_deletion():
        d = Database(URL)
        async with d.session() as s:
            await env.compliance.request_delete(s, SUB)
        await d.dispose()

    run(request_deletion())
    sign_in(env, staff, ["support"])
    token = csrf_of(env)
    data = {
        "csrf": token,
        "target": SUB,
        "action": "execute_deletion",
        "reason": "grace period over, ticket 9",
    }
    assert "cannot run execute_deletion" in env.post("/api/admin/staff-actions", data=data).text
    assert run(_q(select(DataRequest.status)))[0][0] == "pending"
    env.cookies.clear()
    sign_in(env, staff, ["superadmin"])
    data["csrf"] = csrf_of(env)
    assert "execute deletion done" in env.post("/api/admin/staff-actions", data=data).text
    assert run(_q(select(DataRequest.status)))[0][0] == "completed"
    assert ("delete", SUB) in env.identity.calls
    assert run(_q(select(User.deleted_at).where(User.sub == SUB)))[0][0] is not None


def test_session_expiry_and_logout(env, staff):
    sign_in(env, staff)
    assert env.get("/api/admin/").status_code == 200
    r = env.get("/api/admin/logout")
    assert r.status_code in (302, 303) and r.headers["location"].startswith(DOMAIN + "/logout?")
    assert env.get("/api/admin/").status_code == 303


def test_secret_columns_never_reach_the_page(env, staff):
    from tsp.accounts import Org

    async def seed_key():
        d = Database(URL)
        async with d.session() as s:
            await env.accounts.ensure_user(s, SUB, email="o@example.com", email_verified=True)
            org = await env.accounts.create_org(s, SUB, "Acme")
            key, raw = await env.accounts.create_api_key(s, org.id, SUB, "ci")
            out = key.id, key.key_hash, raw
        await d.dispose()
        return out

    key_id, key_hash, raw = run(seed_key())
    sign_in(env, staff, ["support"])
    page = env.get(f"/api/admin/api-key/details/{key_id}")
    assert page.status_code == 200 and "ci" in page.text
    assert key_hash not in page.text and raw not in page.text
    assert key_hash not in env.get("/api/admin/api-key/list").text
    assert (
        env.get("/api/admin/invite/list").status_code == 200
    )  # token hashes are excluded from details too
    _ = Org


def _seed_payment():
    from tsp.payments import Payment

    async def go():
        d = Database(URL)
        async with d.session() as s:
            p = Payment(
                org_id="org1",
                provider="razorpay",
                provider_payment_id="pay_x",
                amount=11800,
                currency="INR",
                status="captured",
            )
            s.add(p)
            await s.flush()
            pid = p.id
        await d.dispose()
        return pid

    return run(go())


def test_finance_sees_money_but_not_the_audit_log(env, staff):
    sign_in(env, staff, ["finance"])
    for view in ("payment", "subscription", "invoice", "refund", "transfer", "user"):
        assert env.get(f"/api/admin/{view}/list").status_code == 200, view
    assert env.get("/api/admin/audit-log/list").status_code == 403
    assert env.get("/api/admin/consent/list").status_code == 403
    env.cookies.clear()
    sign_in(env, staff, ["support"])
    assert env.get("/api/admin/payment/list").status_code == 200
    assert env.get("/api/admin/invoice/list").status_code == 403  # support cannot see invoices


def test_only_finance_and_superadmin_can_refund_and_it_is_audited(env, staff):
    pid = _seed_payment()
    data = {
        "target": pid,
        "action": "refund_payment",
        "reason": "duplicate charge, ticket 55",
        "amount": "500",
    }
    sign_in(env, staff, ["support"])
    data["csrf"] = csrf_of(env)
    assert "cannot run refund_payment" in env.post("/api/admin/staff-actions", data=data).text
    assert env.gateway_calls == []
    env.cookies.clear()
    sign_in(env, staff, ["finance"])
    data["csrf"] = csrf_of(env)
    assert "refund requested" in env.post("/api/admin/staff-actions", data=data).text
    path, body = env.gateway_calls[0]
    assert path == "/v1/payments/pay_x/refund" and b'"amount":500' in body.replace(b" ", b"")
    rows = run(
        _q(
            select(AuditLog.action, AuditLog.actor_role, AuditLog.meta).where(
                AuditLog.action == "billing.refund_requested"
            )
        )
    )
    assert [(r[0], r[1], r[2]["reason"]) for r in rows] == [
        ("billing.refund_requested", "finance", "duplicate charge, ticket 55")
    ]
    bad = {**data, "amount": "-5"}
    assert "positive whole number" in env.post("/api/admin/staff-actions", data=bad).text
    assert (
        "valid payment id"
        in env.post("/api/admin/staff-actions", data={**data, "target": "not-an-id"}).text
    )


def test_invoice_csv_export_is_role_gated_and_audited(env, staff):
    sign_in(env, staff, ["support"])
    assert env.get("/api/admin/finance/invoices.csv").status_code == 403
    env.cookies.clear()
    sign_in(env, staff, ["finance"])
    r = env.get("/api/admin/finance/invoices.csv?fy=2026-27")
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("text/csv")
        and r.text.startswith("number,type")
    )
    assert 'filename="invoices-2026-27.csv"' in r.headers["content-disposition"]
    assert env.get("/api/admin/finance/invoices.csv?fy=../../etc").status_code == 400
    rows = run(_q(select(AuditLog.action).where(AuditLog.action == "admin.exported_invoices")))
    assert len(rows) == 1


def test_metrics_page_is_for_finance_and_superadmin_only(env, staff):
    sign_in(env, staff, ["support"])
    assert env.get("/api/admin/metrics").status_code == 403
    env.cookies.clear()
    sign_in(env, staff, ["finance"])
    r = env.get("/api/admin/metrics")
    assert r.status_code == 200 and "MRR" in r.text and "Churn" in r.text and "Waitlist" in r.text


def test_flags_are_visible_to_staff_and_changed_only_through_audited_actions(env, staff):
    sign_in(env, staff, ["superadmin"])
    token = csrf_of(env)

    def post(**kw):
        data = {"csrf": token, "reason": "launch decision, ticket 7", **kw}
        return env.post("/api/admin/staff-actions", data=data).text

    assert "flag beta_ui updated" in post(target="beta_ui", action="flag_on")
    assert "flag beta_ui updated" in post(target="beta_ui", action="flag_rollout", amount="25")
    assert "0 to 100" in post(target="beta_ui", action="flag_rollout", amount="250")
    assert "not a valid flag key" in post(target="Bad Key", action="flag_on")
    assert env.get("/api/admin/flag/list").status_code == 200
    rows = run(
        _q(
            select(AuditLog.action, AuditLog.meta)
            .where(AuditLog.action == "flag.changed")
            .order_by(AuditLog.id)
        )
    )
    assert (
        len(rows) == 2
        and rows[1][1]["change"]["rollout_percent"] == 25
        and rows[0][1]["reason"].startswith("launch decision")
    )


def test_support_can_only_flip_kill_switches(env, staff):
    sign_in(env, staff, ["support"])
    token = csrf_of(env)
    data = {"csrf": token, "reason": "provider outage, incident 12"}
    assert (
        "only change kill switches"
        in env.post(
            "/api/admin/staff-actions", data={**data, "target": "beta_ui", "action": "flag_on"}
        ).text
    )
    assert (
        "flag kill:ai updated"
        in env.post(
            "/api/admin/staff-actions", data={**data, "target": "kill:ai", "action": "flag_on"}
        ).text
    )
    assert (
        "cannot run flag_rollout"
        in env.post(
            "/api/admin/staff-actions",
            data={**data, "target": "kill:ai", "action": "flag_rollout", "amount": "5"},
        ).text
    )
