"""Wire reader engagement into the app: polls, receipts, streaks, points (always) and submissions with
model moderation (when an AI provider is configured)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tsp.accounts import Accounts
from tsp.cms import ComplaintsDesk
from tsp.engage import (
    EngageConfig,
    Submissions,
    build_workflow,
    default_moderators,
    push_prompts,
)
from tsp.pipeline import Price, Runner

MODERATION_MODEL = "claude-haiku-5-5"


def engage_config(env: Mapping[str, str] | None = None) -> EngageConfig:
    """Settings from the environment; everything has a default.

    ``ENGAGE_TIMEZONE`` (weeks start Monday there), ``ENGAGE_WEEKLY_GOAL``, ``ENGAGE_ONBOARDING_STEPS``
    (comma separated), ``ENGAGE_REQUIRE_SUBSCRIPTION`` (``0`` lets any onboarded reader submit)."""
    e = os.environ if env is None else env
    base = EngageConfig()
    steps = tuple(s.strip() for s in e.get("ENGAGE_ONBOARDING_STEPS", "").split(",") if s.strip())
    return EngageConfig(
        timezone=e.get("ENGAGE_TIMEZONE", base.timezone),
        weekly_goal=int(e.get("ENGAGE_WEEKLY_GOAL", base.weekly_goal)),
        require_subscription=e.get("ENGAGE_REQUIRE_SUBSCRIPTION", "1") != "0",
        required_onboarding=steps or base.required_onboarding,
    )


def subscriber_check(accounts: Accounts, payments: Any | None) -> Any:
    """A reader is a subscriber when any organization they belong to has an active paid plan."""
    if payments is None:
        return None

    async def entitled(session: AsyncSession, user_id: str, org_id: str) -> bool:
        for org, _role in await accounts.list_orgs(session, user_id):
            ent = await payments.entitlements(session, org.id)
            if ent.subscription_status in ("active", "trialing"):
                return True
        return False

    return entitled


class EngageModeration:
    """The moderation workflow, its runner and the factory for ``Submissions``."""

    def __init__(self, cfg: EngageConfig, llm: Any, flags: Any, desk: Any, entitled: Any):
        env = os.environ
        model = env.get("ENGAGE_MODERATION_MODEL", MODERATION_MODEL)
        # USD per million tokens. Defaults are placeholders for a small model: set the real prices.
        price = Price(
            float(env.get("ENGAGE_PRICE_IN", "1.0")), float(env.get("ENGAGE_PRICE_OUT", "5.0"))
        )
        mods = default_moderators(
            [w for w in env.get("ENGAGE_BLOCKLIST", "").split(",") if w.strip()]
        )
        for m in mods:
            if getattr(m, "model", "x") is None:
                m.model = model  # type: ignore[attr-defined]
        self.cfg, self.desk_factory, self.entitled = cfg, desk, entitled
        self.workflow = build_workflow(mods, cfg)
        self.runner = Runner(None, [self.workflow], llm=llm, flags=flags, prices={model: price})

    def submissions(self, session: AsyncSession) -> Submissions:
        return Submissions(
            session,
            self.cfg,
            self.runner,
            self.workflow,
            entitled=self.entitled,
            desk=self.desk_factory(session),
        )

    def lifespan(self) -> Any:
        """Give the runner the app's database and make sure the rubric prompts exist."""

        @asynccontextmanager
        async def run(app: Any) -> AsyncIterator[None]:
            db = getattr(app.state, "db", None)
            if db is not None:
                self.runner.db = db
                async with db.session() as s:
                    await push_prompts(s)
            yield

        return run


def desk_for(desk_factory: Any) -> Any:
    return desk_factory if callable(desk_factory) else ComplaintsDesk
