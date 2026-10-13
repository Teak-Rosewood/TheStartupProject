"""Moderation of submissions: ordered plug-ins, run as a ``tsp.pipeline`` workflow (``engage.moderate``).

Each moderator returns a :class:`Verdict`. Any ``fail`` rejects the submission with the reasons
(shown to its author); any ``flag`` leaves it pending for a person; otherwise it is accepted. Later
moderators are skipped once one has failed, so the cheap checks go first and a model is not paid for
a submission that is already rejected."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.pipeline import PromptRegistry, StepContext, Workflow

from .config import EngageConfig
from .models import Submission
from .points import Points

PROMPTS_DIR = Path(__file__).parent / "prompts"
WORKFLOW = "engage.moderate"
Status = Literal["pass", "fail", "flag"]


@dataclass(frozen=True)
class Verdict:
    status: Status
    reason: str = ""  # written for the author
    score: float | None = None
    moderator: str = ""


@dataclass(frozen=True)
class Draft:
    """What moderators see: the take and where it was made."""

    id: str
    org_id: str
    item_id: str
    user_id: str
    side: str
    body: str
    item_title: str = ""


class Moderator(Protocol):
    name: str

    async def run(self, sub: Draft, ctx: StepContext) -> Verdict: ...


class Length:
    name = "length"

    def __init__(self, min_chars: int = 20, max_chars: int = 1500):
        self.min_chars, self.max_chars = min_chars, max_chars

    async def run(self, sub: Draft, ctx: StepContext) -> Verdict:
        n = len(sub.body.strip())
        if n < self.min_chars:
            return Verdict(
                "fail",
                f"Say a little more: at least {self.min_chars} characters.",
                moderator=self.name,
            )
        if n > self.max_chars:
            return Verdict(
                "fail",
                f"Please shorten it to {self.max_chars} characters or fewer.",
                moderator=self.name,
            )
        return Verdict("pass", moderator=self.name)


class Blocklist:
    """Whole-word match, case-insensitive, any script. The reason does not repeat the word."""

    name = "blocklist"

    def __init__(self, words: Sequence[str]):
        parts = [re.escape(w.strip()) for w in words if w.strip()]
        self._re = (
            re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE) if parts else None
        )

    async def run(self, sub: Draft, ctx: StepContext) -> Verdict:
        if self._re and self._re.search(sub.body):
            return Verdict(
                "fail",
                "It contains language we do not allow. Please rephrase.",
                moderator=self.name,
            )
        return Verdict("pass", moderator=self.name)


class Rubric(BaseModel):
    score: float = Field(ge=0, le=1)
    reason: str = ""


class LlmRubric:
    """One dimension (civility, relevance, substance) scored 0 to 1 by a model with a registered
    prompt. Below ``fail_below`` it fails; below ``flag_below`` a person looks at it."""

    def __init__(
        self,
        dimension: str,
        prompt_key: str | None = None,
        *,
        model: str | None = None,
        fail_below: float = 0.4,
        flag_below: float = 0.6,
        enforce: bool = True,
    ):
        self.name = dimension
        self.prompt_key, self.model = prompt_key or f"engage.{dimension}", model
        self.fail_below, self.flag_below, self.enforce = fail_below, flag_below, enforce

    async def run(self, sub: Draft, ctx: StepContext) -> Verdict:
        r = await ctx.llm(
            {"body": sub.body, "title": sub.item_title, "side": sub.side},
            prompt=self.prompt_key,
            model=self.model,
            response_schema=Rubric,
        )
        if not self.enforce:  # measured and stored (for example to pay a bonus), never blocks
            return Verdict("pass", r.reason, r.score, self.name)
        if r.score < self.fail_below:
            return Verdict(
                "fail", r.reason or f"It did not meet our {self.name} standard.", r.score, self.name
            )
        if r.score < self.flag_below:
            return Verdict("flag", r.reason, r.score, self.name)
        return Verdict("pass", score=r.score, moderator=self.name)


class FactCheckHook:
    """Calls your function. Return a Verdict to fail or flag, or None to pass."""

    name = "fact_check"

    def __init__(self, fn: Callable[[Draft], Awaitable[Verdict | None]]):
        self.fn = fn

    async def run(self, sub: Draft, ctx: StepContext) -> Verdict:
        v = await self.fn(sub)
        return (
            Verdict("pass", moderator=self.name)
            if v is None
            else Verdict(v.status, v.reason, v.score, self.name)
        )


def default_moderators(blocklist: Sequence[str] = ()) -> list[Moderator]:
    """Cheap checks first, then the models. Substance is measured for the evidence bonus but does not block."""
    return [
        Length(),
        Blocklist(blocklist),
        LlmRubric("civility"),
        LlmRubric("relevance"),
        LlmRubric("substance", enforce=False),
    ]


async def push_prompts(session: AsyncSession) -> dict[str, tuple[int, bool]]:
    """Register the bundled rubric prompts (a new version only when the text changed)."""
    return await PromptRegistry().push_dir(session, PROMPTS_DIR)


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(UTC)


def build_workflow(
    moderators: Sequence[Moderator],
    cfg: EngageConfig | None = None,
    *,
    name: str = WORKFLOW,
    version: int = 1,
) -> Workflow:
    """The moderation workflow: one step per moderator, in order, then ``decide``. Register it with
    your ``Runner`` and start it with ``{"draft": {...}, "body_hash": "..."}`` (``Submissions`` does)."""
    cfg = cfg or EngageConfig()
    wf = Workflow(name, version)
    prev: str | None = None
    seen: set[str] = set()
    for m in moderators:
        if m.name in seen:
            raise ValueError(f"two moderators are called {m.name!r}")
        seen.add(m.name)

        def make(
            mod: Moderator,
        ) -> Callable[[StepContext, dict[str, Any]], Awaitable[dict[str, Any]]]:
            async def step(ctx: StepContext, inp: dict[str, Any]) -> dict[str, Any]:
                verdicts = list(inp.get("verdicts", []))
                if any(v["status"] == "fail" for v in verdicts):
                    return {**inp, "verdicts": verdicts}  # already rejected: spend nothing more
                v = await mod.run(Draft(**inp["draft"]), ctx)
                return {**inp, "verdicts": [*verdicts, asdict(v)]}

            return step

        wf.step(f"mod_{m.name}", after=[prev] if prev else [])(make(m))
        prev = f"mod_{m.name}"

    @wf.step("decide", after=[prev] if prev else [])
    async def decide(ctx: StepContext, inp: dict[str, Any]) -> dict[str, Any]:
        sub = await ctx.session.get(Submission, uuid.UUID(inp["draft"]["id"]))
        verdicts = inp.get("verdicts", [])
        if sub is None or sub.status != "pending" or body_hash(sub.body) != inp["body_hash"]:
            return {"decision": "stale"}  # edited or already decided since this run began
        scores = {v["moderator"]: v["score"] for v in verdicts if v.get("score") is not None}
        fails = [v["reason"] for v in verdicts if v["status"] == "fail"]
        flags = [v["reason"] for v in verdicts if v["status"] == "flag"]
        sub.scores = scores
        if fails:
            sub.status, sub.reasons, sub.needs_review, sub.decided_at = (
                "rejected",
                fails,
                False,
                _now(),
            )
            return {"decision": "rejected"}
        if flags:
            sub.reasons, sub.needs_review = flags, True  # stays pending: a person decides
            return {"decision": "review"}
        sub.status, sub.reasons, sub.needs_review, sub.decided_at = "accepted", [], False, _now()
        sub.points_awarded = await award_for_acceptance(ctx.session, sub, cfg)
        return {"decision": "accepted", "points": sub.points_awarded}

    wf.validate()
    return wf


async def award_for_acceptance(session: AsyncSession, sub: Submission, cfg: EngageConfig) -> int:
    pts = Points(session, cfg)
    total = await pts.award(
        sub.user_id, sub.org_id, "submission_accepted", ref_kind="submission", ref_id=str(sub.id)
    )
    if (sub.scores or {}).get("substance", 0) >= cfg.evidence_threshold:
        total += await pts.award(
            sub.user_id,
            sub.org_id,
            "submission_evidence",
            ref_kind="submission",
            ref_id=str(sub.id),
        )
    return total or sub.points_awarded
