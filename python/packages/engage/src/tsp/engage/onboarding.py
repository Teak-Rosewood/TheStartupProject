from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngageConfig
from .models import OnboardingStep


class Onboarding:
    """Steps a reader must finish before they may take part (for example before posting a take)."""

    def __init__(self, session: AsyncSession, cfg: EngageConfig | None = None):
        self.s, self.cfg = session, cfg or EngageConfig()

    async def complete(self, user_id: str, org_id: str, step: str) -> bool:
        """True when this call completed the step (False if it already was). Unknown steps are refused."""
        if step not in self.cfg.required_onboarding:
            raise ValueError(f"unknown onboarding step {step!r}")
        res = await self.s.execute(
            insert(OnboardingStep)
            .values(user_id=user_id, org_id=org_id, step=step)
            .on_conflict_do_nothing()
            .returning(OnboardingStep.step)
        )
        return res.first() is not None

    async def done(self, user_id: str, org_id: str) -> list[str]:
        rows = await self.s.execute(
            select(OnboardingStep.step).where(
                OnboardingStep.user_id == user_id, OnboardingStep.org_id == org_id
            )
        )
        return sorted(rows.scalars())

    async def remaining(self, user_id: str, org_id: str) -> list[str]:
        done = set(await self.done(user_id, org_id))
        return [s for s in self.cfg.required_onboarding if s not in done]

    async def is_complete(self, user_id: str, org_id: str) -> bool:
        return not await self.remaining(user_id, org_id)
