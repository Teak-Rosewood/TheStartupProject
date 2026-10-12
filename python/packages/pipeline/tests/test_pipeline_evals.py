import asyncio
import json
from types import SimpleNamespace

import pytest
from pipeline_support import URL
from sqlalchemy import select
from tsp.ai.models import ChatCompletionResponse, Choice, ChoiceMessage
from tsp.pipeline import EvalRun, PromptRegistry, load_suite, prompt_target, run_eval
from tsp.pipeline.cli import main

CASES = [
    {"id": "fr", "input": {"word": "dog"}, "expected": "chien"},
    {"id": "es", "input": {"word": "cat"}, "expected": "gato"},
    {"id": "de", "input": {"word": "house"}, "expected": "haus"},
]
ANSWERS = {"dog": "chien", "cat": "gato", "house": "haus"}


class ScriptLLM:
    """Replies by a function of the last message, so each prompt version can behave differently."""

    def __init__(self, fn):
        self.calls = []
        self.chat = SimpleNamespace(create=self.create)
        self.fn = fn

    async def create(self, model, messages, **params):
        self.calls.append((model, messages))
        return ChatCompletionResponse(
            model=model, choices=[Choice(message=ChoiceMessage(content=self.fn(messages)))]
        )


def good_v2(messages):
    text = messages[-1]["content"]
    if "Translate:" in text:  # v1 is sloppy, v2 follows "Answer with one word"
        return (
            ANSWERS[text.split()[-1]]
            if "one word" in text
            else "I think it is " + ANSWERS[text.split()[-1]]
        )
    return '{"score": 0.5, "reason": "ok"}'


def write_suite(tmp_path, cases=CASES, **files):
    d = tmp_path / "evals" / "words"
    d.mkdir(parents=True)
    (d / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in cases))
    for name, text in files.items():
        (d / name).write_text(text)
    return tmp_path / "evals"


async def two_versions(db):
    reg = PromptRegistry()
    async with db.session() as s:
        await reg.push(s, "tr", "Translate: {{word}}", model_hint="m")
        await reg.push(s, "tr", "Answer with one word. Translate: {{word}}", model_hint="m")
    return reg


async def test_scores_each_prompt_version_and_stores_the_runs(db, tmp_path):
    reg = await two_versions(db)
    suite = load_suite(write_suite(tmp_path), "words")
    llm = ScriptLLM(good_v2)
    async with db.session() as s:
        v1 = await run_eval(s, suite, "tr@1", prompt_target(llm), compare=False, registry=reg)
        v2 = await run_eval(s, suite, "tr@2", prompt_target(llm), compare=False, registry=reg)
    assert (v1.score, v2.score, v1.scorer) == (0.0, 1.0, "exact")
    async with db.session() as s:
        rows = (await s.execute(select(EvalRun).order_by(EvalRun.prompt_version))).scalars().all()
    assert [(r.prompt_version, r.score, r.n_cases, r.passed) for r in rows] == [
        (1, 0.0, 3, True),
        (2, 1.0, 3, True),
    ]
    assert rows[0].results[0]["reason"] == "expected 'chien'" and rows[0].results[0][
        "output"
    ].startswith("I think")


async def test_a_worse_candidate_fails_against_the_active_version(db, tmp_path):
    reg = await two_versions(db)  # v1 is active (the first version of a key is)
    suite = load_suite(write_suite(tmp_path), "words")
    llm = ScriptLLM(
        lambda m: (
            ANSWERS[m[-1]["content"].split()[-1]]
            if m[-1]["content"].startswith("Translate")
            else ""
        )
    )
    # v1 ("Translate: x") answers correctly with this LLM; v2 starts with "Answer", so it answers ""
    async with db.session() as s:
        rep = await run_eval(s, suite, "tr@2", prompt_target(llm), max_drop=0.1, registry=reg)
    assert (rep.score, rep.baseline, rep.baseline_score, rep.passed) == (0.0, "tr@1", 1.0, False)
    assert "below active v1" in rep.reasons[0]
    async with db.session() as s:
        row = (await s.execute(select(EvalRun))).scalar_one()
    assert row.baseline_version == 1 and row.passed is False


async def test_threshold_and_comparing_with_itself(db, tmp_path):
    reg = await two_versions(db)
    suite = load_suite(write_suite(tmp_path), "words")
    llm = ScriptLLM(good_v2)
    async with db.session() as s:
        low = await run_eval(s, suite, "tr@1", prompt_target(llm), threshold=0.5, registry=reg)
        assert (
            not low.passed and low.baseline is None
        )  # v1 is the active one: nothing to compare with
        active = await run_eval(
            s, suite, "tr", prompt_target(llm), registry=reg
        )  # no version: the active one
        assert active.prompt == "tr@1"


async def test_python_scorer_from_the_suite_folder(db, tmp_path):
    reg = await two_versions(db)
    scorer = "def score(case, output):\n    return (0.5 if case.expected in output else 0.0, 'partial credit')\n"
    suite = load_suite(write_suite(tmp_path, **{"scorer.py": scorer}), "words")
    async with db.session() as s:
        rep = await run_eval(
            s, suite, "tr@1", prompt_target(ScriptLLM(good_v2)), compare=False, registry=reg
        )
    assert rep.scorer == "python" and rep.score == 0.5  # "I think it is chien" contains the answer
    bad = write_suite(tmp_path / "x", **{"scorer.py": "def score(case, output):\n    return 7\n"})
    async with db.session() as s:
        rep = await run_eval(
            s,
            load_suite(bad, "words"),
            "tr@1",
            prompt_target(ScriptLLM(good_v2)),
            compare=False,
            registry=reg,
        )
    assert rep.score == 0.0 and "between 0 and 1" in rep.results[0]["error"]


async def test_llm_judge_scores_against_the_rubric(db, tmp_path):
    reg = await two_versions(db)
    cases = [
        {"id": "a", "input": {"word": "dog"}},
        {"id": "b", "input": {"word": "cat"}, "rubric": "must be gato"},
    ]
    suite = load_suite(
        write_suite(tmp_path, cases, **{"rubric.md": "The answer is a correct translation."}),
        "words",
    )
    judged = []

    def fn(messages):
        rubric_msgs = [m["content"] for m in messages if m["content"].startswith("Rubric:")]
        if rubric_msgs:
            judged.append(rubric_msgs[0])
            return '{"score": 0.75, "reason": "close"}'
        return "chien"

    llm = ScriptLLM(fn)
    async with db.session() as s:
        rep = await run_eval(
            s,
            suite,
            "tr@1",
            prompt_target(llm),
            llm=llm,
            judge_model="judge-m",
            compare=False,
            registry=reg,
        )
    assert rep.scorer == "judge" and rep.score == 0.75 and rep.results[0]["reason"] == "close"
    assert (
        "correct translation" in judged[0] and "must be gato" in judged[1]
    )  # the case rubric wins
    assert {c[0] for c in llm.calls} == {"m", "judge-m"}
    async with db.session() as s:
        with pytest.raises(ValueError, match="judge"):
            await run_eval(
                s, suite, "tr@1", prompt_target(llm), compare=False, registry=reg
            )  # no llm given


async def test_a_failing_case_scores_zero_and_does_not_stop_the_run(db, tmp_path):
    reg = await two_versions(db)
    suite = load_suite(write_suite(tmp_path), "words")

    def fn(messages):
        if "house" in messages[-1]["content"]:
            raise RuntimeError("provider down")
        return ANSWERS[messages[-1]["content"].split()[-1]]

    async with db.session() as s:
        rep = await run_eval(
            s, suite, "tr@2", prompt_target(ScriptLLM(fn)), compare=False, registry=reg
        )
    assert rep.score == pytest.approx(2 / 3) and "provider down" in rep.results[2]["error"]
    async with db.session() as s:
        assert (await s.execute(select(EvalRun.n_errors))).scalar_one() == 1


def test_suite_files_are_validated(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_suite(tmp_path, "nope")
    with pytest.raises(ValueError, match="no cases"):
        load_suite(write_suite(tmp_path / "a", []), "words")
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(write_suite(tmp_path / "b", [CASES[0], CASES[0]]), "words")
    with pytest.raises(ValueError, match="needs a JSON object with an 'input'"):
        load_suite(write_suite(tmp_path / "c", [{"expected": "x"}]), "words")


async def test_exact_scoring_needs_expected_values(db, tmp_path):
    reg = await two_versions(db)
    suite = load_suite(write_suite(tmp_path, [{"input": {"word": "dog"}}]), "words")
    async with db.session() as s:
        with pytest.raises(ValueError, match="expected"):
            await run_eval(
                s, suite, "tr@1", prompt_target(ScriptLLM(good_v2)), compare=False, registry=reg
            )


async def test_cli_prints_a_summary_and_exits_nonzero_on_failure(db, tmp_path, capsys):
    await two_versions(db)
    evals = write_suite(tmp_path)
    base = ["--database-url", URL, "eval", "words", "--evals-dir", str(evals), "--model", "m"]
    llm = ScriptLLM(good_v2)
    assert (
        await asyncio.to_thread(main, [*base, "--prompt", "tr@2", "--threshold", "0.9"], llm) == 0
    )
    out = capsys.readouterr().out
    assert "words tr@2 [exact] score 1.000" in out and "active tr@1: 0.000" in out
    assert (
        await asyncio.to_thread(main, [*base, "--prompt", "tr@1", "--threshold", "0.9"], llm) == 1
    )
    out = capsys.readouterr().out
    assert "FAIL: score 0.000 is below the threshold 0.900" in out and "case fr:" in out
