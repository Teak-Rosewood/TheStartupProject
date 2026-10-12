from __future__ import annotations

import inspect
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

StepFn = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class StepDef:
    name: str
    fn: StepFn
    after: tuple[str, ...]
    model: str | None
    prompt: str | None
    retries: int
    kind: Literal["auto", "human"]


class Workflow:
    """A named, versioned graph of steps. Bump ``version`` when step names or their meaning change:
    runs started under another version refuse to continue instead of mixing old and new steps."""

    def __init__(self, name: str, version: int = 1):
        self.name = name
        self.version = version
        self.steps: dict[str, StepDef] = {}

    def step(
        self,
        name: str,
        *,
        after: list[str] | tuple[str, ...] = (),
        model: str | None = None,
        prompt: str | None = None,
        retries: int = 0,
        kind: Literal["auto", "human"] = "auto",
    ) -> Callable[[StepFn], StepFn]:
        """Register ``async def fn(ctx, input)``. ``input`` is the run input for a first step, the
        previous step's output for one dependency, or ``{step: output}`` for several. A
        ``kind="human"`` step is never run: the run waits until ``Runner.resume`` supplies its output."""
        if kind not in ("auto", "human"):
            raise ValueError("kind must be 'auto' or 'human'")

        def deco(fn: StepFn) -> StepFn:
            if name in self.steps:
                raise ValueError(f"step {name!r} is already defined in workflow {self.name!r}")
            self.steps[name] = StepDef(name, fn, tuple(after), model, prompt, retries, kind)
            return fn

        return deco

    def validate(self) -> None:
        if not self.steps:
            raise ValueError(f"workflow {self.name!r} has no steps")
        for s in self.steps.values():
            for d in s.after:
                if d not in self.steps:
                    raise ValueError(f"step {s.name!r} runs after unknown step {d!r}")
        done: set[str] = set()
        while len(done) < len(self.steps):  # Kahn: a pass that adds nothing means a cycle
            ready = {n for n, s in self.steps.items() if n not in done and set(s.after) <= done}
            if not ready:
                raise ValueError(f"workflow {self.name!r} has a dependency cycle")
            done |= ready

    def leaves(self) -> list[str]:
        used = {d for s in self.steps.values() for d in s.after}
        return [n for n in self.steps if n not in used]


def bind_input(step: StepDef, run_input: Any, outputs: dict[str, Any]) -> Any:
    if not step.after:
        return run_input
    if len(step.after) == 1:
        return outputs[step.after[0]]
    return {d: outputs[d] for d in step.after}


def coerce_input(fn: StepFn, value: Any) -> Any:
    """Validate into the step's annotated pydantic type, if it has one."""
    params = list(inspect.signature(fn).parameters.values())
    if len(params) < 2:
        return value
    try:
        hint = typing.get_type_hints(fn).get(params[1].name)
    except Exception:
        return value
    if isinstance(hint, type) and issubclass(hint, BaseModel):
        return hint.model_validate(value)
    return value


def to_json(value: Any) -> Any:
    """Step outputs are stored as JSON: pydantic models are dumped, anything else must already be JSON-able."""
    import json

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    try:
        return json.loads(json.dumps(value))
    except TypeError as e:
        raise TypeError(f"step output is not JSON-serialisable: {e}") from None
