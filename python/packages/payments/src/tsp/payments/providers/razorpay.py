"""Razorpay: Orders, Subscriptions, Refunds, Route transfers and webhook signature checks.

Request and event shapes follow Razorpay's public API documentation. Before launch run one full
purchase, renewal, refund and cancellation in Razorpay test mode (docs/modules/payments.md).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import (
    InvalidSignature,
    PaymentEvent,
    ProviderError,
    ProviderOrder,
    ProviderRefund,
    ProviderSubscription,
    ProviderTransfer,
)

_SUBSCRIPTION_EVENTS = {
    "subscription.activated",
    "subscription.charged",
    "subscription.pending",
    "subscription.halted",
    "subscription.cancelled",
    "subscription.completed",
    "subscription.paused",
    "subscription.resumed",
    "subscription.updated",
}


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


class RazorpayProvider:
    name = "razorpay"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        webhook_secret: str,
        *,
        base_url: str = "https://api.razorpay.com/v1",
        http: httpx.AsyncClient | None = None,
    ):
        self.key_id = key_id
        self._secret = key_secret
        self._webhook_secret = webhook_secret
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=20)
        self._owns_http = http is None

    async def _call(
        self, method: str, path: str, body: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            resp = await self._http.request(
                method, f"{self._base}{path}", json=body, auth=(self.key_id, self._secret)
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"razorpay unreachable: {e}") from e
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise ProviderError(
                err.get("description") or f"razorpay error {resp.status_code}", resp.status_code
            )
        return data  # type: ignore[no-any-return]

    # -- API ---------------------------------------------------------------------------------

    async def create_customer(
        self,
        *,
        name: str,
        email: str,
        contact: str | None = None,
        notes: Mapping[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "name": name,
            "email": email,
            "fail_existing": "0",
            "notes": dict(notes or {}),
        }
        if contact:
            body["contact"] = contact
        return str((await self._call("POST", "/customers", body))["id"])

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder:
        order = await self._call(
            "POST",
            "/orders",
            {"amount": amount, "currency": currency, "receipt": receipt, "notes": dict(notes)},
        )
        return ProviderOrder(
            order["id"],
            order["amount"],
            order["currency"],
            {
                "key": self.key_id,
                "order_id": order["id"],
                "amount": order["amount"],
                "currency": order["currency"],
            },
        )

    async def create_subscription(
        self, *, plan_id: str, customer_id: str | None, total_count: int, notes: Mapping[str, str]
    ) -> ProviderSubscription:
        sub = await self._call(
            "POST",
            "/subscriptions",
            {
                "plan_id": plan_id,
                "total_count": total_count,
                "customer_notify": 1,
                "notes": dict(notes),
            },
        )
        checkout: dict[str, Any] = {"key": self.key_id, "subscription_id": sub["id"]}
        if customer_id:
            checkout["customer_id"] = customer_id  # passed to Checkout, not to the create call
        return ProviderSubscription(sub["id"], sub["status"], checkout)

    async def cancel_subscription(self, subscription_id: str, *, at_cycle_end: bool = True) -> None:
        await self._call(
            "POST",
            f"/subscriptions/{subscription_id}/cancel",
            {"cancel_at_cycle_end": 1 if at_cycle_end else 0},
        )

    async def refund(
        self, payment_id: str, *, amount: int | None = None, notes: Mapping[str, str] | None = None
    ) -> ProviderRefund:
        body: dict[str, Any] = {"speed": "normal", "notes": dict(notes or {})}
        if amount is not None:
            body["amount"] = amount
        r = await self._call("POST", f"/payments/{payment_id}/refund", body)
        return ProviderRefund(r["id"], r["amount"], r["status"])

    # Razorpay Route (marketplaces): the licensed gateway holds the money and splits it.
    async def create_linked_account(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Pass Razorpay's documented ``/v2/accounts`` body (business details, KYC); returns its response."""
        return await self._v2("/accounts", payload)

    async def _v2(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        base = self._base.rsplit("/v1", 1)[0] + "/v2"
        try:
            resp = await self._http.post(
                f"{base}{path}", json=dict(payload), auth=(self.key_id, self._secret)
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"razorpay unreachable: {e}") from e
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise ProviderError(
                (data.get("error") or {}).get("description", f"razorpay error {resp.status_code}"),
                resp.status_code,
            )
        return data  # type: ignore[no-any-return]

    async def transfer(
        self,
        payment_id: str,
        *,
        account_id: str,
        amount: int,
        currency: str = "INR",
        notes: Mapping[str, str] | None = None,
        on_hold: bool = False,
    ) -> ProviderTransfer:
        data = await self._call(
            "POST",
            f"/payments/{payment_id}/transfers",
            {
                "transfers": [
                    {
                        "account": account_id,
                        "amount": amount,
                        "currency": currency,
                        "notes": dict(notes or {}),
                        "on_hold": on_hold,
                    }
                ],
            },
        )
        item = (data.get("items") or [data])[0]
        return ProviderTransfer(item["id"], item["amount"], item.get("status", "created"))

    # -- signatures --------------------------------------------------------------------------

    def verify_checkout(
        self,
        *,
        payment_id: str,
        signature: str,
        order_id: str | None = None,
        subscription_id: str | None = None,
    ) -> bool:
        """Verify the values Checkout.js hands the browser after payment (UX confirmation only).

        Orders sign ``order_id|payment_id``; subscriptions sign ``payment_id|subscription_id``.
        The webhook, not this callback, is what changes entitlements.
        """
        if order_id:
            message = f"{order_id}|{payment_id}"
        elif subscription_id:
            message = f"{payment_id}|{subscription_id}"
        else:
            return False
        return hmac.compare_digest(_hmac(self._secret, message.encode()), signature or "")

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool:
        sig = {k.lower(): v for k, v in headers.items()}.get("x-razorpay-signature", "")
        return hmac.compare_digest(_hmac(self._webhook_secret, body), sig)

    # -- events ------------------------------------------------------------------------------

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> list[PaymentEvent]:
        if not self.verify_webhook(body, headers):
            raise InvalidSignature("razorpay webhook signature mismatch")
        h = {k.lower(): v for k, v in headers.items()}
        data = json.loads(body)
        name = data.get("event", "")
        # The event id header is unique per event and stable across retries; fall back to a body hash.
        event_id = h.get("x-razorpay-event-id") or hashlib.sha256(body).hexdigest()
        ents = {k: (v or {}).get("entity", {}) for k, v in (data.get("payload") or {}).items()}
        pay, sub, ref, tr = (
            ents.get("payment", {}),
            ents.get("subscription", {}),
            ents.get("refund", {}),
            ents.get("transfer", {}),
        )
        ev = PaymentEvent(event_id=event_id, type=name, raw=data)
        if pay:
            ev.payment_id, ev.order_id = pay.get("id"), pay.get("order_id")
            ev.amount, ev.currency, ev.method, ev.email = (
                pay.get("amount"),
                pay.get("currency"),
                pay.get("method"),
                pay.get("email"),
            )
            ev.failure_reason = pay.get("error_description")
            ev.notes = dict(pay.get("notes") or {})
        if sub:
            ev.subscription_id, ev.status = sub.get("id"), sub.get("status")
            ev.period_end = _ts(sub.get("current_end"))
            ev.notes = {**dict(sub.get("notes") or {}), **ev.notes}
        if ref:
            ev.refund_id, ev.payment_id, ev.amount, ev.status = (
                ref.get("id"),
                ref.get("payment_id"),
                ref.get("amount"),
                ref.get("status"),
            )
        if tr:
            ev.transfer_id, ev.payment_id, ev.amount, ev.status = (
                tr.get("id"),
                tr.get("source"),
                tr.get("amount"),
                tr.get("status"),
            )
            ev.notes = dict(tr.get("notes") or {})
        if (
            name in ("payment.captured", "payment.failed", "payment.authorized")
            or name in _SUBSCRIPTION_EVENTS
        ):
            return [ev]
        if name == "order.paid":  # same information as payment.captured; handled there
            ev.type = "ignored.order.paid"
        elif name in ("refund.created", "refund.processed", "refund.failed"):
            ev.type = (
                "refund.failed"
                if name == "refund.failed"
                else (
                    "refund.processed" if name == "refund.processed" else "ignored.refund.created"
                )
            )
        elif name in ("transfer.processed", "transfer.failed"):
            ev.type = name
        else:
            ev.type = f"ignored.{name}"
        return [ev]

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
