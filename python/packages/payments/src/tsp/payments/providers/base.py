"""The gateway-neutral interface. Services and routes only ever see these types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class ProviderError(Exception):
    """The gateway rejected a call or was unreachable."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class InvalidSignature(Exception):
    """A webhook or checkout callback did not carry a valid signature."""


@dataclass
class ProviderOrder:
    id: str
    amount: int
    currency: str
    checkout: dict[str, Any]  # what the browser SDK needs to open the payment sheet


@dataclass
class ProviderSubscription:
    id: str
    status: str
    checkout: dict[str, Any]


@dataclass
class ProviderRefund:
    id: str
    amount: int
    status: str


@dataclass
class ProviderTransfer:
    id: str
    amount: int
    status: str


@dataclass
class PaymentEvent:
    """A webhook event in the gateway-neutral shape the state machine understands.

    ``type`` is one of: payment.captured, payment.failed, payment.authorized, subscription.activated,
    subscription.charged, subscription.pending, subscription.halted, subscription.cancelled,
    subscription.completed, subscription.paused, subscription.resumed, subscription.updated,
    refund.processed, refund.failed, transfer.processed, transfer.failed, or ``ignored.<name>``.
    """

    event_id: str
    type: str
    payment_id: str | None = None
    order_id: str | None = None
    subscription_id: str | None = None
    refund_id: str | None = None
    transfer_id: str | None = None
    amount: int | None = None
    currency: str | None = None
    method: str | None = None
    email: str | None = None
    status: str | None = None
    failure_reason: str | None = None
    period_end: datetime | None = None
    notes: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    async def create_customer(
        self,
        *,
        name: str,
        email: str,
        contact: str | None = None,
        notes: Mapping[str, str] | None = None,
    ) -> str: ...

    async def create_order(
        self, *, amount: int, currency: str, receipt: str, notes: Mapping[str, str]
    ) -> ProviderOrder: ...

    async def create_subscription(
        self, *, plan_id: str, customer_id: str | None, total_count: int, notes: Mapping[str, str]
    ) -> ProviderSubscription: ...

    async def cancel_subscription(
        self, subscription_id: str, *, at_cycle_end: bool = True
    ) -> None: ...

    async def refund(
        self, payment_id: str, *, amount: int | None = None, notes: Mapping[str, str] | None = None
    ) -> ProviderRefund: ...

    def verify_checkout(
        self,
        *,
        payment_id: str,
        signature: str,
        order_id: str | None = None,
        subscription_id: str | None = None,
    ) -> bool: ...

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool: ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> list[PaymentEvent]: ...

    async def aclose(self) -> None: ...
