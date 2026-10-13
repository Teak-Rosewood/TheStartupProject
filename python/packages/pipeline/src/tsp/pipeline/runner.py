from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.ai.structured import structured
from tsp.jobs import JobContext, JobRegistry, enqueue

from . import trace
from .budget import BudgetExceeded, Price, UnpricedModel, estimate_tokens
from .models import Run, Step
from .prompts import PromptRegistry
from .workflow import StepDef, Workflow, bind_input, coerce_input, to_json

JOB = "pipeline.step"
TERMINAL = ("succeeded", "failed", "cancelled")
T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)


class PipelineDisabled(Exception):
    """The ``pipeline`` kill switch is on: no new runs."""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class StepContext:
    """What a step function receives: identity, the database session, and a metered LLM."""

    run_id: uuid.UUID
    org_id: str
    step: str
    attempt: int
    session: AsyncSession
    runner: Runner
    budget: Decimal | None
    spent_before: Decimal
    prompt_key: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal(0)
    prompt_version: int | None = None
    _models: list[str] = field(default_factory=list)

    @property
    def spent(self) -> Decimal:
        return self.spent_before + self.cost

    def _price(self, model: str) -> Price | None:
        return self.runner.prices.get(model)

    def check_budget(self, model: str, estimated_in: int, max_out: int | None) -> None:
        """Refuse a call that would take the run past its budget (checked before every LLM call)."""
        if self.budget is None:
            return
        price = self._price(model)
        if price is None:
            raise UnpricedModel(f"model {model!r} has no price; add it to Runner(prices=...)")
        if self.spent >= self.budget:
            raise BudgetExceeded(f"spent ${self.spent} of ${self.budget}")
        projected = self.spent + price.cost(estimated_in, max_out or 0)
        if projected > self.budget:
            raise BudgetExceeded(f"next call could cost up to ${projected} of ${self.budget}")

    async def emit(self, kind: str, **data: Any) -> None:
        await trace.emit(self.session, self.run_id, kind, self.step, **data)

    async def llm(
        self,
        variables: Mapping[str, Any] | None = None,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        prompt: str | None = None,
        response_schema: type[M] | None = None,
        **params: Any,
    ) -> Any:
        """Call a model from this step: loads the active prompt, checks the budget, records tokens and
        cost. Returns the reply text, or a validated ``response_schema`` model.

        With a prompt, its rendered body is the user message (or the system message when ``messages``
        is given). The prompt's ``params`` are defaults for ``temperature`` and the like.
        """
        key = prompt or self.prompt_key
        msgs = list(messages or [])
        call_params: dict[str, Any] = {}
        hint: str | None = None
        if key:
            p = await self.runner.prompts.get(self.session, key)
            self.prompt_key, self.prompt_version = p.key, p.version
            text, hint, call_params = p.render(dict(variables or {})), p.model_hint, dict(p.params)
            msgs = [{"role": "system" if messages else "user", "content": text}, *msgs]
        elif not msgs:
            raise ValueError("ctx.llm needs a prompt or messages")
        call_params.update(params)
        chosen = model or self.model or hint
        if not chosen:
            raise ValueError(
                "no model: set model= on the step, the prompt's model_hint or the call"
            )
        max_out = call_params.get("max_tokens")

        async def call(m: list[dict[str, Any]]) -> Any:
            self.check_budget(
                chosen, estimate_tokens(" ".join(str(x.get("content", "")) for x in m)), max_out
            )
            resp = await self.runner.llm.chat.create(model=chosen, messages=m, **call_params)
            await self._record(chosen, resp)
            return resp

        if response_schema is not None:
            parsed, _ = await structured(call, msgs, response_schema)
            return parsed
        resp = await call(msgs)
        return "".join(c.message.content or "" for c in resp.choices)

    async def _record(self, requested: str, resp: Any) -> None:
        usage = resp.usage
        tin = usage.prompt_tokens if usage else 0
        tout = usage.completion_tokens if usage else 0
        routed = (resp.routed_via or "").split("/", 1)[-1]
        price = self._price(requested) or self._price(routed)
        if price is None and self.budget is not None:
            raise UnpricedModel(f"model {requested!r} ({routed}) has no price")
        cost = price.cost(tin, tout) if price else Decimal(0)
        self.tokens_in, self.tokens_out, self.cost = (
            self.tokens_in + tin,
            self.tokens_out + tout,
            self.cost + cost,
        )
        self._models.append(routed or requested)
        await self.emit(
            "llm.call",
            model=routed or requested,
            tokens_in=tin,
            tokens_out=tout,
            cost_usd=str(cost),
            prompt=f"{self.prompt_key}@{self.prompt_version}" if self.prompt_key else None,
            priced=price is not None,
        )

    async def map(
        self,
        items: Iterable[T],
        fn: Callable[[StepContext, T], Awaitable[Any]],
        concurrency: int = 4,
    ) -> list[Any]:
        """Fan out over ``items`` concurrently inside this step; results keep the item order. The
        step is the checkpoint: if it fails, the whole fan-out runs again."""
        sem = asyncio.Semaphore(concurrency)

        async def one(item: T) -> Any:
            async with sem:
                return await fn(self, item)

        return list(await asyncio.gather(*(one(i) for i in items)))


class Runner:
    """Starts, resumes and executes workflow runs. Each step is a ``pipeline.step`` job; finished
    steps are checkpoints and never run again, so a crashed worker's run continues from the first
    unfinished step. Delivery is at-least-once: make steps safe to repeat.

    ``llm`` is anything with ``chat.create(model=, messages=, **params)`` (an ``LLMClient``).
    ``prices`` (USD per million tokens, by model id) are required for runs with a budget.
    """

    def __init__(
        self,
        database: Any,
        workflows: Iterable[Workflow],
        *,
        llm: Any = None,
        prompts: PromptRegistry | None = None,
        prices: Mapping[str, Price] | None = None,
        flags: Any = None,
        retry_delay: Callable[[int], timedelta] = lambda attempt: timedelta(
            seconds=min(300, 5 * 2 ** (attempt - 1))
        ),
    ):
        self.db = database
        self.workflows: dict[str, Workflow] = {}
        for wf in workflows:
            wf.validate()
            self.workflows[wf.name] = wf
        self.llm = llm
        self.prompts = prompts or PromptRegistry()
        self.prices = dict(prices or {})
        self.flags = flags
        self.retry_delay = retry_delay

    def register(self, registry: JobRegistry) -> None:
        """Add the ``pipeline.step`` job to your job registry."""
        registry.job(JOB)(self._handle)

    @asynccontextmanager
    async def _use(self, session: AsyncSession | None) -> Any:
        if session is not None:
            yield session
        else:
            async with self.db.session() as s:
                yield s

    def _workflow(self, run: Run) -> Workflow:
        wf = self.workflows.get(run.workflow)
        if wf is None:
            raise LookupError(f"workflow {run.workflow!r} is not registered in this process")
        if wf.version != run.version:
            raise RuntimeError(
                f"run {run.id} was started under {run.workflow} v{run.version}; this process has v{wf.version}"
            )
        return wf

    # -- public API ------------------------------------------------------------------------------

    async def start(
        self,
        org_id: str,
        wf: Workflow,
        input: Any,
        budget_usd: float | Decimal | None = None,
        *,
        parent_run_id: uuid.UUID | None = None,
        session: AsyncSession | None = None,
    ) -> uuid.UUID:
        """Create a run and queue its first steps. Pass ``session`` to start inside your transaction."""
        if wf.name not in self.workflows:
            raise LookupError(f"workflow {wf.name!r} is not registered with this Runner")
        async with self._use(session) as s:
            if self.flags is not None and await self.flags.killed(s, "pipeline"):
                raise PipelineDisabled("pipeline runs are switched off (kill:pipeline)")
            run = Run(
                org_id=org_id,
                workflow=wf.name,
                version=wf.version,
                input=to_json(input),
                status="running",
                budget_usd=Decimal(str(budget_usd)) if budget_usd is not None else None,
                parent_run_id=parent_run_id,
            )
            s.add(run)
            await s.flush()
            await trace.emit(s, run.id, "run.started", workflow=wf.name, version=wf.version)
            await self._schedule(s, run)
            return run.id

    async def resume(
        self,
        run_id: uuid.UUID,
        step: str | None = None,
        data: Any = None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Supply a human step's output (``step`` and ``data``), or with no step re-queue whatever
        is ready, for example after the ``pipeline`` kill switch was switched off."""
        async with self._use(session) as s:
            run = await self._lock(s, run_id)
            if run.status in TERMINAL:
                raise ValueError(f"run is {run.status}")
            if step is not None:
                row = await self._latest(s, run_id, step)
                if row is None or row.status != "waiting":
                    raise ValueError(f"step {step!r} is not waiting for input")
                row.status, row.output, row.finished_at = "succeeded", to_json(data), _now()
                await trace.emit(s, run_id, "step.resumed", step)
            await self._schedule(s, run)

    async def cancel(self, run_id: uuid.UUID, *, session: AsyncSession | None = None) -> None:
        async with self._use(session) as s:
            run = await self._lock(s, run_id)
            if run.status not in TERMINAL:
                run.status, run.finished_at = "cancelled", _now()
                await trace.emit(s, run_id, "run.cancelled")

    # -- internals -------------------------------------------------------------------------------

    async def _lock(self, s: AsyncSession, run_id: uuid.UUID) -> Run:
        run = (
            await s.execute(
                select(Run)
                .where(Run.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if run is None:
            raise LookupError(f"no run {run_id}")
        return run

    async def _latest(self, s: AsyncSession, run_id: uuid.UUID, name: str) -> Step | None:
        return (
            await s.execute(
                select(Step)
                .where(Step.run_id == run_id, Step.name == name)
                .order_by(Step.attempt.desc())
                .limit(1)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _latest_all(self, s: AsyncSession, run_id: uuid.UUID) -> dict[str, Step]:
        rows = (
            await s.execute(
                select(Step)
                .where(Step.run_id == run_id)
                .order_by(Step.name, Step.attempt)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        return {r.name: r for r in rows}  # the last attempt of each step wins

    async def _enqueue_step(
        self,
        s: AsyncSession,
        run_id: uuid.UUID,
        name: str,
        attempt: int,
        at: datetime | None = None,
    ) -> None:
        await enqueue(
            s,
            JOB,
            {"run_id": str(run_id), "step": name, "attempt": attempt},
            run_at=at,
            unique_key=f"{run_id}:{name}:{attempt}",
            max_attempts=5,
        )

    async def _schedule(self, s: AsyncSession, run: Run) -> None:
        """Queue every step whose dependencies are done, then settle the run's status. Needs the run locked."""
        if run.status in TERMINAL:
            return
        wf = self._workflow(run)
        latest = await self._latest_all(s, run.id)
        paused = self.flags is not None and await self.flags.killed(s, "pipeline")
        for sd in wf.steps.values():
            if sd.name in latest or paused:
                continue
            if not all(d in latest and latest[d].status == "succeeded" for d in sd.after):
                continue
            row = Step(
                run_id=run.id,
                name=sd.name,
                attempt=1,
                status="waiting" if sd.kind == "human" else "queued",
            )
            s.add(row)
            latest[sd.name] = row
            if sd.kind == "human":
                await trace.emit(s, run.id, "step.waiting", sd.name)
            else:
                await trace.emit(s, run.id, "step.queued", sd.name)
                await self._enqueue_step(s, run.id, sd.name, 1)
        await s.flush()
        statuses = [latest[n].status if n in latest else None for n in wf.steps]
        if all(x == "succeeded" for x in statuses):
            run.status, run.finished_at = "succeeded", _now()
            run.output = {n: latest[n].output for n in wf.leaves()}
            await trace.emit(s, run.id, "run.succeeded", spent_usd=str(run.spent_usd))
        elif any(x in ("queued", "running") for x in statuses):
            run.status = "running"
        else:
            run.status = "waiting"  # human input needed, or paused by the kill switch

    async def _fail_run(self, s: AsyncSession, run: Run, error: str) -> None:
        run.status, run.finished_at, run.error = "failed", _now(), error[:2000]
        await trace.emit(s, run.id, "run.failed", error=error[:500])

    async def _handle(self, jc: JobContext, payload: dict[str, Any]) -> None:
        s = jc.session
        run_id, name, attempt = (
            uuid.UUID(payload["run_id"]),
            payload["step"],
            int(payload["attempt"]),
        )
        run = await s.get(Run, run_id)
        step = (
            await s.execute(
                select(Step).where(
                    Step.run_id == run_id, Step.name == name, Step.attempt == attempt
                )
            )
        ).scalar_one_or_none()
        if run is None or step is None or run.status in TERMINAL:
            return  # cancelled or finished while this job waited
        if step.status in ("succeeded", "failed"):
            run = await self._lock(
                s, run_id
            )  # a repeated delivery: only make sure the next steps are queued
            await self._schedule(s, run)
            return
        try:
            wf = self._workflow(run)
        except (LookupError, RuntimeError) as e:
            run = await self._lock(s, run_id)
            step.status, step.error, step.finished_at = "failed", str(e)[:1000], _now()
            await self._fail_run(s, run, str(e))
            return
        sd = wf.steps[name]
        latest = await self._latest_all(s, run_id)
        outputs = {n: r.output for n, r in latest.items() if r.status == "succeeded"}
        raw = bind_input(sd, run.input, outputs)
        step.status, step.started_at, step.input = "running", _now(), raw
        step.model, step.prompt_key = sd.model, sd.prompt
        ctx = StepContext(
            run_id=run_id,
            org_id=run.org_id,
            step=name,
            attempt=attempt,
            session=s,
            runner=self,
            budget=Decimal(str(run.budget_usd)) if run.budget_usd is not None else None,
            spent_before=Decimal(str(run.spent_usd)),
            prompt_key=sd.prompt,
            model=sd.model,
        )
        await trace.emit(s, run_id, "step.started", name, attempt=attempt)
        error: Exception | None = None
        output: Any = None
        try:
            output = to_json(await sd.fn(ctx, coerce_input(sd.fn, raw)))
        except Exception as e:
            error = e
        await self._finish(s, run_id, step, sd, ctx, output, error)

    async def _finish(
        self,
        s: AsyncSession,
        run_id: uuid.UUID,
        step: Step,
        sd: StepDef,
        ctx: StepContext,
        output: Any,
        error: Exception | None,
    ) -> None:
        step.tokens_in, step.tokens_out, step.cost_usd = ctx.tokens_in, ctx.tokens_out, ctx.cost
        step.model = ctx._models[-1] if ctx._models else step.model
        step.prompt_key, step.prompt_version = ctx.prompt_key, ctx.prompt_version
        step.finished_at = _now()
        run = await self._lock(
            s, run_id
        )  # short critical section: the model calls are already done
        run.spent_usd = Decimal(str(run.spent_usd)) + ctx.cost
        if error is None:
            step.status, step.output = "succeeded", output
            await trace.emit(s, run_id, "step.succeeded", step.name, cost_usd=str(ctx.cost))
            await self._schedule(s, run)
            return
        step.status, step.error = "failed", f"{type(error).__name__}: {error}"[:1000]
        await trace.emit(
            s, run_id, "step.failed", step.name, attempt=step.attempt, error=step.error
        )
        if run.status in TERMINAL:
            return
        if isinstance(error, (BudgetExceeded, UnpricedModel)):
            await self._fail_run(
                s,
                run,
                f"budget: {error}" if isinstance(error, BudgetExceeded) else f"unpriced: {error}",
            )
        elif step.attempt <= sd.retries:
            nxt = step.attempt + 1
            s.add(Step(run_id=run_id, name=step.name, attempt=nxt, status="queued"))
            await s.flush()
            await trace.emit(s, run_id, "step.retry", step.name, attempt=nxt)
            await self._enqueue_step(
                s, run_id, step.name, nxt, _now() + self.retry_delay(step.attempt)
            )
        else:
            await self._fail_run(s, run, f"step {step.name}: {step.error}")
