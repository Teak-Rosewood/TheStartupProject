# AI workflows (`tsp.pipeline`)

Multi-step AI work that survives crashes, stays under a budget and can be inspected afterwards. A workflow is a small graph of steps; each step runs as a `tsp.jobs` job, its output is stored as a checkpoint, and the run's cost and trace are in the database. Examples use summarising product reviews.

Install `tsp-pipeline`, add `tsp.pipeline` to `TSP_MIGRATE_MODULES` (it also needs `tsp.jobs`), create a `Runner`, and register it with your job registry: `runner.register(registry)` adds the `pipeline.step` job.

## Define a workflow

```python
wf = Workflow("summarise_reviews", version=1)

@wf.step("collect")
async def collect(ctx, inp: dict) -> dict: ...

@wf.step("draft", after=["collect"], model="claude-sonnet-5-5", prompt="reviews.draft", retries=2)
async def draft(ctx, collected: Collected) -> Draft:
    return await ctx.llm({"reviews": collected.text}, response_schema=Draft)

@wf.step("approve", after=["draft"], kind="human")      # the run waits here
async def approve(ctx, inp): ...

@wf.step("publish", after=["approve"])
async def publish(ctx, decision): ...
```

- The second argument is the run input for a first step, the previous step's output for one `after` entry, or `{step: output}` for several. A pydantic annotation validates it. Outputs must be JSON (pydantic models are dumped).
- Steps whose dependencies are done run in parallel. `ctx.map(items, fn, concurrency=4)` fans out inside a step; the step is the checkpoint.
- Bump `version` when step names or meaning change. A run started under another version fails with a clear error instead of mixing old and new steps.

## Run it

```python
runner = Runner(db, [wf], llm=client, flags=flags, prices={"claude-sonnet-5-5": Price(3.0, 15.0)})
run_id = await runner.start(org_id, wf, {"product": "Widget"}, budget_usd=2.0)
await runner.resume(run_id, "approve", {"ok": True})     # supply a human step's output
await runner.cancel(run_id)
```

`start` and the others take `session=` to join your transaction. Statuses: `running`, `waiting` (for human input, or paused by the kill switch), `succeeded`, `failed`, `cancelled`.

**Crashes and retries.** A finished step is never run again. If a worker dies mid-step, its job is redelivered after the lease and only that step runs again, so write steps that are safe to repeat (use idempotency keys for outside effects). A step that raises is retried up to `retries` times with backoff (`retry_delay`), each attempt a row in `pl_steps`; after the last one the run fails with the step's error.

**Budgets.** `prices` gives USD per million tokens for each model id; `Price(0, 0)` marks a free model. Before every `ctx.llm` call the run's spend plus the projected cost of the call (estimated input plus `max_tokens`) must fit the budget, otherwise the run fails with `budget: ...` and the model is not called. A budgeted run refuses a model with no price (fail closed). Parallel steps each see the spend committed before they started, so a run can overshoot by what its parallel steps spend together.

**Kill switch.** Switching on the flag `kill:pipeline` makes `start` raise `PipelineDisabled`; running runs finish the steps already queued, then wait. Call `resume(run_id)` after the switch is off.

## Prompts

`ctx.llm(variables, ...)` renders the step's prompt (`{{name}}` placeholders; a missing variable is an error) from the registry's **active** version, sends it as the user message (or the system message when you pass `messages`), and records the key and version on the step. The prompt's `model_hint` and `params` (for example `temperature`) are defaults. The model comes from the call, the step, or the hint, in that order.

```
prompts/reviews.draft.md
---
key: reviews.draft
model_hint: claude-sonnet-5-5
params: {"temperature": 0.2}
---
Summarise these reviews of {{product}}:
{{reviews}}
```

```bash
DATABASE_URL=... tsp-pipeline prompts push prompts/        # new version only if the text, hint or params changed
DATABASE_URL=... tsp-pipeline prompts activate reviews.draft 2
```

The first version of a key becomes active; later ones wait for `activate`, so a push never changes production by itself.

## Structured output

`ctx.llm(..., response_schema=MyModel)` asks for JSON matching the schema, validates it, and if it does not match sends the error back once (two calls at most). Both calls are counted in tokens and cost. The same helper is `tsp.ai.structured.structured`.

## Trace, cost and API

`pl_runs` holds status, budget and spend; `pl_steps` one row per attempt with model, prompt version, tokens, cost and error; `pl_events` the ordered trace (`run.started`, `step.queued`, `llm.call` with tokens and cost, `step.failed`, ...). `build_router(runner, org_of, session)` serves `GET /pipeline/runs`, `GET /pipeline/runs/{id}` (steps and events) and `POST /pipeline/runs/{id}/resume`, limited to the caller's organization. Use an `org_of` that requires an admin role: resuming approves work.

## Evals

An eval suite is a folder `evals/<suite>/` with `cases.jsonl` (one JSON object per line: `input`, and `expected` or `rubric`; `id` optional) and optionally `rubric.md` (instructions for the LLM judge) or `scorer.py` (`def score(case, output) -> float | (float, reason)`, between 0 and 1; it is your own code and runs as you).

```bash
tsp-pipeline eval words --prompt translate.word@2 --model claude-haiku-5-5 --threshold 0.9 --max-drop 0.05
```

Each case renders the prompt with the case `input` as variables and scores the reply: `exact` (equals `expected`), `python` (the suite's `scorer.py`) or `judge` (an LLM grades it against the case's or the suite's rubric, `--judge-model`). The scorer is chosen by what the suite contains unless `--scorer` says otherwise. The mean score and every case's output, score and reason are stored in `pl_eval_runs`. Unless `--no-compare` is given, the **active** prompt version is scored on the same cases too: the run fails (exit code 1) if the mean is under `--threshold`, or more than `--max-drop` below the active version. A case that raises scores 0 and is counted in `n_errors`; it does not stop the run. In CI use a cheap model and a threshold. From Python: `run_eval(session, load_suite("evals", "words"), "key@2", target)`, where `target(case_input, prompt)` can run a whole workflow instead of a single prompt.

## Not built yet

Staff console views of runs and prompts (the API returns the same data), `ctx.batch` (waiting on a Message Batch from inside a step; the batches client exists in `tsp.ai`). The rest of the K3 brief is in `tsp.ai` (see [the AI client](ai.md)).
