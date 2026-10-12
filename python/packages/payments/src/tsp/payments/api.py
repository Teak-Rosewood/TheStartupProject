"""FastAPI routes. User, organization-membership and session dependencies are passed in."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .invoicing import export_csv
from .models import Invoice
from .providers import InvalidSignature, ProviderError
from .service import NotAllowed, Payments, PaymentsError, UnknownPlan


class CheckoutIn(BaseModel):
    plan_code: str
    provider: str | None = None


class ConfirmIn(BaseModel):
    provider: str | None = None
    payment_id: str
    signature: str
    order_id: str | None = None
    subscription_id: str | None = None


class CancelIn(BaseModel):
    at_cycle_end: bool = True


class ProfileIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=200)
    address: str = Field(min_length=5, max_length=500)
    gstin: str | None = None
    country: str = Field(default="IN", min_length=2, max_length=2)
    state_code: str | None = None


def _http(e: Exception) -> HTTPException:
    if isinstance(e, UnknownPlan):
        return HTTPException(404, detail={"type": "unknown_plan", "message": f"no such plan: {e}"})
    if isinstance(e, NotAllowed):
        return HTTPException(409, detail={"type": "not_allowed", "message": str(e)})
    if isinstance(e, ProviderError):
        return HTTPException(502, detail={"type": "payment_provider_error", "message": str(e)})
    if isinstance(e, PaymentsError):
        return HTTPException(400, detail={"type": e.code, "message": str(e)})
    return HTTPException(400, detail={"type": "invalid_request", "message": str(e)})


def build_router(
    p: Payments,
    account: Callable[..., Any],  # dependency -> object with .sub and .email
    org_member: Callable[
        [str], Callable[..., Any]
    ],  # org_member("admin") -> dependency (path param org_id)
    session: Callable[..., Any],
) -> APIRouter:
    r = APIRouter()
    admin = org_member("admin")
    member = org_member("member")

    @r.get("/billing/plans")
    async def plans(s: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
        return [
            {
                "code": x.code,
                "name": x.name,
                "kind": x.kind,
                "amount": x.amount,
                "currency": x.currency,
                "interval": x.interval,
                "credits": x.credits,
                "features": x.features,
            }
            for x in await p.plans(s)
        ]

    @r.get("/orgs/{org_id}/billing")
    async def overview(
        org_id: str, _m: Any = Depends(member), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        ent = await p.entitlements(s, org_id)
        return {
            "plan": ent.plan_code,
            "features": ent.features,
            "subscription_status": ent.subscription_status,
            "valid_until": ent.valid_until,
            "credits": await p.credit_balance(s, org_id),
        }

    @r.post("/orgs/{org_id}/billing/checkout", status_code=201)
    async def checkout(
        org_id: str,
        body: CheckoutIn,
        request: Request,
        u: Any = Depends(account),
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        try:
            co = await p.start_checkout(
                s,
                org_id,
                body.plan_code,
                user_sub=u.sub,
                email=u.email or "",
                provider=body.provider,
                request=request,
            )
        except (PaymentsError, ProviderError) as e:
            raise _http(e) from e
        return {"provider": co.provider, "kind": co.kind, "checkout": co.payload}

    @r.post("/orgs/{org_id}/billing/confirm")
    async def confirm(org_id: str, body: ConfirmIn, _m: Any = Depends(admin)) -> dict[str, bool]:
        """UX only: tells the browser the callback was genuine. Access is granted by the webhook."""
        try:
            ok = p.provider(body.provider).verify_checkout(
                payment_id=body.payment_id,
                signature=body.signature,
                order_id=body.order_id,
                subscription_id=body.subscription_id,
            )
        except PaymentsError as e:
            raise _http(e) from e
        return {"verified": ok}

    @r.post("/orgs/{org_id}/billing/cancel")
    async def cancel(
        org_id: str,
        body: CancelIn,
        request: Request,
        u: Any = Depends(account),
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        try:
            sub = await p.cancel(s, org_id, u.sub, at_cycle_end=body.at_cycle_end, request=request)
        except (PaymentsError, ProviderError) as e:
            raise _http(e) from e
        return {"status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end}

    @r.get("/orgs/{org_id}/billing/profile")
    async def get_profile(
        org_id: str, _m: Any = Depends(admin), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        prof = await p.get_profile(s, org_id)
        return (
            {}
            if prof is None
            else {
                "legal_name": prof.legal_name,
                "email": prof.email,
                "address": prof.address,
                "gstin": prof.gstin,
                "country": prof.country,
                "state_code": prof.state_code,
            }
        )

    @r.put("/orgs/{org_id}/billing/profile")
    async def put_profile(
        org_id: str, body: ProfileIn, _m: Any = Depends(admin), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        try:
            prof = await p.set_profile(s, org_id, **body.model_dump())
        except ValueError as e:
            raise _http(e) from e
        return {"gstin": prof.gstin, "state_code": prof.state_code}

    @r.get("/orgs/{org_id}/invoices")
    async def invoices(
        org_id: str, _m: Any = Depends(admin), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        rows = (
            await s.execute(
                select(Invoice)
                .where(Invoice.org_id == org_id)
                .order_by(Invoice.issued_at.desc())
                .limit(200)
            )
        ).scalars()
        return [
            {
                "id": i.id,
                "number": i.number,
                "kind": i.kind,
                "issued_at": i.issued_at,
                "total": i.total,
                "currency": i.currency,
            }
            for i in rows
        ]

    @r.get("/orgs/{org_id}/invoices/{invoice_id}/pdf")
    async def invoice_pdf(
        org_id: str, invoice_id: str, _m: Any = Depends(admin), s: AsyncSession = Depends(session)
    ) -> dict[str, str | None]:
        inv = await s.get(Invoice, invoice_id)
        if inv is None or inv.org_id != org_id or p.invoicer is None:
            raise HTTPException(404, "not found")
        return {"url": await p.invoicer.pdf_url(inv)}

    @r.post("/webhooks/{provider}")
    async def webhook(
        provider: str, request: Request, s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        body = await request.body()
        try:
            result = await p.handle_webhook(s, provider, body, dict(request.headers))
        except InvalidSignature as e:
            raise HTTPException(
                400, detail={"type": "invalid_signature", "message": "signature mismatch"}
            ) from e
        except PaymentsError as e:
            if "is not configured" in str(e):
                raise HTTPException(404, "unknown provider") from e
            raise
        await (
            s.commit()
        )  # acknowledge only after the work is durable; an error above makes the gateway retry
        return {"processed": result.processed, "duplicates": result.duplicates}

    return r


def require_feature(p: Payments, feature: str, session: Callable[..., Any]) -> Callable[..., Any]:
    """Dependency for routes with an ``org_id`` path parameter: 402 unless the plan includes ``feature``."""

    async def dep(org_id: str, s: AsyncSession = Depends(session)) -> None:
        ent = await p.entitlements(s, org_id)
        if not ent.features.get(feature):
            raise HTTPException(
                402,
                detail={
                    "type": "upgrade_required",
                    "message": f"your plan does not include {feature}",
                    "feature": feature,
                    "plan": ent.plan_code,
                },
            )

    return dep


async def finance_csv(p: Payments, s: AsyncSession, fy: str) -> str:
    """For the admin console and accountants: one financial year of invoices and credit notes."""
    return await export_csv(s, fy)


__all__ = ["build_router", "finance_csv", "require_feature"]
