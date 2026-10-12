"""Anthropic Message Batches: many requests, asynchronous, at about half the price.

``client.batches.submit([...])`` returns a batch id; ``poll`` says when it has ended; ``results``
returns one entry per request. A batch can take up to 24 hours, so store the id and collect later
(``tsp_api.ai_batches`` does that on a schedule). Batch token usage should be priced at
:data:`BATCH_DISCOUNT` of the normal rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .models import ChatCompletionResponse, Choice, ChoiceMessage, Usage
from .providers.anthropic import AnthropicProvider
from .providers.base import ProviderHttpError

if TYPE_CHECKING:
    from .client import LLMClient

BATCH_DISCOUNT = 0.5  # batch requests are billed at half the normal token price


@dataclass(frozen=True)
class BatchRequest:
    custom_id: str  # your key for matching results to requests; unique within a batch
    model: str
    messages: list[dict[str, Any]]
    options: dict[str, Any] = field(
        default_factory=dict
    )  # max_tokens, temperature, cache, documents...


@dataclass(frozen=True)
class BatchStatus:
    id: str
    status: Literal["in_progress", "canceling", "ended"]
    counts: dict[str, int]

    @property
    def done(self) -> bool:
        return self.status == "ended"


@dataclass(frozen=True)
class BatchResult:
    custom_id: str
    status: Literal["succeeded", "errored", "canceled", "expired"]
    response: ChatCompletionResponse | None = None
    error: str | None = None


def parse_result(line: dict[str, Any], provider: AnthropicProvider) -> BatchResult:
    result = line.get("result", {})
    kind = result.get("type", "errored")
    if kind == "succeeded":
        msg = result["message"]
        data = provider._to_openai_format(msg, msg.get("model", ""))
        usage = data.get("usage") or {}
        resp = ChatCompletionResponse(
            model=msg.get("model", ""),
            routed_via=f"anthropic/{msg.get('model', '')}",
            choices=[
                Choice(
                    message=ChoiceMessage(content=data["choices"][0]["message"]["content"]),
                    finish_reason=data["choices"][0].get("finish_reason"),
                )
            ],
            usage=Usage(**usage),
        )
        return BatchResult(line["custom_id"], "succeeded", resp)
    err = (result.get("error") or {}).get("error", result.get("error")) or {}
    message = err.get("message") if isinstance(err, dict) else str(err)
    return BatchResult(
        line["custom_id"], kind if kind in ("canceled", "expired") else "errored", None, message
    )


class BatchesNamespace:
    """``client.batches``: uses the first usable Anthropic key registered on the client."""

    def __init__(self, client: LLMClient):
        self._client = client

    def _target(self) -> tuple[AnthropicProvider, str]:
        router = self._client._router
        provider = router._providers.get("anthropic")
        keys = [k for k in router._keys.get("anthropic", []) if k.enabled]
        if not isinstance(provider, AnthropicProvider) or not keys:
            raise ProviderHttpError("batches need an Anthropic provider with a key", status=404)
        return provider, keys[0].api_key

    async def submit(self, requests: list[BatchRequest]) -> str:
        if not requests:
            raise ValueError("a batch needs at least one request")
        ids = [r.custom_id for r in requests]
        if len(set(ids)) != len(ids):
            raise ValueError("custom_id must be unique within a batch")
        provider, key = self._target()
        out = await provider.batch_submit(
            key, [(r.custom_id, r.model, r.messages, dict(r.options)) for r in requests]
        )
        return str(out["id"])

    async def poll(self, batch_id: str) -> BatchStatus:
        provider, key = self._target()
        data = await provider.batch_status(key, batch_id)
        return BatchStatus(
            batch_id,
            "ended"
            if data.get("processing_status") == "ended"
            else data.get("processing_status", "in_progress"),
            dict(data.get("request_counts") or {}),
        )

    async def results(self, batch_id: str) -> list[BatchResult]:
        """Results of an ended batch (raises if it is still running)."""
        status = await self.poll(batch_id)
        if not status.done:
            raise ProviderHttpError(f"batch {batch_id} has not ended ({status.status})", status=409)
        provider, key = self._target()
        return [
            parse_result(line, provider) for line in await provider.batch_results(key, batch_id)
        ]
