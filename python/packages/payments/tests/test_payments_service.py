import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from payments_support import PLANS, captured, make_gstin, signed
from sqlalchemy import func, select
from tsp.audit import Actor, AuditLog
from tsp.payments import (
    CreditEntry,
    InsufficientCredits,
    InvalidSignature,
    Invoice,
    NotAllowed,
    Payment,
    Refund,
    Subscription,
    Transfer,
    UnknownPlan,
    WebhookEvent,
    export_csv,
)
from tsp.payments.pdf import render_pdf

ORG, USER = "org1", "user1"


async def checkout(pay, db, plan="credits_100"):
    async with db.session() as s:
        await pay.sync_plans(s, PLANS)
    async with db.session() as s:
        return await pay.start_checkout(s, ORG, plan, user_sub=USER, email="buyer@example.com")


async def deliver(pay, db, event, event_id):
    body, headers = signed(event, event_id)
    async with db.session() as s:
        return await pay.handle_webhook(s, "razorpay", body, headers)


async def test_plans_sync_and_deactivate(db, pay):
    async with db.session() as s:
        await pay.sync_plans(s, PLANS)
        assert {p.code for p in await pay.plans(s)} == {"pro_monthly", "credits_100"}
        await pay.sync_plans(s, PLANS[:1])
    async with db.session() as s:
        assert [p.code for p in await pay.plans(s)] == ["pro_monthly"]
        with pytest.raises(UnknownPlan):
            await pay.start_checkout(s, ORG, "credits_100", user_sub=USER, email="x@y.z")


async def test_order_checkout_stores_expected_amount(db, pay, gateway):
    co = await checkout(pay, db)
    assert (
        co.kind == "order"
        and co.payload["key"] == "rzp_test"
        and co.payload["order_id"].startswith("order_")
    )
    order_call = next(c for c in gateway.calls if c[1] == "/v1/orders")
    assert order_call[2]["amount"] == 11800 and order_call[2]["notes"] == {
        "org_id": ORG,
        "plan": "credits_100",
        "user": USER,
    }
    async with db.session() as s:
        p = (await s.execute(select(Payment))).scalar_one()
    assert (p.status, p.amount, p.provider_order_id) == ("created", 11800, co.payload["order_id"])


async def test_subscription_checkout_reuses_the_customer(db, pay, gateway):
    await checkout(pay, db, "pro_monthly")
    async with db.session() as s:
        await pay.start_checkout(s, ORG, "pro_monthly", user_sub=USER, email="buyer@example.com")
    assert [c[1] for c in gateway.calls].count("/v1/customers") == 1
    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(Subscription))).scalar_one() == 2


async def test_captured_payment_grants_credits_once_and_issues_an_invoice(db, pay, store):
    co = await checkout(pay, db)
    ev = captured("pay_1", co.payload["order_id"])
    r1 = await deliver(pay, db, ev, "evt_1")
    r2 = await deliver(pay, db, ev, "evt_1")  # the gateway redelivers the same event
    assert (r1.processed, r1.duplicates, r2.processed, r2.duplicates) == (1, 0, 0, 1)
    # a second event id for the same payment (e.g. order.paid arriving as another notification) is also harmless
    await deliver(pay, db, ev, "evt_1b")
    async with db.session() as s:
        assert await pay.credit_balance(s, ORG) == 100
        p = (await s.execute(select(Payment))).scalar_one()
        inv = (await s.execute(select(Invoice))).scalar_one()
        events = (
            (await s.execute(select(WebhookEvent.event_id).order_by(WebhookEvent.event_id)))
            .scalars()
            .all()
        )
    assert (p.status, p.provider_payment_id, p.method) == ("captured", "pay_1", "upi")
    assert inv.number == "INV/" + inv.fy[2:4] + inv.fy[5:7] + "/000001" and inv.total == 11800
    assert (inv.taxable, inv.cgst, inv.sgst, inv.igst) == (
        10000,
        900,
        900,
        0,
    )  # same state, 18% included
    assert store.files[inv.pdf_key].startswith(b"%PDF")
    assert events == ["evt_1", "evt_1b"]


async def test_wrong_amount_grants_nothing(db, pay):
    co = await checkout(pay, db)
    await deliver(pay, db, captured("pay_x", co.payload["order_id"], amount=100), "evt_m")
    async with db.session() as s:
        p = (await s.execute(select(Payment))).scalar_one()
        assert (p.status, await pay.credit_balance(s, ORG)) == ("amount_mismatch", 0)
        assert (await s.execute(select(func.count()).select_from(Invoice))).scalar_one() == 0
        assert (
            "billing.amount_mismatch" in (await s.execute(select(AuditLog.action))).scalars().all()
        )


async def test_forged_webhooks_are_rejected_and_leave_no_trace(db, pay):
    co = await checkout(pay, db)
    body, headers = signed(captured("pay_f", co.payload["order_id"]), "evt_f", secret="attacker")
    async with db.session() as s:
        with pytest.raises(InvalidSignature):
            await pay.handle_webhook(s, "razorpay", body, headers)
    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(WebhookEvent))).scalar_one() == 0
        assert await pay.credit_balance(s, ORG) == 0


async def test_unattributable_event_fails_so_the_gateway_retries(db, pay):
    body, headers = signed(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_z",
                        "order_id": "order_unknown",
                        "amount": 1,
                        "currency": "INR",
                        "notes": {},
                    }
                }
            },
        },
        "evt_z",
    )
    with pytest.raises(Exception, match="which organization"):
        async with db.session() as s:
            await pay.handle_webhook(s, "razorpay", body, headers)
    async with (
        db.session() as s
    ):  # the half-processed event row was rolled back, so redelivery is processed
        assert (await s.execute(select(func.count()).select_from(WebhookEvent))).scalar_one() == 0


async def test_subscription_lifecycle_drives_entitlements(db, pay):
    co = await checkout(pay, db, "pro_monthly")
    sub_id = co.payload["subscription_id"]
    end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

    def sub_event(name, status, end_ts=end):
        return {
            "event": name,
            "payload": {
                "subscription": {
                    "entity": {
                        "id": sub_id,
                        "status": status,
                        "current_end": end_ts,
                        "notes": {"org_id": ORG, "plan": "pro_monthly"},
                    }
                }
            },
        }

    async with db.session() as s:
        assert (await pay.entitlements(s, ORG)).plan_code == "free"  # created is not paid
    await deliver(pay, db, sub_event("subscription.activated", "active"), "e1")
    async with db.session() as s:
        e = await pay.entitlements(s, ORG)
        assert (e.plan_code, e.features["seats"], e.subscription_status) == (
            "pro_monthly",
            10,
            "active",
        )
    past = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    await deliver(pay, db, sub_event("subscription.pending", "pending", past), "e2")
    async with db.session() as s:
        assert (
            await pay.entitlements(s, ORG)
        ).plan_code == "pro_monthly"  # inside the 3-day grace window
    await deliver(
        pay,
        db,
        sub_event(
            "subscription.pending",
            "pending",
            int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp()),
        ),
        "e3",
    )
    async with db.session() as s:
        assert (await pay.entitlements(s, ORG)).plan_code == "free"  # grace over
    await deliver(pay, db, sub_event("subscription.cancelled", "cancelled", end), "e4")
    async with db.session() as s:
        assert (
            await pay.entitlements(s, ORG)
        ).plan_code == "pro_monthly"  # paid-for time is honoured
    await deliver(pay, db, sub_event("subscription.halted", "halted", end), "e5")
    async with db.session() as s:
        assert (await pay.entitlements(s, ORG)).plan_code == "free"


async def test_renewal_charge_updates_period_records_one_payment_and_grants_plan_credits(db, pay):
    async with db.session() as s:
        await pay.sync_plans(s, [*PLANS[:1], PLANS[1]])
        await pay.sync_plans(
            s,
            [
                PLANS[1],
                type(PLANS[0])(
                    "pro_monthly",
                    "Pro",
                    "subscription",
                    11800,
                    interval="month",
                    provider_plan_id="plan_pro",
                    credits=50,
                ),
            ],
        )
    async with db.session() as s:
        co = await pay.start_checkout(s, ORG, "pro_monthly", user_sub=USER, email="b@example.com")
    sub_id = co.payload["subscription_id"]
    end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    charged = {
        "event": "subscription.charged",
        "payload": {
            "subscription": {
                "entity": {
                    "id": sub_id,
                    "status": "active",
                    "current_end": end,
                    "notes": {"org_id": ORG, "plan": "pro_monthly"},
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_r1",
                    "amount": 11800,
                    "currency": "INR",
                    "method": "card",
                    "notes": {},
                }
            },
        },
    }
    await deliver(pay, db, charged, "evt_c1")
    await deliver(
        pay, db, {**charged, "event": "payment.captured"}, "evt_c2"
    )  # the sibling event for the same payment
    async with db.session() as s:
        assert (await s.execute(select(func.count()).select_from(Payment))).scalar_one() == 1
        assert await pay.credit_balance(s, ORG) == 50  # granted once, not twice
        sub = (await s.execute(select(Subscription))).scalar_one()
        assert sub.status == "active" and sub.current_period_end is not None


async def test_refund_marks_payment_claws_back_credits_and_issues_a_credit_note(db, pay):
    co = await checkout(pay, db)
    await deliver(pay, db, captured("pay_9", co.payload["order_id"]), "evt_a")
    async with db.session() as s:
        payment = (await s.execute(select(Payment))).scalar_one()
        await pay.refund(s, payment.id, actor=Actor("staff", "support"), reason="customer asked")
    refund_event = {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": "rfnd_9",
                    "payment_id": "pay_9",
                    "amount": 11800,
                    "status": "processed",
                }
            }
        },
    }
    await deliver(pay, db, refund_event, "evt_r")
    await deliver(
        pay, db, refund_event, "evt_r2"
    )  # even under a new event id the refund counts once
    async with db.session() as s:
        p = (await s.execute(select(Payment))).scalar_one()
        assert (p.status, p.refunded_amount) == ("refunded", 11800)
        assert await pay.credit_balance(s, ORG) == 0
        assert (await s.execute(select(func.count()).select_from(Refund))).scalar_one() == 1
        notes = (
            (await s.execute(select(Invoice).where(Invoice.kind == "credit_note"))).scalars().all()
        )
        assert len(notes) == 1 and notes[0].number.startswith("CN/") and notes[0].total == 11800
        csv = await export_csv(s, notes[0].fy)
    lines = csv.strip().splitlines()
    assert lines[0].startswith("number,type") and any(
        ",credit_note," in row and ",-118.00" in row for row in lines
    )


async def test_cannot_refund_what_was_not_captured_or_more_than_paid(db, pay):
    co = await checkout(pay, db)
    async with db.session() as s:
        payment = (await s.execute(select(Payment))).scalar_one()
        with pytest.raises(NotAllowed):
            await pay.refund(s, payment.id, actor=Actor("staff"), reason="x")
    await deliver(pay, db, captured("pay_10", co.payload["order_id"]), "evt_b")
    async with db.session() as s:
        with pytest.raises(NotAllowed):
            await pay.refund(s, payment.id, actor=Actor("staff"), reason="x", amount=999999)


async def test_invoice_failure_does_not_lose_the_payment_and_can_be_repaired(db, pay, store):
    co = await checkout(pay, db)
    store.fail = True
    await deliver(pay, db, captured("pay_11", co.payload["order_id"]), "evt_c")
    async with db.session() as s:
        assert (await s.execute(select(Payment.status))).scalar_one() == "captured"
        assert await pay.credit_balance(s, ORG) == 100
        assert (await s.execute(select(func.count()).select_from(Invoice))).scalar_one() == 0
    store.fail = False
    async with db.session() as s:
        assert await pay.issue_missing_invoices(s) == 1
    async with db.session() as s:
        assert await pay.issue_missing_invoices(s) == 0


async def test_invoice_numbers_are_gapless_under_concurrency(db, pay):
    async with db.session() as s:
        await pay.sync_plans(s, PLANS)
        pays = []
        for i in range(12):
            p = Payment(
                org_id=f"o{i}",
                provider="razorpay",
                provider_payment_id=f"p{i}",
                amount=11800,
                currency="INR",
                status="captured",
            )
            s.add(p)
            pays.append(p)
        await s.flush()
        ids = [p.id for p in pays]

    async def issue(pid):
        async with db.session() as s2:
            payment = await s2.get(Payment, pid)
            return (await pay.invoicer.issue_for_payment(s2, payment, None, None)).number

    numbers = await asyncio.gather(*(issue(i) for i in ids))
    seqs = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
    assert seqs == list(range(1, 13))  # 1..12, no gaps, no duplicates


async def test_a_rolled_back_invoice_does_not_burn_a_number(db, pay):
    async with db.session() as s:
        p = Payment(
            org_id="o",
            provider="razorpay",
            provider_payment_id="pp",
            amount=11800,
            currency="INR",
            status="captured",
        )
        s.add(p)
        await s.flush()
        pid = p.id
    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await pay.invoicer.issue_for_payment(s, await s.get(Payment, pid), None, None)
            raise RuntimeError("something later in the request failed")
    async with db.session() as s:
        inv = await pay.invoicer.issue_for_payment(s, await s.get(Payment, pid), None, None)
        assert inv.number.endswith("/000001")


async def test_gst_treatment_for_each_kind_of_buyer(db, pay):
    async def invoice_for(pid, profile=None, currency="INR"):
        async with db.session() as s:
            if profile:
                await pay.set_profile(s, pid, **profile)
            p = Payment(
                org_id=pid,
                provider="razorpay",
                provider_payment_id=pid,
                amount=11800,
                currency=currency,
                status="captured",
            )
            s.add(p)
            await s.flush()
            return await pay.invoicer.issue_for_payment(s, p, None, await pay.get_profile(s, pid))

    base = dict(legal_name="Buyer Co", email="b@c.d", address="Somewhere")
    same = await invoice_for("a", {**base, "gstin": make_gstin("29", "BBBPL1234C")})
    other = await invoice_for("b", {**base, "gstin": make_gstin("27", "CCCPL1234C")})
    abroad = await invoice_for("c", {**base, "country": "US"}, currency="USD")
    anon = await invoice_for("d")
    assert (same.supply_type, same.cgst, same.sgst, same.igst) == ("intra_state", 900, 900, 0)
    assert (other.supply_type, other.place_of_supply, other.igst) == ("inter_state", "27", 1800)
    assert (abroad.supply_type, abroad.total, abroad.igst, abroad.currency) == (
        "export",
        11800,
        0,
        "USD",
    )
    assert (anon.supply_type, anon.place_of_supply) == (
        "intra_state",
        "29",
    )  # consumer: supplier's state
    assert same.buyer["gstin"] and not anon.buyer.get("gstin")


async def test_bad_gstin_is_refused_when_saving_the_profile(db, pay):
    async with db.session() as s:
        with pytest.raises(ValueError, match="GSTIN"):
            await pay.set_profile(
                s, "o", legal_name="X", email="x@y.z", address="a", gstin="29AAAPL1234C1ZZ"
            )


async def test_credits_are_idempotent_and_never_negative(db, pay):
    async with db.session() as s:
        assert await pay.grant_credits(s, "o", 10, reason="promo", ref="promo-1") == 10
        assert (
            await pay.grant_credits(s, "o", 10, reason="promo", ref="promo-1") == 10
        )  # replay: no second grant
        assert await pay.spend_credits(s, "o", 4, reason="usage", ref="req-1") == 6
        assert (
            await pay.spend_credits(s, "o", 4, reason="usage", ref="req-1") == 6
        )  # retry: no second charge
        with pytest.raises(InsufficientCredits):
            await pay.spend_credits(s, "o", 7, reason="usage", ref="req-2")
    async with db.session() as s:
        ledger = (
            await s.execute(
                select(CreditEntry.delta, CreditEntry.balance_after).order_by(CreditEntry.id)
            )
        ).all()
    assert [tuple(r) for r in ledger] == [(10, 10), (-4, 6)]


async def test_concurrent_spending_cannot_overdraw(db, pay):
    async with db.session() as s:
        await pay.grant_credits(s, "o", 10, reason="seed")

    async def spend(i):
        try:
            async with db.session() as s2:
                await pay.spend_credits(s2, "o", 3, reason="usage", ref=f"r{i}")
            return True
        except InsufficientCredits:
            return False

    results = await asyncio.gather(*(spend(i) for i in range(8)))
    assert sum(results) == 3  # 10 credits buy exactly three 3-credit calls
    async with db.session() as s:
        assert await pay.credit_balance(s, "o") == 1


async def test_marketplace_transfers_are_capped_by_the_payment(db, pay, gateway):
    co = await checkout(pay, db)
    await deliver(pay, db, captured("pay_t", co.payload["order_id"]), "evt_t")
    async with db.session() as s:
        payment = (await s.execute(select(Payment))).scalar_one()
        t = await pay.transfer(
            s,
            payment.id,
            seller_org_id="seller1",
            account_id="acc_1",
            amount=9000,
            actor=Actor("system"),
        )
        assert t.status == "created"
        with pytest.raises(NotAllowed, match="exceed"):
            await pay.transfer(
                s,
                payment.id,
                seller_org_id="seller2",
                account_id="acc_2",
                amount=3000,
                actor=Actor("system"),
            )
    await deliver(
        pay,
        db,
        {
            "event": "transfer.processed",
            "payload": {
                "transfer": {
                    "entity": {
                        "id": t.provider_transfer_id,
                        "source": "pay_t",
                        "amount": 9000,
                        "status": "processed",
                    }
                }
            },
        },
        "evt_tp",
    )
    async with db.session() as s:
        assert (await s.execute(select(Transfer.status))).scalar_one() == "processed"


def test_pdf_is_a_valid_document():
    inv = {
        "kind": "invoice",
        "number": "INV/2627/000001",
        "issued_at": "2026-10-12T00:00:00+00:00",
        "currency": "INR",
        "seller": {"name": "Acme", "gstin": "29AAAPL1234C1ZF", "address": "Bengaluru"},
        "buyer": {"name": "Buyer", "address": "x"},
        "place_of_supply": "29",
        "supply_type": "intra_state",
        "sac": "998314",
        "description": "Pro plan",
        "taxable": 10000,
        "cgst": 900,
        "sgst": 900,
        "igst": 0,
        "total": 11800,
    }
    pdf = render_pdf(inv)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
