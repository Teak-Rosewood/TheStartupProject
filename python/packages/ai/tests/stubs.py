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
