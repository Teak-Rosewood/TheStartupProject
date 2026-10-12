from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, Run, Step
from .runner import Runner


class ResumeBody(BaseModel):
    step: str | None = None
    data: Any = None


def _run(r: Run) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "workflow": r.workflow,
        "version": r.version,
        "status": r.status,
        "budget_usd": float(r.budget_usd) if r.budget_usd is not None else None,
        "spent_usd": float(r.spent_usd),
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error,
    }


def build_router(
    runner: Runner, org_of: Callable[..., Any], session: Callable[..., Any]
) -> APIRouter:
    """Runs of the caller's organization: list, detail with step trace, and resume for human steps.

    ``org_of`` is a dependency returning the caller's organization id; use one that requires an
    admin or owner role, since resuming a run approves work.
    """
    r = APIRouter(prefix="/pipeline")

    async def own(s: AsyncSession, org: str, run_id: uuid.UUID) -> Run:
        run = await s.get(Run, run_id)
        if run is None or run.org_id != org:
            raise HTTPException(404, "run not found")
        return run

    @r.get("/runs")
    async def runs(
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        org: str = Depends(org_of),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        q = select(Run).where(Run.org_id == org).order_by(Run.started_at.desc()).limit(limit)
        if status:
            q = q.where(Run.status == status)
        return [_run(x) for x in (await s.execute(q)).scalars()]

    @r.get("/runs/{run_id}")
    async def detail(
        run_id: uuid.UUID, org: str = Depends(org_of), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        run = await own(s, org, run_id)
        steps = (
            await s.execute(
                select(Step)
                .where(Step.run_id == run_id)
                .order_by(Step.started_at.nulls_last(), Step.name, Step.attempt)
            )
        ).scalars()
        events = (
            await s.execute(select(Event).where(Event.run_id == run_id).order_by(Event.id))
        ).scalars()
        return {
            **_run(run),
            "input": run.input,
            "output": run.output,
            "steps": [
                {
                    "name": x.name,
                    "attempt": x.attempt,
                    "status": x.status,
                    "model": x.model,
                    "prompt": f"{x.prompt_key}@{x.prompt_version}"
                    if x.prompt_key and x.prompt_version
                    else None,
                    "tokens_in": x.tokens_in,
                    "tokens_out": x.tokens_out,
                    "cost_usd": float(x.cost_usd),
                    "started_at": x.started_at,
                    "finished_at": x.finished_at,
                    "error": x.error,
                }
                for x in steps
            ],
            "events": [
                {"at": e.at, "step": e.step, "kind": e.kind, "data": e.data} for e in events
            ],
        }

    @r.post("/runs/{run_id}/resume")
    async def resume(
        run_id: uuid.UUID,
        body: ResumeBody,
        org: str = Depends(org_of),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        run = await own(s, org, run_id)
        try:
            await runner.resume(run_id, body.step, body.data, session=s)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        await s.refresh(run)
        return _run(run)

    return r
