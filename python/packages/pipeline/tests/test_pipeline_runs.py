import asyncio
from datetime import timedelta

import pytest
from pipeline_support import FakeLLM
from pydantic import BaseModel
from sqlalchemy import select, text
from tsp.audit import Actor
from tsp.flags import FlagUpdate
from tsp.pipeline import PipelineDisabled, Run, Step, Workflow
from tsp.pipeline.models import Event

ORG = "org-1"


async def run_of(db, run_id):
    async with db.session() as s:
        return await s.get(Run, run_id)


async def steps_of(db, run_id):
    async with db.session() as s:
        rows = (
            await s.execute(
                select(Step).where(Step.run_id == run_id).order_by(Step.started_at, Step.name)
            )
        ).scalars()
        return {(r.name, r.attempt): r for r in rows}


async def events_of(db, run_id):
    async with db.session() as s:
        return [
            e.kind
            for e in (
                await s.execute(select(Event).where(Event.run_id == run_id).order_by(Event.id))
            ).scalars()
        ]


def three_step(calls):
    wf = Workflow("reviews", version=1)

    @wf.step("collect")
    async def collect(ctx, inp):
        calls.append("collect")
        return {"reviews": inp["reviews"]}

    @wf.step("draft", after=["collect"], model="m-cheap")
    async def draft(ctx, got):
        calls.append("draft")
        return {
            "summary": await ctx.llm(
                messages=[{"role": "user", "content": " ".join(got["reviews"])}]
            )
        }

    @wf.step("publish", after=["draft"])
    async def publish(ctx, d):
        calls.append("publish")
        return {"published": d["summary"]}

    return wf


async def test_a_run_completes_and_records_cost_and_trace(db, make):
    calls = []
    h = make([three_step(calls)], llm=FakeLLM(["A fine summary"]))
    run_id = await h.runner.start(
        ORG, h.runner.workflows["reviews"], {"reviews": ["good", "bad"]}, budget_usd=1.0
    )
    await h.drain()
    run = await run_of(db, run_id)
    assert run.status == "succeeded" and run.output == {"publish": {"published": "A fine summary"}}
    assert calls == ["collect", "draft", "publish"]
    st = await steps_of(db, run_id)
    assert st[("draft", 1)].tokens_in == 1000 and st[("draft", 1)].model == "m-cheap"
    assert float(st[("draft", 1)].cost_usd) == pytest.approx(
        0.015
    )  # 1500 tokens at $10 per million
    assert float(run.spent_usd) == pytest.approx(0.015) and float(st[("collect", 1)].cost_usd) == 0
    kinds = await events_of(db, run_id)
    assert kinds[0] == "run.started" and kinds[-1] == "run.succeeded" and "llm.call" in kinds


async def test_killed_worker_resumes_from_the_unfinished_step_only(db, make):
    calls = []
    h1 = make([three_step(calls)], lease=timedelta(0))
    run_id = await h1.runner.start(ORG, h1.runner.workflows["reviews"], {"reviews": ["x"]})
    await h1.worker.run_once()  # collect
    await h1.worker.run_once()  # draft
    assert calls == ["collect", "draft"]

    # The process dies in the middle of step 3: the job stays claimed and its transaction rolls back.
    wf = h1.runner.workflows["reviews"]
    real = wf.steps["publish"].fn

    async def dies(ctx, inp):
        calls.append("publish (crashed)")
        raise asyncio.CancelledError

    object.__setattr__(wf.steps["publish"], "fn", dies)
    with pytest.raises(asyncio.CancelledError):
        await h1.worker.run_once()
    object.__setattr__(wf.steps["publish"], "fn", real)
    assert (await run_of(db, run_id)).status == "running"

    # A new process picks the job up again once its lease has expired.
    calls.clear()
    h2 = make([three_step(calls)], lease=timedelta(0))
    await h2.drain()
    assert calls == ["publish"]  # steps 1 and 2 are checkpoints and did not run again
    assert (await run_of(db, run_id)).status == "succeeded"


async def test_budget_stops_the_run_before_the_expensive_step(db, make):
    wf = Workflow("budgeted")

    @wf.step("cheap", model="m-cheap")
    async def cheap(ctx, inp):
        return await ctx.llm(messages=[{"role": "user", "content": "hi"}])

    @wf.step("expensive", after=["cheap"], model="m-big")
    async def expensive(ctx, inp):
        return await ctx.llm(messages=[{"role": "user", "content": "think hard"}], max_tokens=4000)

    llm = FakeLLM(tin=100, tout=100)
    h = make([wf], llm=llm)
    run_id = await h.runner.start(ORG, wf, {}, budget_usd=0.01)
    await h.drain()
    run = await run_of(db, run_id)
    assert run.status == "failed" and run.error.startswith("budget:")
    assert [c[0] for c in llm.calls] == ["m-cheap"]  # the big model was never called
    assert float(run.spent_usd) == pytest.approx(0.002)  # 200 tokens at $10 per million


async def test_a_budgeted_run_refuses_unpriced_models(db, make):
    wf = Workflow("unpriced")

    @wf.step("go", model="mystery")
    async def go(ctx, inp):
        return await ctx.llm(messages=[{"role": "user", "content": "hi"}])

    h = make([wf])
    run_id = await h.runner.start(ORG, wf, {}, budget_usd=1)
    await h.drain()
    run = await run_of(db, run_id)
    assert run.status == "failed" and "unpriced" in run.error and h.llm.calls == []


async def test_a_human_step_waits_and_resumes_with_data(db, make):
    wf = Workflow("approval")

    @wf.step("draft")
    async def draft(ctx, inp):
        return {"text": "v1"}

    @wf.step("approve", after=["draft"], kind="human")
    async def approve(ctx, inp): ...

    @wf.step("publish", after=["approve"])
    async def publish(ctx, decision):
        return {"decision": decision}

    h = make([wf])
    run_id = await h.runner.start(ORG, wf, {})
    await h.drain()
    assert (await run_of(db, run_id)).status == "waiting"
    with pytest.raises(ValueError):
        await h.runner.resume(run_id, "draft", {})  # not a waiting step
    await h.runner.resume(run_id, "approve", {"ok": True, "by": "ann"})
    await h.drain()
    run = await run_of(db, run_id)
    assert run.status == "succeeded" and run.output == {
        "publish": {"decision": {"ok": True, "by": "ann"}}
    }


async def test_the_pipeline_kill_switch_refuses_new_runs_and_pauses_running_ones(db, make, flags):
    calls = []
    h = make([three_step(calls)], flags=flags)
    wf = h.runner.workflows["reviews"]
    run_id = await h.runner.start(ORG, wf, {"reviews": ["x"]})
    await h.worker.run_once()  # collect finishes

    async with db.session() as s:
        await flags.set(
            s,
            "kill:pipeline",
            FlagUpdate(enabled=True),
            actor=Actor("staff", "superadmin"),
            reason="incident",
        )
    with pytest.raises(PipelineDisabled):
        await h.runner.start(ORG, wf, {"reviews": []})
    await h.drain()  # the queued step is allowed to finish, but nothing new is queued after it
    assert calls == ["collect", "draft"] and (await run_of(db, run_id)).status == "waiting"

    async with db.session() as s:
        await flags.set(
            s,
            "kill:pipeline",
            FlagUpdate(enabled=False),
            actor=Actor("staff", "superadmin"),
            reason="over",
        )
    await h.runner.resume(run_id)
    await h.drain()
    assert (
        calls == ["collect", "draft", "publish"]
        and (await run_of(db, run_id)).status == "succeeded"
    )


async def test_failed_steps_retry_then_fail_the_run(db, make):
    attempts = {"flaky": 0}
    wf = Workflow("retries")

    @wf.step("flaky", retries=2)
    async def flaky(ctx, inp):
        attempts["flaky"] += 1
        if attempts["flaky"] < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    @wf.step("doomed", after=["flaky"], retries=1)
    async def doomed(ctx, inp):
        raise ValueError("permanent")

    h = make([wf])
    run_id = await h.runner.start(ORG, wf, {})
    await h.drain()
    st = await steps_of(db, run_id)
    assert [(k, v.status) for k, v in st.items() if k[0] == "flaky"] == [
        (("flaky", 1), "failed"), (("flaky", 2), "failed"), (("flaky", 3), "succeeded"),
    ]  # fmt: skip
    assert st[("doomed", 1)].status == st[("doomed", 2)].status == "failed"
    run = await run_of(db, run_id)
    assert run.status == "failed" and "step doomed: ValueError: permanent" in run.error


async def test_parallel_branches_join_and_fan_out_keeps_order(db, make):
    wf = Workflow("diamond")

    class Num(BaseModel):
        n: int

    @wf.step("start")
    async def start(ctx, inp):
        return Num(n=inp["n"])

    @wf.step("double", after=["start"])
    async def double(ctx, got: Num):
        async def one(c, i):
            await asyncio.sleep(0.01 * (3 - i))
            return i * got.n

        return await ctx.map([0, 1, 2], one)

    @wf.step("square", after=["start"])
    async def square(ctx, got: Num):
        return got.n**2

    @wf.step("join", after=["double", "square"])
    async def join(ctx, both):
        return both

    h = make([wf])
    run_id = await h.runner.start(ORG, wf, {"n": 3})
    await h.drain()
    run = await run_of(db, run_id)
    assert run.output == {"join": {"double": [0, 3, 6], "square": 9}}


async def test_structured_output_is_validated_repaired_and_costed(db, make):
    class Summary(BaseModel):
        title: str
        stars: int

    wf = Workflow("structured")

    @wf.step("go", model="m-cheap")
    async def go(ctx, inp):
        return (
            await ctx.llm(
                messages=[{"role": "user", "content": "summarise"}], response_schema=Summary
            )
        ).model_dump()

    llm = FakeLLM(
        ['{"title": "Great"}', '```json\n{"title": "Great", "stars": 5}\n```'], tin=10, tout=10
    )
    h = make([wf], llm=llm)
    run_id = await h.runner.start(ORG, wf, {})
    await h.drain()
    run = await run_of(db, run_id)
    assert run.status == "succeeded" and run.output == {"go": {"title": "Great", "stars": 5}}
    assert (
        len(llm.calls) == 2 and "not valid" in llm.calls[1][1][-1]["content"]
    )  # one repair round trip
    assert (await steps_of(db, run_id))[("go", 1)].tokens_in == 20  # both calls are counted


async def test_cancel_stops_further_steps(db, make):
    calls = []
    h = make([three_step(calls)])
    run_id = await h.runner.start(ORG, h.runner.workflows["reviews"], {"reviews": []})
    await h.worker.run_once()
    await h.runner.cancel(run_id)
    await h.drain()
    assert calls == ["collect"] and (await run_of(db, run_id)).status == "cancelled"


async def test_a_run_from_another_workflow_version_does_not_continue(db, make):
    calls = []
    h = make([three_step(calls)])
    run_id = await h.runner.start(ORG, h.runner.workflows["reviews"], {"reviews": []})
    wf2 = Workflow("reviews", version=2)

    @wf2.step("only")
    async def only(ctx, inp):
        return 1

    await make([wf2]).drain()
    run = await run_of(db, run_id)
    assert run.status == "failed" and "v1" in run.error and "v2" in run.error and calls == []


def test_workflow_validation():
    wf = Workflow("bad")
    with pytest.raises(ValueError, match="no steps"):
        wf.validate()

    @wf.step("a", after=["b"])
    async def a(ctx, i): ...

    @wf.step("b", after=["a"])
    async def b(ctx, i): ...

    with pytest.raises(ValueError, match="cycle"):
        wf.validate()
    with pytest.raises(ValueError, match="already defined"):
        wf.step("a")(a)
    wf2 = Workflow("x")
    wf2.step("only", after=["ghost"])(a)
    with pytest.raises(ValueError, match="unknown step"):
        wf2.validate()


async def test_runs_of_other_organizations_stay_separate(db, make):
    h = make([three_step([])])
    wf = h.runner.workflows["reviews"]
    a = await h.runner.start("org-a", wf, {"reviews": []})
    await h.runner.start("org-b", wf, {"reviews": []})
    async with db.session() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM pl_runs WHERE org_id = 'org-a'"))
        ).scalar_one()
    assert n == 1 and (await run_of(db, a)).org_id == "org-a"
