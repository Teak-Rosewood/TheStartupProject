"""
Main client module for TheStartupProject LLM Library.

Provides the LLMClient class that serves as the entry point for all
LLM interactions through a unified OpenAI-compatible interface.
"""

import asyncio
import os
import threading
import time
from collections.abc import AsyncGenerator, Coroutine
from pathlib import Path
from typing import Any

from .catalog import ModelCatalog
from .config import load_env_file
from .fallback import run_fallback_loop
from .models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    StreamChoice,
)
from .providers.base import ProviderHttpError
from .providers.registry import PROVIDERS, ProviderSpec, get_spec
from .router import LLMRouter, ModelCandidate, RouteResult


class LLMClient:
    """
    Async client over many LLM providers.

    Usage:
        client = LLMClient.from_env(".env", catalog="catalog.json")
        response = await client.chat.create(
            model="auto",
            messages=[{"role": "user", "content": "Hello!"}],
        )

    Routing needs candidate models: pass ``catalog`` (from ``tsp-ai verify``; only models
    that answered are routable) or per-provider ``"models": ["id", ...]``.
    """

    def __init__(
        self,
        providers: dict[str, dict[str, Any]] | None = None,
        strategy: str = "balanced",
        max_retries: int = 20,
        catalog: ModelCatalog | str | Path | None = None,
    ):
        self._router = LLMRouter(strategy=strategy, max_retries=max_retries)
        self._http_lock = threading.Lock()
        self._abort_signals: dict[int, asyncio.Event] = {}
        if catalog is not None and not isinstance(catalog, ModelCatalog):
            catalog = ModelCatalog.load(catalog)
        self._register_providers(providers or {}, catalog)

    def _register_providers(
        self, provider_config: dict[str, dict[str, Any]], catalog: ModelCatalog | None
    ) -> None:
        for platform, config in provider_config.items():
            spec = PROVIDERS.get(platform)
            if spec is None:
                if not config.get("base_url"):
                    get_spec(platform)  # raises ValueError listing known providers
                spec = ProviderSpec(platform, config.get("name", platform.title()))
            api_key = config.get("api_key") or ("no-key" if spec.keyless else None)
            if not api_key:
                continue

            provider = spec.build(config.get("base_url"), config.get("name"))
            if catalog is not None:
                result = catalog.providers.get(platform)
                models = [
                    ModelCandidate(
                        platform=platform,
                        model_id=m.id,
                        display_name=m.id,
                        avg_ttfb_ms=m.latency_ms or 0.0,
                    )
                    for m in (result.ok_models if result else [])
                ]
            else:
                models = [
                    ModelCandidate(platform=platform, model_id=m, display_name=m)
                    for m in config.get("models", [])
                ]
            self._router.add_provider(platform, provider, models)
            self._router.add_key(platform, api_key, label=config.get("label", "main"))

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        catalog: ModelCatalog | str | Path | None = None,
        **kwargs: Any,
    ) -> "LLMClient":
        """Build from ``<PLATFORM>_KEY`` env vars (e.g. ``GROQ_KEY``), optionally via a .env file."""
        if env_file is not None:
            load_env_file(env_file)
        providers = {
            p: {"api_key": os.environ[s.env_var]}
            for p, s in PROVIDERS.items()
            if os.environ.get(s.env_var)
        }
        return cls(providers=providers, catalog=catalog, **kwargs)

    @property
    def chat(self) -> "ChatNamespace":
        return ChatNamespace(self)

    def get_models(self) -> list[dict[str, Any]]:
        """Get all routable models."""
        return self._router.get_models()

    async def close(self) -> None:
        """Close all provider connections."""
        for provider in self._router._providers.values():
            await provider.close()


class ChatNamespace:
    """Namespace for chat completions."""

    def __init__(self, client: LLMClient):
        self._client = client

    def create(
        self,
        model: str = "auto",
        messages: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict | None = None,
        abort_signal: asyncio.Event | None = None,
        capability: str = "chat",
        **kwargs,
    ) -> Coroutine[Any, Any, ChatCompletionResponse] | AsyncGenerator[ChatCompletionChunk, None]:
        """
        Create a chat completion.

        Args:
            model: Model ID or routing strategy (e.g., "auto", "auto:smart", "gpt-4o")
            messages: List of chat messages
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            tools: List of tool definitions for function calling
            tool_choice: Tool choice ("auto", "none", or specific tool)
            abort_signal: Asyncio event to abort the request

        Returns:
            ChatCompletionResponse if stream=False, AsyncGenerator if stream=True
        """
        messages = messages or []
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if tools:
            options["tools"] = tools
        if tool_choice:
            options["tool_choice"] = tool_choice
        options.update(kwargs)

        if stream:
            return self._stream_create(model, messages, options, abort_signal, capability)
        else:
            return self._create(model, messages, options, abort_signal, capability)

    async def _create(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        abort_signal: asyncio.Event | None = None,
        capability: str = "chat",
    ) -> ChatCompletionResponse:
        """Non-streaming chat completion."""

        async def dispatch(
            route: RouteResult, messages: list[dict[str, Any]], options: dict[str, Any]
        ):
            """Dispatch to the selected provider."""
            start = time.time()
            try:
                response = await route.provider.chat_completion(
                    route.key.api_key,
                    messages,
                    route.model_id,
                    options,
                )
                (time.time() - start) * 1000
                return response
            except ProviderHttpError:
                raise
            except Exception as e:
                raise ProviderHttpError(str(e), status=500)

        async def dispatch_fn(
            route: RouteResult, messages: list[dict[str, Any]], options: dict[str, Any]
        ):
            """Wrapper for fallback loop."""
            return await dispatch(route, messages, options)

        result = await run_fallback_loop(
            self._client._router,
            dispatch_fn,
            model,
            messages,
            options,
            stream=False,
            abort_signal=abort_signal,
        )

        if isinstance(result, ChatCompletionResponse):
            if result.routed_via == "exhausted":
                raise ProviderHttpError(result.choices[0].message.content or "", status=503)
            return result
        elif isinstance(result, dict):
            return ChatCompletionResponse(
                model=model,
                choices=[
                    Choice(
                        message=ChoiceMessage(
                            content=result.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", ""),
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=result.get("usage"),
                routed_via=result.get("routed_via"),
            )
        else:
            raise RuntimeError("Failed to get a response from any provider")

    async def _stream_create(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        abort_signal: asyncio.Event | None = None,
        capability: str = "chat",
    ) -> AsyncGenerator[ChatCompletionChunk, None]:
        """Streaming chat completion."""
        chunk_id = f"chatcmpl-{int(time.time())}"

        async def dispatch(route: RouteResult):
            """Dispatch streaming to provider."""
            start = time.time()
            try:
                async for chunk in route.provider.stream_chat_completion(
                    route.key.api_key,
                    messages,
                    route.model_id,
                    options,
                ):
                    if abort_signal and abort_signal.is_set():
                        break
                    chunk_chunk = ChatCompletionChunk(
                        id=f"{chunk_id}-{int((time.time() - start) * 1000)}",
                        model=route.model_id,
                        choices=[
                            StreamChoice(
                                delta=DeltaMessage(
                                    content=chunk.get("choices", [{}])[0]
                                    .get("delta", {})
                                    .get("content", "")
                                ),
                                finish_reason=chunk.get("choices", [{}])[0].get("finish_reason"),
                            )
                        ],
                    )
                    yield chunk_chunk
            except ProviderHttpError:
                raise
            except Exception as e:
                raise ProviderHttpError(str(e), status=500)

        async def dispatch_fn(route: RouteResult):
            """Wrapper for fallback loop — returns an async generator of chunks."""
            async for chunk in dispatch(route):
                chunk.routed_via = f"{route.platform}/{route.model_id}"
                yield chunk

        try:
            result = await run_fallback_loop(
                self._client._router,
                dispatch_fn,
                model,
                messages,
                options,
                stream=True,
                abort_signal=abort_signal,
                capability=capability,
            )
            async for chunk in result:
                yield chunk
        except StopAsyncIteration:
            pass


# ── Synchronous wrapper ──────────────────────────────────────────────────────


class _SyncChat:
    def __init__(self, owner: "SyncLLMClient"):
        self._owner = owner

    def create(self, *args: Any, stream: bool = False, **kwargs: Any):
        """Mirror of ``LLMClient.chat.create``; ``stream=True`` returns a plain iterator."""
        owner = self._owner
        chat = owner._client.chat
        if not stream:
            return owner._run(chat.create(*args, **{**kwargs, "stream": False}))
        agen = chat.create(*args, **{**kwargs, "stream": True})
        return owner._iterate(agen)


class SyncLLMClient:
    """Blocking wrapper: same API as LLMClient, runs the async client on a private loop thread."""

    def __init__(self, *args: Any, **kwargs: Any):
        self._client = LLMClient(*args, **kwargs)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self.chat = _SyncChat(self)

    @classmethod
    def from_env(cls, *args: Any, **kwargs: Any) -> "SyncLLMClient":
        self = cls.__new__(cls)
        self._client = LLMClient.from_env(*args, **kwargs)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self.chat = _SyncChat(self)
        return self

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _iterate(self, agen):
        while True:
            try:
                yield self._run(agen.__anext__())
            except StopAsyncIteration:
                return

    def get_models(self) -> list[dict[str, Any]]:
        return self._client.get_models()

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._run(self._client.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def __enter__(self) -> "SyncLLMClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
