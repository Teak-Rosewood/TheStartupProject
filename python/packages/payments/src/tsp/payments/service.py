from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from . import credits
from .invoicing import Invoicer, validate_profile
from .models import (
    BillingProfile,
    Customer,
    Invoice,
    Payment,
    Plan,
    Refund,
    Subscription,
    Transfer,
    WebhookEvent,
)
from .providers import InvalidSignature, PaymentEvent, PaymentProvider, ProviderError

log = logging.getLogger("tsp.payments")

ENTITLED_STATUSES = {"active", "authenticated"}


class PaymentsError(Exception):
    code = "payments_error"


class UnknownPlan(PaymentsError):
    code = "unknown_plan"


class NotAllowed(PaymentsError):
    code = "not_allowed"


@dataclass(frozen=True)
class PlanSpec:
    code: str
    name: str
    kind: str  # subscription | one_time | credit_pack
    amount: int
    currency: str = "INR"
    interval: str | None = None
    provider_plan_id: str | None = None
    credits: int = 0
    features: Mapping[str, Any] = field(default_factory=dict)
    active: bool = True


@dataclass
class Checkout:
    provider: str
    kind: str  # order | subscription
    payload: dict[
        str, Any
    ]  # hand to the browser SDK (Razorpay Checkout) or redirect to payload["url"] (Stripe)
    local_id: str


@dataclass
class Entitlements:
    plan_code: str
    features: dict[str, Any]
    subscription_status: str | None = None
    valid_until: datetime | None = None


@dataclass
class WebhookResult:
    processed: int = 0
    duplicates: int = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Payments:
    def __init__(
        self,
        providers: Mapping[str, PaymentProvider],
        *,
        default_provider: str,
        invoicer: Invoicer | None = None,
        subscription_total_count: int = 120,
        grace: timedelta = timedelta(days=3),
        free_features: Mapping[str, Any] | None = None,
        on_receipt: Callable[[AsyncSession, Payment, Invoice], Awaitable[None]] | None = None,
        on_payment_failed: Callable[[AsyncSession, Payment], Awaitable[None]] | None = None,
    ):
        if default_provider not in providers:
            raise ValueError("default_provider is not configured")
        self.providers = dict(providers)
        self.default_provider = default_provider
        self.invoicer = invoicer
        self.total_count = subscription_total_count
        self.grace = grace
        self.free_features = dict(free_features or {})
        self.on_receipt, self.on_payment_failed = on_receipt, on_payment_failed

    def provider(self, name: str | None = None) -> PaymentProvider:
        try:
            return self.providers[name or self.default_provider]
        except KeyError:
            raise PaymentsError(f"payment provider {name!r} is not configured") from None

    # -- catalog and profile -----------------------------------------------------------------

    async def sync_plans(self, session: AsyncSession, plans: Iterable[PlanSpec]) -> None:
        """Make the plans table match the code. Plans missing from the list are deactivated, not deleted."""
        specs = {p.code: p for p in plans}
        for p in specs.values():
            values = dict(
                code=p.code,
                name=p.name,
                kind=p.kind,
                amount=p.amount,
                currency=p.currency,
                interval=p.interval,
                provider_plan_id=p.provider_plan_id,
                credits=p.credits,
                features=dict(p.features),
                active=p.active,
            )
            stmt = insert(Plan).values(**values)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["code"], set_={k: v for k, v in values.items() if k != "code"}
                )
            )
        for existing in (
            await session.execute(select(Plan).where(Plan.active.is_(True)))
        ).scalars():
            if existing.code not in specs:
                existing.active = False

    async def plans(self, session: AsyncSession) -> list[Plan]:
        return list(
            (
                await session.execute(
                    select(Plan).where(Plan.active.is_(True)).order_by(Plan.amount)
                )
            ).scalars()
        )

    async def get_profile(self, session: AsyncSession, org_id: str) -> BillingProfile | None:
        return await session.get(BillingProfile, org_id)

    async def set_profile(
        self, session: AsyncSession, org_id: str, **fields: Any
    ) -> BillingProfile:
        p = await session.get(BillingProfile, org_id) or BillingProfile(
            org_id=org_id, legal_name="", email="", address=""
        )
        for k, v in fields.items():
            setattr(p, k, v)
        validate_profile(p)
        p.updated_at = _now()
        session.add(p)
        await session.flush()
        return p

    # -- checkout ----------------------------------------------------------------------------

    async def start_checkout(
        self,
        session: AsyncSession,
        org_id: str,
        plan_code: str,
        *,
        user_sub: str,
        email: str,
        provider: str | None = None,
        request: Any = None,
    ) -> Checkout:
        plan = await session.get(Plan, plan_code)
        if plan is None or not plan.active:
            raise UnknownPlan(plan_code)
        prov = self.provider(provider)
        notes = {"org_id": org_id, "plan": plan.code, "user": user_sub}
        if plan.kind == "subscription":
            if not plan.provider_plan_id:
                raise PaymentsError(f"plan {plan.code} has no provider_plan_id for {prov.name}")
            customer_id = await self._customer(session, prov, org_id, email)
            sub = await prov.create_subscription(
                plan_id=plan.provider_plan_id,
                customer_id=customer_id,
                total_count=self.total_count,
                notes=notes,
            )
            row = Subscription(
                org_id=org_id,
                plan_code=plan.code,
                provider=prov.name,
                provider_subscription_id=sub.id,
                status="created",
            )
            session.add(row)
            await session.flush()
            await record(
                session,
                "billing.checkout_started",
                actor=Actor(user_sub),
                target=("org", org_id),
                request=request,
                plan=plan.code,
                kind="subscription",
            )
            return Checkout(prov.name, "subscription", sub.checkout, row.id)
        pay = Payment(
            org_id=org_id,
            provider=prov.name,
            plan_code=plan.code,
            amount=plan.amount,
            currency=plan.currency,
            status="created",
        )
        session.add(pay)
        await session.flush()
        order = await prov.create_order(
            amount=plan.amount, currency=plan.currency, receipt=pay.id[:40], notes=notes
        )
        pay.provider_order_id = order.id
        await record(
            session,
            "billing.checkout_started",
            actor=Actor(user_sub),
            target=("org", org_id),
            request=request,
            plan=plan.code,
            kind="order",
        )
        return Checkout(prov.name, "order", order.checkout, pay.id)

    async def _customer(
        self, session: AsyncSession, prov: PaymentProvider, org_id: str, email: str
    ) -> str:
        c = (
            await session.execute(
                select(Customer).where(Customer.org_id == org_id, Customer.provider == prov.name)
            )
        ).scalar_one_or_none()
        if c:
            return c.provider_customer_id
        profile = await session.get(BillingProfile, org_id)
        pid = await prov.create_customer(
            name=(profile.legal_name if profile else email), email=email, notes={"org_id": org_id}
        )
        session.add(Customer(org_id=org_id, provider=prov.name, provider_customer_id=pid))
        return pid

    async def cancel(
        self,
        session: AsyncSession,
        org_id: str,
        actor_sub: str,
        *,
        at_cycle_end: bool = True,
        request: Any = None,
    ) -> Subscription:
        sub = await self._current_subscription(session, org_id)
        if sub is None:
            raise NotAllowed("no active subscription")
        await self.provider(sub.provider).cancel_subscription(
            sub.provider_subscription_id, at_cycle_end=at_cycle_end
        )
        if at_cycle_end:
            sub.cancel_at_period_end = True
        else:
            sub.status = "cancelled"
        sub.updated_at = _now()
        await record(
            session,
            "billing.cancel_requested",
            actor=Actor(actor_sub),
            target=("org", org_id),
            request=request,
            at_cycle_end=at_cycle_end,
        )
        return sub

    async def refund(
        self,
        session: AsyncSession,
        payment_id: str,
        *,
        actor: Actor,
        reason: str,
        amount: int | None = None,
        request: Any = None,
    ) -> None:
        """Ask the gateway for a refund. Local records change when its refund webhook arrives."""
        pay = await session.get(Payment, payment_id)
        if (
            pay is None
            or pay.status not in ("captured", "partially_refunded")
            or not pay.provider_payment_id
        ):
            raise NotAllowed("only captured payments can be refunded")
        remaining = pay.amount - pay.refunded_amount
        if amount is not None and not 0 < amount <= remaining:
            raise NotAllowed(f"refund must be between 1 and {remaining}")
        await self.provider(pay.provider).refund(
            pay.provider_payment_id, amount=amount, notes={"reason": reason[:200]}
        )
        await record(
            session,
            "billing.refund_requested",
            actor=actor,
            target=("payment", pay.id),
            request=request,
            amount=amount or remaining,
            reason=reason,
        )

    # -- entitlements ------------------------------------------------------------------------

    async def _current_subscription(
        self, session: AsyncSession, org_id: str
    ) -> Subscription | None:
        subs = (
            (
                await session.execute(
                    select(Subscription)
                    .where(Subscription.org_id == org_id)
                    .order_by(Subscription.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        now = _now()
        for s in subs:
            if self._entitled(s, now):
                return s
        return None

    def _entitled(self, s: Subscription, now: datetime) -> bool:
        if s.status in ENTITLED_STATUSES:
            return True
        end = s.current_period_end
        if end is None:
            return False
        if s.status == "pending":
            return end + self.grace >= now  # a failed renewal keeps access for a few days
        return s.status in ("cancelled", "completed") and end >= now  # paid-for time is honoured

    async def entitlements(self, session: AsyncSession, org_id: str) -> Entitlements:
        sub = await self._current_subscription(session, org_id)
        if sub is None:
            return Entitlements("free", dict(self.free_features))
        plan = await session.get(Plan, sub.plan_code)
        return Entitlements(
            sub.plan_code, dict(plan.features) if plan else {}, sub.status, sub.current_period_end
        )

    # -- credits -----------------------------------------------------------------------------

    credit_balance = staticmethod(credits.balance)
    grant_credits = staticmethod(credits.grant)
    spend_credits = staticmethod(credits.spend)

    # -- webhooks ----------------------------------------------------------------------------

    async def handle_webhook(
        self, session: AsyncSession, provider_name: str, body: bytes, headers: Mapping[str, str]
    ) -> WebhookResult:
        """Verify, deduplicate and apply a gateway event. Raises ``InvalidSignature`` for forgeries.

        Any other exception means "try again": the caller returns a 5xx and the gateway redelivers,
        which is safe because the event row is only kept if processing succeeded.
        """
        prov = self.provider(provider_name)
        events = prov.parse_webhook(body, headers)  # verifies the signature first
        result = WebhookResult()
        for ev in events:
            inserted = await session.execute(
                insert(WebhookEvent)
                .values(
                    provider=prov.name, event_id=ev.event_id, event_type=ev.type, payload=ev.raw
                )
                .on_conflict_do_nothing()
                .returning(WebhookEvent.event_id)
            )
            if inserted.first() is None:
                result.duplicates += 1
                continue
            await self._apply(session, prov, ev)
            row = await session.get(WebhookEvent, (prov.name, ev.event_id))
            if row is not None:
                row.processed_at = _now()
            result.processed += 1
        return result

    async def _org_for(self, session: AsyncSession, ev: PaymentEvent) -> str | None:
        if ev.notes.get("org_id"):
            return str(ev.notes["org_id"])
        for model, col, val in (
            (Subscription, Subscription.provider_subscription_id, ev.subscription_id),
            (Payment, Payment.provider_order_id, ev.order_id),
            (Payment, Payment.provider_payment_id, ev.payment_id),
        ):
            if val:
                found = (await session.execute(select(model.org_id).where(col == val))).first()
                if found:
                    return str(found[0])
        return None

    async def _apply(self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent) -> None:
        t = ev.type
        if t.startswith("ignored."):
            return
        if t.startswith("refund."):
            return await self._refund_event(session, prov, ev)
        if t.startswith("transfer."):
            tr = (
                await session.execute(
                    select(Transfer).where(Transfer.provider_transfer_id == ev.transfer_id)
                )
            ).scalar_one_or_none()
            if tr:
                tr.status = "processed" if t == "transfer.processed" else "failed"
            return
        org_id = await self._org_for(session, ev)
        if org_id is None:
            raise PaymentsError(f"cannot tell which organization event {ev.event_id} belongs to")

        if t.startswith("subscription."):
            await self._subscription_event(session, prov, ev, org_id)
        if t in ("payment.captured", "subscription.charged"):
            await self._captured(session, prov, ev, org_id)
        elif t == "payment.failed":
            await self._failed(session, prov, ev, org_id)

    async def _subscription_event(
        self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent, org_id: str
    ) -> None:
        sub = (
            await session.execute(
                select(Subscription).where(
                    Subscription.provider_subscription_id == ev.subscription_id
                )
            )
        ).scalar_one_or_none()
        if sub is None:
            plan_code = ev.notes.get("plan")
            # Adopt the placeholder created at checkout (Stripe only learns the real id after payment).
            sub = (
                (
                    await session.execute(
                        select(Subscription)
                        .where(
                            Subscription.org_id == org_id,
                            Subscription.plan_code == plan_code,
                            Subscription.status == "created",
                        )
                        .order_by(Subscription.created_at.desc())
                    )
                )
                .scalars()
                .first()
                if plan_code
                else None
            )
            if sub is not None:
                sub.provider_subscription_id = ev.subscription_id or sub.provider_subscription_id
            elif plan_code and ev.subscription_id:
                sub = Subscription(
                    org_id=org_id,
                    plan_code=plan_code,
                    provider=prov.name,
                    provider_subscription_id=ev.subscription_id,
                    status="created",
                )
                session.add(sub)
        if sub is None:
            raise PaymentsError(f"unknown subscription {ev.subscription_id}")
        status = {
            "subscription.activated": "active",
            "subscription.charged": "active",
            "subscription.resumed": "active",
            "subscription.pending": "pending",
            "subscription.halted": "halted",
            "subscription.cancelled": "cancelled",
            "subscription.completed": "completed",
            "subscription.paused": "paused",
        }.get(ev.type)
        if status:
            sub.status = status
        elif ev.type == "subscription.updated" and ev.status:
            sub.status = ev.status
        if ev.period_end:
            sub.current_period_end = ev.period_end
        sub.updated_at = _now()

    async def _failed(
        self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent, org_id: str
    ) -> None:
        pay = await self._payment_for(session, prov, ev, org_id)
        if pay.status not in ("captured", "refunded", "partially_refunded", "failed"):
            pay.status, pay.failure_reason = "failed", ev.failure_reason
            if self.on_payment_failed:
                try:
                    async with session.begin_nested():
                        await self.on_payment_failed(session, pay)
                except Exception:
                    log.exception("payment-failed hook failed for %s", pay.id)

    async def _payment_for(
        self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent, org_id: str
    ) -> Payment:
        pay = None
        if ev.payment_id:
            pay = (
                await session.execute(
                    select(Payment).where(Payment.provider_payment_id == ev.payment_id)
                )
            ).scalar_one_or_none()
        if pay is None and ev.order_id:
            pay = (
                await session.execute(
                    select(Payment)
                    .where(Payment.provider_order_id == ev.order_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
        if pay is None:
            pay = Payment(
                org_id=org_id,
                provider=prov.name,
                provider_order_id=ev.order_id,
                amount=ev.amount or 0,
                currency=(ev.currency or "INR"),
                status="created",
                plan_code=ev.notes.get("plan"),
            )
            session.add(pay)
        pay.provider_payment_id = ev.payment_id or pay.provider_payment_id
        pay.method = ev.method or pay.method
        await session.flush()
        return pay

    async def _captured(
        self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent, org_id: str
    ) -> None:
        if ev.amount is None:
            raise PaymentsError(f"captured event {ev.event_id} has no amount")
        pay = await self._payment_for(session, prov, ev, org_id)
        if pay.status in ("captured", "refunded", "partially_refunded"):
            return  # payment.captured and subscription.charged both announce the same payment
        if pay.amount and pay.status == "created" and pay.amount != ev.amount:
            # Money arrived but not what we asked for: do not grant anything; a person must look.
            pay.status, pay.failure_reason = (
                "amount_mismatch",
                f"expected {pay.amount}, received {ev.amount}",
            )
            await record(
                session,
                "billing.amount_mismatch",
                actor=Actor(None, "system"),
                target=("payment", pay.id),
                expected=pay.amount,
                received=ev.amount,
            )
            return
        pay.amount, pay.currency = ev.amount, (ev.currency or pay.currency)
        pay.status, pay.captured_at = "captured", _now()
        if ev.subscription_id:
            sub = (
                await session.execute(
                    select(Subscription).where(
                        Subscription.provider_subscription_id == ev.subscription_id
                    )
                )
            ).scalar_one_or_none()
            if sub:
                pay.subscription_id, pay.plan_code = sub.id, pay.plan_code or sub.plan_code
        plan = await session.get(Plan, pay.plan_code) if pay.plan_code else None
        if plan and plan.credits > 0:
            await credits.grant(
                session, org_id, plan.credits, reason=f"plan:{plan.code}", ref=f"payment:{pay.id}"
            )
        await record(
            session,
            "billing.payment_captured",
            actor=Actor(None, "system"),
            target=("payment", pay.id),
            org=org_id,
            amount=pay.amount,
            plan=pay.plan_code,
        )
        await self._try_invoice(session, pay, plan, ev.email)

    async def _try_invoice(
        self, session: AsyncSession, pay: Payment, plan: Plan | None, email: str | None
    ) -> None:
        if self.invoicer is None:
            return
        try:
            async with session.begin_nested():  # a failed invoice must not undo the payment record
                invoice = await self.invoicer.issue_for_payment(
                    session, pay, plan, await session.get(BillingProfile, pay.org_id), email=email
                )
                if self.on_receipt:
                    await self.on_receipt(session, pay, invoice)
        except Exception:
            log.exception(
                "invoice issuing failed for payment %s; run issue_missing_invoices", pay.id
            )

    async def issue_missing_invoices(self, session: AsyncSession, *, limit: int = 100) -> int:
        """Sweep for captured payments without an invoice (a job runs this). Returns how many were issued."""
        if self.invoicer is None:
            return 0
        have = select(Invoice.payment_id).where(
            Invoice.kind == "invoice", Invoice.payment_id.is_not(None)
        )
        todo = (
            (
                await session.execute(
                    select(Payment)
                    .where(
                        Payment.status.in_(("captured", "partially_refunded", "refunded")),
                        Payment.id.not_in(have),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        n = 0
        for pay in todo:
            plan = await session.get(Plan, pay.plan_code) if pay.plan_code else None
            await self.invoicer.issue_for_payment(
                session, pay, plan, await session.get(BillingProfile, pay.org_id)
            )
            n += 1
        return n

    async def _refund_event(
        self, session: AsyncSession, prov: PaymentProvider, ev: PaymentEvent
    ) -> None:
        pay = (
            await session.execute(
                select(Payment)
                .where(Payment.provider_payment_id == ev.payment_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if pay is None:
            raise PaymentsError(f"refund for unknown payment {ev.payment_id}")
        if ev.type == "refund.failed":
            return
        fresh = await session.execute(
            insert(Refund)
            .values(
                payment_id=pay.id,
                provider_refund_id=ev.refund_id,
                amount=ev.amount or 0,
                status="processed",
            )
            .on_conflict_do_nothing()
            .returning(Refund.id)
        )
        if fresh.first() is None:
            return
        pay.refunded_amount += ev.amount or 0
        pay.status = "refunded" if pay.refunded_amount >= pay.amount else "partially_refunded"
        plan = await session.get(Plan, pay.plan_code) if pay.plan_code else None
        if plan and plan.credits and pay.status == "refunded":
            bal = await credits.balance(session, pay.org_id)
            take = min(bal, plan.credits)  # claw back what is still unspent, never below zero
            if take:
                await credits.spend(
                    session,
                    pay.org_id,
                    take,
                    reason=f"refund:{plan.code}",
                    ref=f"refund:{ev.refund_id}",
                )
        await record(
            session,
            "billing.refund_processed",
            actor=Actor(None, "system"),
            target=("payment", pay.id),
            amount=ev.amount,
            status=pay.status,
        )
        if self.invoicer:
            original = (
                await session.execute(
                    select(Invoice).where(Invoice.payment_id == pay.id, Invoice.kind == "invoice")
                )
            ).scalar_one_or_none()
            if original and ev.amount:
                try:
                    async with session.begin_nested():
                        await self.invoicer.issue_credit_note(session, original, ev.amount)
                except Exception:
                    log.exception("credit note failed for refund %s", ev.refund_id)

    # -- compliance ---------------------------------------------------------------------------

    def register_with_compliance(self, compliance: Any, orgs_of: Any) -> None:
        """Include billing history in a user's data export. ``orgs_of(session, sub)`` returns the ids of
        organizations whose billing the user manages. Nothing is erased: invoices and payments are
        kept for tax law, which the privacy policy and retention schedule say."""

        async def export(session: AsyncSession, sub: str) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for org_id in await orgs_of(session, sub):
                pays = (
                    (
                        await session.execute(
                            select(Payment)
                            .where(Payment.org_id == org_id)
                            .order_by(Payment.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                invs = (
                    (
                        await session.execute(
                            select(Invoice)
                            .where(Invoice.org_id == org_id)
                            .order_by(Invoice.issued_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                prof = await session.get(BillingProfile, org_id)
                out[org_id] = {
                    "billing_profile": None
                    if prof is None
                    else {
                        "legal_name": prof.legal_name,
                        "email": prof.email,
                        "gstin": prof.gstin,
                        "address": prof.address,
                    },
                    "payments": [
                        {
                            "amount": x.amount,
                            "currency": x.currency,
                            "status": x.status,
                            "at": x.captured_at,
                        }
                        for x in pays
                    ],
                    "invoices": [
                        {
                            "number": i.number,
                            "kind": i.kind,
                            "total": i.total,
                            "issued_at": i.issued_at,
                        }
                        for i in invs
                    ],
                }
            return out

        compliance.register_exporter("billing", export)

    # -- marketplace (Razorpay Route) --------------------------------------------------------

    async def transfer(
        self,
        session: AsyncSession,
        payment_id: str,
        *,
        seller_org_id: str,
        account_id: str,
        amount: int,
        actor: Actor,
        request: Any = None,
    ) -> Transfer:
        """Send part of a captured payment to a seller's linked account through the licensed gateway."""
        pay = await session.get(Payment, payment_id)
        if pay is None or pay.status != "captured":
            raise NotAllowed("only captured payments can be split")
        sent = int(
            (
                await session.execute(
                    select(func.coalesce(func.sum(Transfer.amount), 0)).where(
                        Transfer.payment_id == pay.id, Transfer.status != "failed"
                    )
                )
            ).scalar_one()
        )
        if amount <= 0 or sent + amount > pay.amount:
            raise NotAllowed("transfers cannot exceed the payment amount")
        prov = self.provider(pay.provider)
        if not hasattr(prov, "transfer"):
            raise NotAllowed(f"{prov.name} does not support split payments here")
        res = await prov.transfer(
            pay.provider_payment_id,
            account_id=account_id,
            amount=amount,
            notes={"seller_org_id": seller_org_id},
        )  # type: ignore[attr-defined]
        tr = Transfer(
            payment_id=pay.id,
            seller_org_id=seller_org_id,
            provider_account_id=account_id,
            provider_transfer_id=res.id,
            amount=amount,
            status=res.status,
        )
        session.add(tr)
        await record(
            session,
            "billing.transfer_created",
            actor=actor,
            target=("payment", pay.id),
            request=request,
            seller=seller_org_id,
            amount=amount,
        )
        return tr


__all__ = [
    "Checkout",
    "Entitlements",
    "InvalidSignature",
    "NotAllowed",
    "Payments",
    "PaymentsError",
    "PlanSpec",
    "ProviderError",
    "UnknownPlan",
    "WebhookResult",
]
