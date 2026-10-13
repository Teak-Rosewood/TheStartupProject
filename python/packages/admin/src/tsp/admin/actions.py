"""The one place staff change things: a form with a mandatory reason, CSRF token and audit entry."""

from __future__ import annotations

import hmac
import re
from datetime import UTC, datetime
from typing import Any

from sqladmin import BaseView, expose
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.requests import Request
from starlette.responses import Response
from tsp.accounts import Accounts
from tsp.audit import Actor, record
from tsp.compliance import Compliance, DataRequest
from tsp.flags import Flags, FlagUpdate
from tsp.flags import service as flag_service
from tsp.payments import Payments, export_csv
from tsp.payments.gst import financial_year

from .roles import KILL_SWITCH_ONLY, STAFF_ACTIONS, can_run

_SUB = re.compile(r"^[0-9a-fA-F-]{36}$")
_PAYMENT_ID = re.compile(r"^[0-9a-f]{32}$")
_FY = re.compile(r"^\d{4}-\d{2}$")


def make_view(
    engine: AsyncEngine,
    accounts: Accounts,
    compliance: Compliance | None,
    payments: Payments | None = None,
    flags: Flags | None = None,
) -> type[BaseView]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class StaffActionsView(BaseView):
        name = "Staff actions"
        identity = "staff-actions"
        icon = "fa-solid fa-user-shield"
        methods = ["GET", "POST"]

        @expose("/staff-actions", methods=["GET", "POST"], identity="staff-actions")
        async def page(self, request: Request) -> Response:
            staff = request.session.get("staff") or {}
            role = staff.get("role")
            message, ok = "", True
            if request.method == "POST":
                form = await request.form()
                message, ok = await self._run(request, staff, role, form)
            async with sessions() as s:
                pending = (
                    (
                        await s.execute(
                            select(DataRequest)
                            .where(DataRequest.kind == "delete", DataRequest.status == "pending")
                            .order_by(DataRequest.scheduled_for)
                            .limit(50)
                        )
                    )
                    .scalars()
                    .all()
                )
            return await self.templates.TemplateResponse(
                request,
                "staff_actions.html",
                {
                    "staff": staff,
                    "message": message,
                    "ok": ok,
                    "pending": pending,
                    "actions": sorted(a for a in STAFF_ACTIONS if can_run(role, a)),
                },
            )

        async def _run(
            self, request: Request, staff: dict[str, Any], role: str | None, form: Any
        ) -> tuple[str, bool]:
            if not hmac.compare_digest(str(form.get("csrf", "")), str(staff.get("csrf", "x"))):
                return "invalid form token; reload the page", False
            action, sub, reason = (
                str(form.get("action", "")),
                str(form.get("target", "")).strip(),
                str(form.get("reason", "")).strip(),
            )
            if not can_run(role, action):
                return f"your role ({role}) cannot run {action}", False
            is_refund = action == "refund_payment"
            is_flag = action.startswith("flag_")
            if is_flag:
                if role in KILL_SWITCH_ONLY and not sub.startswith("kill:"):
                    return (
                        "your role can only change kill switches (keys starting with kill:)",
                        False,
                    )
                if not flag_service.KEY.match(sub):
                    return "that is not a valid flag key", False
            elif is_refund and not _PAYMENT_ID.match(sub):
                return "that is not a valid payment id", False
            elif not is_refund and not _SUB.match(sub):
                return "that is not a valid user id", False
            if len(reason) < 10:
                return "a reason of at least 10 characters is required", False
            amount_raw = str(form.get("amount", "")).strip()
            if action == "flag_rollout":
                if not (amount_raw.isdigit() and 0 <= int(amount_raw) <= 100):
                    return "rollout needs a percentage from 0 to 100 in the amount field", False
            elif amount_raw and not (is_refund and amount_raw.isdigit() and int(amount_raw) > 0):
                return (
                    "amount must be a positive whole number of paise, and only applies to refunds",
                    False,
                )
            actor = Actor(staff.get("sub"), role or "staff")
            try:
                async with sessions() as s:
                    if is_flag:
                        if flags is None:
                            return "feature flags are not configured", False
                        update = {
                            "flag_on": FlagUpdate(enabled=True),
                            "flag_off": FlagUpdate(enabled=False),
                            "flag_rollout": FlagUpdate(
                                enabled=True, rollout_percent=int(amount_raw or 0)
                            ),
                        }[action]
                        await flags.set(s, sub, update, actor=actor, reason=reason, request=request)
                        await s.commit()
                        return f"flag {sub} updated", True
                    if is_refund:
                        if payments is None:
                            return "payments are not configured", False
                        await payments.refund(
                            s,
                            sub,
                            actor=actor,
                            reason=reason,
                            amount=int(amount_raw) if amount_raw else None,
                            request=request,
                        )
                        await s.commit()
                        return (
                            "refund requested; the payment updates when the gateway confirms it",
                            True,
                        )
                    if action in ("disable_user", "enable_user"):
                        await accounts.set_disabled(
                            s,
                            sub,
                            action == "disable_user",
                            actor=actor,
                            reason=reason,
                            request=request,
                        )
                        if compliance and compliance.identity and action == "disable_user":
                            await compliance.identity.disable(sub)
                    elif action == "sign_out_user":
                        if compliance and compliance.identity:
                            await compliance.identity.sign_out(sub)
                        await record(
                            s,
                            "user.signed_out",
                            actor=actor,
                            target=("user", sub),
                            request=request,
                            reason=reason,
                        )
                    elif compliance is None:
                        return "compliance is not configured", False
                    else:
                        req = (
                            (
                                await s.execute(
                                    select(DataRequest).where(
                                        DataRequest.user_sub == sub,
                                        DataRequest.kind == "delete",
                                        DataRequest.status == "pending",
                                    )
                                )
                            )
                            .scalars()
                            .first()
                        )
                        if req is None:
                            return "no pending deletion for that user", False
                        if action == "execute_deletion":
                            await compliance.execute_deletion(s, req, actor=actor)
                        else:
                            await compliance.cancel_delete(s, sub, request=request)
                        await record(
                            s,
                            f"admin.{action}",
                            actor=actor,
                            target=("user", sub),
                            request=request,
                            reason=reason,
                        )
                    await s.commit()
            except Exception as e:
                return f"failed: {e}", False
            return f"{action.replace('_', ' ')} done", True

    return StaffActionsView


def make_finance_view(engine: AsyncEngine) -> type[BaseView]:
    """Download a financial year's invoices and credit notes for the accountant (audited)."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class FinanceToolsView(BaseView):
        name = "Invoice export (CSV)"
        identity = "finance-tools"
        icon = "fa-solid fa-file-csv"

        @expose("/finance/invoices.csv", methods=["GET"], identity="finance-tools")
        async def invoices_csv(self, request: Request) -> Response:
            staff = request.session.get("staff") or {}
            if staff.get("role") not in ("finance", "superadmin"):
                return Response("forbidden", status_code=403)
            fy = request.query_params.get("fy") or financial_year(datetime.now(UTC))[0]
            if not _FY.match(fy):
                return Response("fy must look like 2026-27", status_code=400)
            async with sessions() as s:
                body = await export_csv(s, fy)
                await record(
                    s,
                    "admin.exported_invoices",
                    actor=Actor(staff.get("sub"), staff["role"]),
                    request=request,
                    fy=fy,
                )
                await s.commit()
            return Response(
                body,
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="invoices-{fy}.csv"'},
            )

    return FinanceToolsView


def make_metrics_view(engine: AsyncEngine) -> type[BaseView]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def inr(minor: int) -> str:
        return f"INR {minor / 100:,.0f}"

    class MetricsView(BaseView):
        name = "Metrics"
        identity = "metrics"
        icon = "fa-solid fa-chart-line"

        @expose("/metrics", methods=["GET"], identity="metrics")
        async def page(self, request: Request) -> Response:
            staff = request.session.get("staff") or {}
            if staff.get("role") not in ("finance", "superadmin"):
                return Response("forbidden", status_code=403)
            from tsp.growth import metrics

            async with sessions() as s:
                o = await metrics.overview(s)
                signups = await metrics.signups_by_day(s, 30)
                events = await metrics.top_events(s, 7)
            cards = [
                ("MRR", inr(o["mrr"])),
                ("ARR", inr(o["arr"])),
                ("ARPU", inr(o["arpu"])),
                ("Active subscriptions", o["active_subscriptions"]),
                ("Churn (30 days)", f"{o['churn_30d_percent']}%"),
                ("Revenue (30 days)", inr(o["revenue_30d"])),
                ("Refunded (30 days)", inr(o["refunded_30d"])),
                ("Users", o["users_total"]),
                ("Sign-ups (7 / 30 days)", f"{o['signups_7d']} / {o['signups_30d']}"),
                ("Waitlist (confirmed / total)", f"{o['leads_confirmed']} / {o['leads_total']}"),
            ]
            return await self.templates.TemplateResponse(
                request, "metrics.html", {"cards": cards, "signups": signups, "events": events}
            )

    return MetricsView
