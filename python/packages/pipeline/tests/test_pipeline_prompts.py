import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipeline_support import URL
from tsp.db import Database
from tsp.pipeline import PromptRegistry, Workflow, build_router, parse_prompt_file
from tsp.pipeline.cli import main

BODY = "Summarise these reviews of {{product}}:\n{{reviews}}"


async def test_push_is_idempotent_and_a_changed_body_makes_version_two(db):
    reg = PromptRegistry()
    async with db.session() as s:
        assert await reg.push(s, "reviews.draft", BODY, model_hint="m-cheap") == (1, True)
        assert await reg.push(s, "reviews.draft", BODY, model_hint="m-cheap") == (1, False)
        assert await reg.push(s, "reviews.draft", BODY + "\nBe brief.", model_hint="m-cheap") == (
            2,
            True,
        )
        assert (
            await reg.get(s, "reviews.draft")
        ).version == 1  # new versions are not active until activated
        await reg.activate(s, "reviews.draft", 2)
        got = await reg.get(s, "reviews.draft")
        assert got.version == 2 and (await reg.get(s, "reviews.draft", 1)).body == BODY
        with pytest.raises(LookupError):
            await reg.activate(s, "reviews.draft", 9)
        with pytest.raises(LookupError):
            await reg.get(s, "nope")


async def test_render_fills_variables_and_rejects_missing_ones(db):
    reg = PromptRegistry()
    async with db.session() as s:
        await reg.push(s, "p", BODY)
        p = await reg.get(s, "p")
    assert (
        p.render({"product": "Widget", "reviews": "- good"})
        == "Summarise these reviews of Widget:\n- good"
    )
    with pytest.raises(KeyError, match="reviews"):
        p.render({"product": "Widget"})


def test_prompt_files_have_front_matter():
    meta = parse_prompt_file(
        '---\nkey: a.b\nmodel_hint: m-cheap\nparams: {"temperature": 0.2}\n---\nHello {{x}}\n'
    )
    assert (
        meta["key"] == "a.b"
        and meta["params"] == {"temperature": 0.2}
        and meta["body"] == "Hello {{x}}"
    )
    with pytest.raises(ValueError):
        parse_prompt_file("no front matter")
    with pytest.raises(ValueError):
        parse_prompt_file("---\nmodel_hint: x\n---\nbody")


async def test_workflow_uses_the_active_prompt_version_and_records_it(db, make):
    wf = Workflow("prompted")

    @wf.step("draft", prompt="reviews.draft")
    async def draft(ctx, inp):
        return await ctx.llm({"product": inp["product"], "reviews": "- fine"})

    h = make([wf])
    async with db.session() as s:
        await h.runner.prompts.push(
            s, "reviews.draft", BODY, model_hint="m-cheap", params={"temperature": 0.1}
        )
    run_id = await h.runner.start("org", wf, {"product": "Widget"})
    await h.drain()
    model, messages, params = h.llm.calls[0]
    assert model == "m-cheap" and params == {"temperature": 0.1}
    assert messages == [{"role": "user", "content": "Summarise these reviews of Widget:\n- fine"}]
    from test_pipeline_runs import steps_of

    row = (await steps_of(db, run_id))[("draft", 1)]
    assert (row.prompt_key, row.prompt_version) == ("reviews.draft", 1)


def test_cli_push_and_activate(db, tmp_path, capsys):

    (tmp_path / "p.md").write_text("---\nkey: cli.demo\nmodel_hint: m\n---\nHello {{name}}")
    argv = ["--database-url", URL, "prompts"]
    assert main([*argv, "push", str(tmp_path)]) == 0
    assert "cli.demo@1 created" in capsys.readouterr().out
    assert main([*argv, "push", str(tmp_path)]) == 0
    assert "cli.demo@1 unchanged" in capsys.readouterr().out
    assert main([*argv, "activate", "cli.demo", "5"]) == 1
    assert "error:" in capsys.readouterr().err


async def test_api_lists_shows_trace_and_resumes_only_own_runs(db, make):
    wf = Workflow("api")

    @wf.step("draft")
    async def draft(ctx, inp):
        return {"t": 1}

    @wf.step("approve", after=["draft"], kind="human")
    async def approve(ctx, inp): ...

    h = make([wf])
    run_id = await h.runner.start("org-1", wf, {})
    await h.runner.start("org-2", wf, {})
    await h.drain()

    async def sess():
        d = Database(URL)
        try:
            async with d.session() as s:
                yield s
        finally:
            await d.dispose()

    app = FastAPI()
    app.state.org = "org-1"
    app.include_router(build_router(h.runner, lambda: "org-1", sess), prefix="/api")
    c = TestClient(app)
    listed = c.get("/api/pipeline/runs").json()
    assert [r["id"] for r in listed] == [str(run_id)] and listed[0]["status"] == "waiting"
    detail = c.get(f"/api/pipeline/runs/{run_id}").json()
    assert [s["name"] for s in detail["steps"]] == ["draft", "approve"]
    assert "step.waiting" in [e["kind"] for e in detail["events"]]
    assert c.get("/api/pipeline/runs", params={"status": "failed"}).json() == []
    bad = c.post(f"/api/pipeline/runs/{run_id}/resume", json={"step": "draft"})
    assert bad.status_code == 409
    ok = c.post(f"/api/pipeline/runs/{run_id}/resume", json={"step": "approve", "data": {"ok": 1}})
    assert ok.status_code == 200
