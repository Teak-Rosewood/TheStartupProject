"""Shared fake provider and client builder for tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from tsp.ai import LLMClient, ModelCandidate
from tsp.ai.providers.base import BaseProvider, ProviderHttpError


class StubProvider(BaseProvider):
    """Answers every chat with ``"pong"``; raises ``error`` instead when set."""

    def __init__(self, platform: str = "stub", error: ProviderHttpError | None = None):
        self._platform = platform
        self.error = error
        self.closed = False

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def name(self) -> str:
        return "Stub"

    async def chat_completion(self, api_key, messages, model_id, options=None) -> dict[str, Any]:
        if self.error:
            raise self.error
        return {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def stream_chat_completion(
        self, api_key, messages, model_id, options=None
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self.error:
            raise self.error
        for part in ("po", "ng"):
            yield {"choices": [{"delta": {"content": part}, "finish_reason": None}]}

    async def validate_key(self, api_key: str) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def stub_client(provider: StubProvider | None = None, model: str = "stub-1") -> LLMClient:
    provider = provider or StubProvider()
    client = LLMClient()
    client._router.add_provider(
        provider.platform,
        provider,
        [ModelCandidate(platform=provider.platform, model_id=model, display_name=model)],
    )
    client._router.add_key(provider.platform, "k")
    return client


class EmbeddingStub(StubProvider):
    """Embeds each text as ``[len(text), index-in-call, 0...]`` padded to ``dims``; records calls."""

    def __init__(
        self, platform: str = "emb", dims: int = 4, error: ProviderHttpError | None = None
    ):
        super().__init__(platform, error)
        self.dims = dims
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []

    async def embedding(self, api_key, input, model_id, options=None) -> dict[str, Any]:
        self.calls.append((api_key, list(input), dict(options or {})))
        if self.error:
            raise self.error
        vectors = [[float(len(t))] + [0.0] * (self.dims - 1) for t in input]
        # Providers may return items out of order; clients must restore input order via ``index``.
        data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)][::-1]
        return {"data": data, "usage": {"prompt_tokens": len(input), "total_tokens": len(input)}}


def embedding_client(
    *providers: EmbeddingStub, model: str = "embed-1", dims: int | None = 4
) -> LLMClient:
    client = LLMClient()
    for p in providers:
        client._router.add_provider(p.platform, p)
        client._router.add_key(p.platform, f"key-{p.platform}")
        client._router.set_embedding_models(p.platform, {model: dims})
    return client
