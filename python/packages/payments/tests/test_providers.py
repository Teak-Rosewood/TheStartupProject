import hashlib
import hmac
import json

import httpx
import pytest
from tsp.payments.providers import InvalidSignature, ProviderError, RazorpayProvider, StripeProvider

WH = "whsec_test"


def rzp(handler=None):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={})))
    )
    return RazorpayProvider("rzp_test_key", "key_secret", WH, http=http)


def sign(body: bytes, secret=WH) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payment_captured(event_id="evt_1"):
    body = json.dumps(
        {
            "entity": "event",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_1",
                        "order_id": "order_1",
                        "amount": 11800,
                        "currency": "INR",
                        "method": "upi",
                        "email": "a@b.co",
                        "notes": {"org_id": "org1", "plan": "pro"},
                    }
                }
            },
        }
    ).encode()
    return body, {"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": event_id}


# -- Razorpay ---------------------------------------------------------------------------------


async def test_order_creation_sends_basic_auth_and_returns_checkout_options():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"], seen["body"], seen["path"] = (
            req.headers["authorization"],
            json.loads(req.content),
            req.url.path,
        )
        return httpx.Response(200, json={"id": "order_9", "amount": 11800, "currency": "INR"})

    order = await rzp(handler).create_order(
        amount=11800, currency="INR", receipt="r1", notes={"org_id": "o"}
    )
    assert seen["path"] == "/v1/orders" and seen["auth"].startswith("Basic ")
    assert seen["body"] == {
        "amount": 11800,
        "currency": "INR",
        "receipt": "r1",
        "notes": {"org_id": "o"},
    }
    assert order.checkout == {
        "key": "rzp_test_key",
        "order_id": "order_9",
        "amount": 11800,
        "currency": "INR",
    }


async def test_subscription_and_cancel_and_refund_shapes():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, json.loads(req.content)))
        if req.url.path == "/v1/subscriptions":
            return httpx.Response(200, json={"id": "sub_1", "status": "created"})
        if req.url.path.endswith("/refund"):
            return httpx.Response(200, json={"id": "rfnd_1", "amount": 500, "status": "processed"})
        return httpx.Response(200, json={})

    p = rzp(handler)
    sub = await p.create_subscription(
        plan_id="plan_x", customer_id="cust_1", total_count=120, notes={"org_id": "o"}
    )
    assert sub.checkout == {
        "key": "rzp_test_key",
        "subscription_id": "sub_1",
        "customer_id": "cust_1",
    }
    assert calls[0][2] == {
        "plan_id": "plan_x",
        "total_count": 120,
        "customer_notify": 1,
        "notes": {"org_id": "o"},
    }  # no customer_id here
    await p.cancel_subscription("sub_1", at_cycle_end=True)
    assert calls[1] == ("POST", "/v1/subscriptions/sub_1/cancel", {"cancel_at_cycle_end": 1})
    r = await p.refund("pay_1", amount=500)
    assert (r.id, r.amount) == ("rfnd_1", 500) and calls[2][2]["amount"] == 500


async def test_gateway_errors_become_provider_errors():
    p = rzp(
        lambda r: httpx.Response(
            400, json={"error": {"code": "BAD_REQUEST_ERROR", "description": "amount too low"}}
        )
    )
    with pytest.raises(ProviderError, match="amount too low") as e:
        await p.create_order(amount=1, currency="INR", receipt="r", notes={})
    assert e.value.status == 400


async def test_transfer_shape_for_marketplaces():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "entity": "collection",
                "count": 1,
                "items": [{"id": "trf_1", "amount": 9000, "status": "created"}],
            },
        )

    t = await rzp(handler).transfer(
        "pay_1", account_id="acc_seller", amount=9000, notes={"order": "42"}
    )
    assert seen["path"] == "/v1/payments/pay_1/transfers" and t.id == "trf_1"
    assert seen["body"]["transfers"][0] == {
        "account": "acc_seller",
        "amount": 9000,
        "currency": "INR",
        "notes": {"order": "42"},
        "on_hold": False,
    }


def test_checkout_signatures_use_the_documented_message_order():
    p = rzp()
    order_sig = hmac.new(b"key_secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    sub_sig = hmac.new(b"key_secret", b"pay_1|sub_1", hashlib.sha256).hexdigest()
    assert p.verify_checkout(payment_id="pay_1", order_id="order_1", signature=order_sig)
    assert p.verify_checkout(payment_id="pay_1", subscription_id="sub_1", signature=sub_sig)
    assert not p.verify_checkout(payment_id="pay_1", order_id="order_1", signature=sub_sig)
    assert not p.verify_checkout(payment_id="pay_1", signature=order_sig)


def test_webhook_signature_and_parsing():
    p = rzp()
    body, headers = payment_captured()
    (ev,) = p.parse_webhook(body, headers)
    assert (ev.type, ev.event_id, ev.payment_id, ev.order_id, ev.amount, ev.method) == (
        "payment.captured",
        "evt_1",
        "pay_1",
        "order_1",
        11800,
        "upi",
    )
    assert ev.notes == {"org_id": "org1", "plan": "pro"}
    with pytest.raises(InvalidSignature):
        p.parse_webhook(body + b" ", headers)  # any change to the body breaks the signature
    with pytest.raises(InvalidSignature):
        p.parse_webhook(body, {"X-Razorpay-Signature": "00" * 32})
    with pytest.raises(InvalidSignature):
        p.parse_webhook(body, {})
    assert not p.verify_webhook(body, {"x-razorpay-signature": sign(body, "other-secret")})


def test_subscription_charged_carries_both_entities():
    p = rzp()
    body = json.dumps(
        {
            "event": "subscription.charged",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_1",
                        "status": "active",
                        "current_end": 1790000000,
                        "notes": {"org_id": "o1"},
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_2",
                        "amount": 11800,
                        "currency": "INR",
                        "method": "card",
                        "notes": {},
                    }
                },
            },
        }
    ).encode()
    (ev,) = p.parse_webhook(
        body, {"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": "evt_2"}
    )
    assert (ev.type, ev.subscription_id, ev.payment_id, ev.amount, ev.notes["org_id"]) == (
        "subscription.charged",
        "sub_1",
        "pay_2",
        11800,
        "o1",
    )
    assert ev.period_end.year == 2026 and ev.period_end.tzinfo is not None


def test_refund_and_unknown_events():
    p = rzp()
    body = json.dumps(
        {
            "event": "refund.processed",
            "payload": {
                "refund": {
                    "entity": {
                        "id": "rfnd_1",
                        "payment_id": "pay_1",
                        "amount": 500,
                        "status": "processed",
                    }
                }
            },
        }
    ).encode()
    (ev,) = p.parse_webhook(body, {"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": "e3"})
    assert (ev.type, ev.refund_id, ev.payment_id, ev.amount) == (
        "refund.processed",
        "rfnd_1",
        "pay_1",
        500,
    )
    body = json.dumps({"event": "payout.processed", "payload": {}}).encode()
    (ev,) = p.parse_webhook(body, {"X-Razorpay-Signature": sign(body)})
    assert ev.type == "ignored.payout.processed" and len(ev.event_id) == 64  # body-hash fallback id


# -- Stripe -----------------------------------------------------------------------------------


def stripe(handler=None, now=1_790_000_000):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200, json={})))
    )
    return StripeProvider(
        "sk_test",
        WH,
        success_url="https://app/ok",
        cancel_url="https://app/no",
        http=http,
        clock=lambda: now,
    )


def stripe_headers(body: bytes, t=1_790_000_000, secret=WH):
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={t},v1={sig}"}


async def test_stripe_checkout_sessions_are_form_encoded():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"})

    o = await stripe(handler).create_order(
        amount=5000, currency="USD", receipt="r1", notes={"org_id": "o1"}
    )
    assert o.checkout["url"].startswith("https://checkout.stripe.com")
    body = {k.replace("%5B", "[").replace("%5D", "]"): v for k, v in seen["body"].items()}
    assert body["mode"] == "payment" and body["line_items[0][price_data][unit_amount]"] == "5000"
    assert body["line_items[0][price_data][currency]"] == "usd" and body["metadata[org_id]"] == "o1"


def test_stripe_webhook_signature_and_replay_window():
    p = stripe()
    body = json.dumps(
        {
            "id": "evt_s1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "mode": "payment",
                    "payment_status": "paid",
                    "payment_intent": "pi_1",
                    "amount_total": 5000,
                    "currency": "usd",
                    "metadata": {"org_id": "o1"},
                    "customer_details": {"email": "x@y.z"},
                }
            },
        }
    ).encode()
    (ev,) = p.parse_webhook(body, stripe_headers(body))
    assert (ev.type, ev.payment_id, ev.amount, ev.currency, ev.notes["org_id"]) == (
        "payment.captured",
        "pi_1",
        5000,
        "USD",
        "o1",
    )
    with pytest.raises(InvalidSignature):
        p.parse_webhook(
            body, stripe_headers(body, t=1_790_000_000 - 4000)
        )  # signed long ago: a replay
    with pytest.raises(InvalidSignature):
        p.parse_webhook(body, stripe_headers(body, secret="other"))
    with pytest.raises(InvalidSignature):
        p.parse_webhook(body, {})


def test_stripe_invoice_paid_maps_to_subscription_charged():
    p = stripe()
    body = json.dumps(
        {
            "id": "evt_s2",
            "type": "invoice.paid",
            "data": {
                "object": {
                    "subscription": "sub_s1",
                    "payment_intent": "pi_2",
                    "amount_paid": 99900,
                    "currency": "inr",
                    "customer_email": "x@y.z",
                    "lines": {
                        "data": [{"period": {"end": 1792000000}, "metadata": {"org_id": "o1"}}]
                    },
                }
            },
        }
    ).encode()
    (ev,) = p.parse_webhook(body, stripe_headers(body))
    assert (ev.type, ev.subscription_id, ev.amount, ev.notes["org_id"], ev.status) == (
        "subscription.charged",
        "sub_s1",
        99900,
        "o1",
        "active",
    )
    assert ev.period_end.year == 2026
