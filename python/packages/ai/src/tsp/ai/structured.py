"""Structured output: ask for JSON, validate it against a pydantic model, repair once.

Provider-agnostic: works on any chat model. ``call`` is whatever sends messages and returns the
response, so callers decide how each attempt is routed, metered and budgeted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .models import ChatCompletionResponse

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class StructuredOutputError(ValueError):
    """The model did not return JSON matching the schema, even after one repair attempt."""


def schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object that matches this JSON Schema, and nothing else "
        "(no prose, no code fences):\n" + json.dumps(schema.model_json_schema())
    )


def parse_json_object(text: str) -> Any:
    text = text.strip()
    if m := _FENCE.match(text):
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")  # tolerate a sentence around the object
        if 0 <= start < end:
            return json.loads(text[start : end + 1])
        raise


def _text(resp: ChatCompletionResponse) -> str:
    return "".join(c.message.content or "" for c in resp.choices)


async def structured(
    call: Callable[[list[dict[str, Any]]], Awaitable[ChatCompletionResponse]],
    messages: list[dict[str, Any]],
    schema: type[T],
) -> tuple[T, list[ChatCompletionResponse]]:
    """Run ``call`` until it yields a valid ``schema`` (at most two calls).

    Returns the parsed model and every response made, so the caller can add up tokens and cost.
    """
    msgs = [*messages, {"role": "system", "content": schema_instruction(schema)}]
    responses: list[ChatCompletionResponse] = []
    error: Exception | None = None
    for attempt in range(2):
        resp = await call(msgs)
        responses.append(resp)
        text = _text(resp)
        try:
            return schema.model_validate(parse_json_object(text)), responses
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            error = e
            if attempt == 0:
                msgs = [
                    *msgs,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"That was not valid: {str(e)[:500]}\nReply again with only the corrected JSON object.",
                    },
                ]
    raise StructuredOutputError(f"model output did not match {schema.__name__}: {error}")
