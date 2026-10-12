# K3 AI workflows (`tsp.pipeline` + `tsp.ai` additions)

**Goal:** durable multi-step AI workflows on `tsp.jobs` with checkpoints, retries, per-step model choice, run budgets, traces and costs; a versioned prompt registry; an eval harness; and the Anthropic features the workflows need (prompt caching, Message Batches, citations, structured outputs) plus a glossary-aware translation helper.

**Depends on:** phase 8; K2 for graph-backed steps. **Branch:** `feat/k3-<step>`. **Size:** large; three pull requests.

## K3-A `tsp.ai` additions
Files: `tsp/ai/{models.py,client.py,providers/anthropic.py,batches.py (new),structured.py (new),translate.py (new)}`.
- `chat.create(..., cache=[...])`: mark system blocks or messages as cacheable (Anthropic `cache_control: {"type": "ephemeral"}`); usage reports `cache_read_tokens`, `cache_write_tokens`.
- `chat.create(..., response_schema=PydanticModel)`: structured output (Anthropic tool-forcing or native JSON schema); returns a validated model; one repair retry on validation error.
- `chat.create(..., documents=[Doc(id, text, title)], citations=True)`: Anthropic citations; response carries `citations: [{doc_id, start, end, quoted_text}]`.
- `batches.submit(requests) -> batch_id`, `batches.poll(batch_id)`, `batches.results(batch_id)`; a job `ai.batch.collect` polls and stores results. Metered at batch price.
- `translate(text, target_lang, glossary: dict[str,str], model) -> str`: glossary terms enforced and checked after translation (missing term → retry once, then flag).
Tests with recorded payloads.

## K3-B `tsp.pipeline` engine
Files: `python/packages/pipeline/` with `src/tsp/pipeline/{__init__,models,workflow,runner,budget,prompts,trace,jobs,api,admin,cli}.py`, `migrations/versions/pipeline_0001_init.py`.

Tables:
| Table | Columns |
|---|---|
| `pl_runs` | id, org_id, workflow, version, input jsonb, status (`queued`/`running`/`waiting`/`succeeded`/`failed`/`cancelled`), budget_usd, spent_usd, started_at, finished_at, error, parent_run_id |
| `pl_steps` | id, run_id, name, attempt, status, input jsonb, output jsonb, model, prompt_key, prompt_version, tokens_in, tokens_out, cost_usd, started_at, finished_at, error; unique (run_id, name, attempt) |
| `pl_prompts` | key, version (int), body text, model_hint, params jsonb, created_by, created_at, notes; pk (key, version); `active` version per key in `pl_prompt_active` |
| `pl_events` | run_id, at, kind, data jsonb (trace) |

Interfaces:
```python
wf = Workflow("summarise_reviews", version=1)

@wf.step("collect")
async def collect(ctx: StepContext, inp: In) -> Collected: ...

@wf.step("draft", after=["collect"], model="claude-sonnet-5-5", prompt="reviews.draft", retries=2)
async def draft(ctx, collected: Collected) -> Draft:
    return await ctx.llm(response_schema=Draft, variables={...})   # loads active prompt version, records cost

@wf.step("approve", after=["draft"], kind="human")   # run goes to `waiting` until resume(run_id, "approve", data)
...

runner.start(org_id, wf, input, budget_usd=2.0) -> run_id
runner.resume(run_id, step=None, data=None); runner.cancel(run_id)
```
Kill switch: a `tsp.flags` flag `pipeline` (same mechanism as the existing `ai` kill switch, `flags.killed(session, "pipeline")`) stops `runner.start` from starting new runs; running ones finish their current step and wait.

Rules: each step is a `tsp.jobs` job `pipeline.step` with `unique_key=f"{run_id}:{step}:{attempt}"`; completed step outputs are checkpoints, never recomputed on resume; budget check before every LLM call (`BudgetExceeded` fails the run cleanly); steps can fan out (`ctx.map(items, substep)`), and can call `ctx.batch(...)` to use Message Batches and wait.

Prompt registry: `tsp-pipeline prompts push <dir>` loads `prompts/*.md` with front matter (`key`, `model_hint`, `params`) and creates a new version only when the body changed; `activate key version`.

Admin: runs list (status, cost, duration), run detail with step trace, prompt versions. API: `GET /api/pipeline/runs`, `GET /api/pipeline/runs/{id}`, `POST /api/pipeline/runs/{id}/resume`.

## K3-C Eval harness
- `evals/<suite>/cases.jsonl` (input, expected or rubric), `tsp-pipeline eval <suite> --prompt key@version` runs each case through a step or workflow, scores with exact match, a Python scorer, or an LLM judge with a rubric file, stores `pl_eval_runs` (suite, prompt version, score, per-case results), and compares against the active version.
- CI can run a suite on cheap models; failure threshold configurable.

## Tests
- Kill the worker after step 2 of a 3-step workflow → resume completes step 3 only.
- Budget of $0.01 stops the run before the expensive step with status `failed: budget`.
- Human step waits and resumes with data.
- With the `pipeline` flag killed, `start` refuses new runs.
- Prompt push is idempotent; a changed body creates version 2.
- Eval suite produces scores per version.

## Done when
A sample three-step workflow resumes after a crash, stays under its budget, and shows its trace and cost in admin; `docs/modules/pipeline.md` written.

## Prompt
```
Repository: Teak-Rosewood/TheStartupProject. Branch: feat/k3-X (X = A, B, C, in order).
Read docs/plans/K3-pipeline.md section K3-X, docs/plans/README.md, tsp.ai (client, anthropic
provider) and tsp.jobs. Implement the section. Sample workflow: summarising product reviews.
Recorded fixtures only. Run lint, types and tests; open a PR.
```
