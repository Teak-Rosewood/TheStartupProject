"""Stripe: hosted Checkout for one-time and subscription purchases, refunds and webhook checks.

Stripe is the second provider (for customers paying from abroad). Event and request shapes follow
Stripe's public API documentation; run one test-mode purchase, renewal and refund before launch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import (
    InvalidSignature,
    PaymentEvent,
    ProviderError,
    ProviderOrder,
    ProviderRefund,
    ProviderSubscription,
)

TOLERANCE_SECONDS = 300
_SUB_STATUS = {
    "active": "active",
    "trialing": "active",
    "past_due": "pending",
    "unpaid": "halted",
    "canceled": "cancelled",
    "incomplete": "created",
    "incomplete_expired": "cancelled",
    "paused": "paused",
}


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    """Stripe takes form-encoded bodies with bracketed keys: ``line_items[0][quantity]=1``."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else str(k), v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif value is not None:
        out[prefix] = str(value).lower() if isinstance(value, bool) else str(value)


class StripeProvider:
    name = "stripe"

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str,
        *,
        success_url: str,
        cancel_url: str,
        base_url: str = "https://api.stripe.com/v1",
        http: httpx.AsyncClient | None = None,
        clock: Any = time.time,
    ):
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self._success, self._cancel = success_url, cancel_url
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=20)
        self._owns_http = http is None
        self._clock = clock

    async def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        form: dict[str, str] = {}
        _flatten("", body or {}, form)
        try:
            resp = await self._http.request(
                method,
                f"{self._base}{path}",
                data=form or None,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"stripe unreachable: {e}") from e
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise ProviderError(
                (data.get("error") or {}).get("message", f"stripe error {resp.status_code}"),
                resp.status_code,
            )
        return data  # type: ignore[no-any-return]

    async def create_customer(
        self,
        *,
        name: str,
        email: str,
        contact: str | None = None,
        notes: Mapping[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"name": name, "email": email, "metadata": dict(notes or {})}
        if contact:
            body["phone"] = contact
        return str((await self._call("POST", "/customers", body))["id"])

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder:
        s = await self._call(
            "POST",
            "/checkout/sessions",
            {
                "mode": "payment",
                "success_url": self._success,
                "cancel_url": self._cancel,
                "client_reference_id": receipt,
                "line_items": [
                    {
                        "quantity": 1,
                        "price_data": {
                            "currency": currency.lower(),
                            "unit_amount": amount,
                            "product_data": {"name": receipt},
                        },
                    }
                ],
                "metadata": dict(notes),
                "payment_intent_data": {"metadata": dict(notes)},
            },
        )
        return ProviderOrder(s["id"], amount, currency, {"url": s["url"], "session_id": s["id"]})

    async def create_subscription(
        self, *, plan_id: str, customer_id: str | None, total_count: int, notes: Mapping[str, str]
    ) -> ProviderSubscription:
        body: dict[str, Any] = {
            "mode": "subscription",
            "success_url": self._success,
            "cancel_url": self._cancel,
            "line_items": [{"price": plan_id, "quantity": 1}],
            "metadata": dict(notes),
            "subscription_data": {"metadata": dict(notes)},
        }
        if customer_id:
            body["customer"] = customer_id
        s = await self._call("POST", "/checkout/sessions", body)
        # The subscription object appears once Checkout completes; the session id identifies it until then.
        return ProviderSubscription(s["id"], "created", {"url": s["url"], "session_id": s["id"]})

    async def cancel_subscription(self, subscription_id: str, *, at_cycle_end: bool = True) -> None:
        if at_cycle_end:
            await self._call(
                "POST", f"/subscriptions/{subscription_id}", {"cancel_at_period_end": True}
            )
        else:
            await self._call("DELETE", f"/subscriptions/{subscription_id}")

    async def refund(
        self, payment_id: str, *, amount: int | None = None, notes: Mapping[str, str] | None = None
    ) -> ProviderRefund:
        body: dict[str, Any] = {"payment_intent": payment_id, "metadata": dict(notes or {})}
        if amount is not None:
            body["amount"] = amount
        r = await self._call("POST", "/refunds", body)
        return ProviderRefund(r["id"], r["amount"], r["status"])

    def verify_checkout(
        self,
        *,
        payment_id: str,
        signature: str,
        order_id: str | None = None,
        subscription_id: str | None = None,
    ) -> bool:
        return False  # Stripe confirms through its webhook and the success URL, not a signed browser callback

    def _signatures(self, header: str) -> tuple[int, list[str]]:
        parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
        t = next((int(v) for k, v in parts if k == "t" and v.isdigit()), 0)
        return t, [v for k, v in parts if k == "v1"]

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool:
        header = {k.lower(): v for k, v in headers.items()}.get("stripe-signature", "")
        t, sigs = self._signatures(header)
        if not t or abs(self._clock() - t) > TOLERANCE_SECONDS:  # stale timestamps are replays
            return False
        expected = hmac.new(
            self._webhook_secret.encode(), f"{t}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in sigs)

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> list[PaymentEvent]:
        if not self.verify_webhook(body, headers):
            raise InvalidSignature("stripe webhook signature mismatch")
        data = json.loads(body)
        obj = (data.get("data") or {}).get("object") or {}
        name = data.get("type", "")
        ev = PaymentEvent(
            event_id=data["id"],
            type=f"ignored.{name}",
            raw=data,
            notes=dict(obj.get("metadata") or {}),
        )
        if (
            name == "checkout.session.completed"
            and obj.get("mode") == "payment"
            and obj.get("payment_status") == "paid"
        ):
            ev.type, ev.payment_id, ev.order_id = (
                "payment.captured",
                obj.get("payment_intent"),
                obj.get("id"),
            )
            ev.amount, ev.currency, ev.email = (
                obj.get("amount_total"),
                (obj.get("currency") or "").upper(),
                (obj.get("customer_details") or {}).get("email"),
            )
        elif name == "invoice.paid":
            line = ((obj.get("lines") or {}).get("data") or [{}])[0]
            ev.type, ev.subscription_id, ev.payment_id = (
                "subscription.charged",
                obj.get("subscription"),
                obj.get("payment_intent"),
            )
            ev.amount, ev.currency, ev.email = (
                obj.get("amount_paid"),
                (obj.get("currency") or "").upper(),
                obj.get("customer_email"),
            )
            end = (line.get("period") or {}).get("end")
            ev.period_end = datetime.fromtimestamp(end, tz=UTC) if end else None
            ev.notes = {**dict(line.get("metadata") or {}), **ev.notes}
            ev.status = "active"
        elif name == "invoice.payment_failed":
            ev.type, ev.subscription_id, ev.payment_id = (
                "payment.failed",
                obj.get("subscription"),
                obj.get("payment_intent"),
            )
            ev.amount, ev.currency, ev.failure_reason = (
                obj.get("amount_due"),
                (obj.get("currency") or "").upper(),
                "payment failed",
            )
        elif name in ("customer.subscription.updated", "customer.subscription.deleted"):
            status = (
                "cancelled"
                if name.endswith("deleted")
                else _SUB_STATUS.get(obj.get("status", ""), "pending")
            )
            ev.type = {
                "active": "subscription.activated",
                "cancelled": "subscription.cancelled",
                "pending": "subscription.pending",
                "halted": "subscription.halted",
                "paused": "subscription.paused",
            }.get(status, "subscription.updated")
            ev.subscription_id, ev.status = obj.get("id"), status
            end = obj.get("current_period_end")
            ev.period_end = datetime.fromtimestamp(end, tz=UTC) if end else None
        elif name == "charge.refunded":
            ev.type, ev.payment_id, ev.amount = (
                "refund.processed",
                obj.get("payment_intent"),
                obj.get("amount_refunded"),
            )
            refunds = (obj.get("refunds") or {}).get("data") or [{}]
            ev.refund_id = refunds[0].get("id") or f"{obj.get('id')}:{obj.get('amount_refunded')}"
            ev.currency = (obj.get("currency") or "").upper()
        return [ev]

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
