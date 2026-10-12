"""Discover each provider's models and probe every one with a tiny real request.

Turns "provider X is broken" into per-model facts: which models answer, which are gone,
which hit billing or rate limits. Output is a :class:`ModelCatalog` that ``LLMClient``
can route from.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from .catalog import ModelCatalog, ModelResult, ProviderResult, Status
from .client import LLMClient
from .providers.base import BaseProvider, ProviderHttpError
from .providers.registry import PROVIDERS, ProviderSpec
from .router import is_chat_model, is_embedding_model

PROBE_PROMPT = [{"role": "user", "content": "Reply with the single word OK."}]
PROBE_OPTIONS = {"max_tokens": 32, "temperature": 0}
EMBED_PROBE_TEXT = "tsp-ai probe"
PROBE_TIMEOUT_S = 45.0
MAX_RETRY_WAIT_S = 20.0
RATE_LIMIT_RETRIES = 2

_BILLING_WORDS = (
    "insufficient",
    "quota",
    "balance",
    "credit",
    "billing",
    "payment",
    "paused",
    "disabled",
    "exceeded your current",
    "add a payment",
)
_MODEL_WORDS = (
    "not found",
    "does not exist",
    "not supported",
    "decommission",
    "deprecated",
    "unknown model",
    "invalid model",
    "no such model",
    "not available",
    "no endpoints",
    "end of life",
    "access to model",
    "terms acceptance",
    "not a chat model",
    "model_not_found",
)


def classify(err: ProviderHttpError) -> Status:
    status = err.status or 0
    msg = str(err).lower()
    if status == 408 or "timeout" in msg:
        return Status.TIMEOUT
    if status == 402 or (any(w in msg for w in _BILLING_WORDS) and status in (400, 403, 429)):
        return Status.QUOTA_OR_BILLING
    if status == 429:
        return Status.RATE_LIMITED
    if status in (404, 410) or (status in (400, 403, 422) and any(w in msg for w in _MODEL_WORDS)):
        return Status.MODEL_UNAVAILABLE
    if status in (401, 403):
        return Status.AUTH
    if status >= 500:
        return Status.SERVER_ERROR
    return Status.BAD_REQUEST


def _model_id(entry: dict[str, Any]) -> str | None:
    value = entry.get("id") or entry.get("model_id") or entry.get("name")
    return str(value) if value else None


def _is_free(spec: ProviderSpec, entry: dict[str, Any]) -> bool:
    if spec.free_tier_only:
        return True
    mid = _model_id(entry) or ""
    pricing = entry.get("pricing")
    if isinstance(pricing, dict) and pricing.get("prompt") is not None:
        try:
            return float(pricing["prompt"]) == 0 and float(pricing.get("completion", 0)) == 0
        except (TypeError, ValueError):
            return False
    return any(marker in mid for marker in spec.free_markers)


def _reply_ok(response: Any) -> bool:
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False
    content = message.get("content")
    if isinstance(content, list):  # content-part arrays: [{"type": "text", "text": ...}]
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return bool(
        (content.strip() if isinstance(content, str) else content)
        or message.get("reasoning_content")
        or message.get("reasoning")
        or message.get("tool_calls")
    )


async def _probe(provider: BaseProvider, api_key: str, model_id: str) -> ModelResult:
    start = time.monotonic()
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                provider.chat_completion(api_key, PROBE_PROMPT, model_id, dict(PROBE_OPTIONS)),
                PROBE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            return ModelResult(id=model_id, status=Status.TIMEOUT, error="probe timed out")
        except ProviderHttpError as e:
            status = classify(e)
            if status is Status.RATE_LIMITED and attempt < RATE_LIMIT_RETRIES:
                wait = (e.retry_after_ms or 2000 * (attempt + 1)) / 1000
                await asyncio.sleep(min(wait, MAX_RETRY_WAIT_S))
                continue
            return ModelResult(id=model_id, status=status, error=str(e)[:300])
        except Exception as e:  # adapter bug or malformed upstream body
            return ModelResult(
                id=model_id, status=Status.SERVER_ERROR, error=f"{type(e).__name__}: {e}"[:300]
            )
        latency = (time.monotonic() - start) * 1000
        if _reply_ok(response):
            return ModelResult(id=model_id, status=Status.OK, latency_ms=round(latency))
        return ModelResult(
            id=model_id,
            status=Status.EMPTY_REPLY,
            latency_ms=round(latency),
            error=str(response)[:300],
        )
    raise AssertionError("unreachable")


def _vector_dims(response: Any) -> int | None:
    try:
        return len(response["data"][0]["embedding"]) or None
    except (KeyError, IndexError, TypeError):
        return None


async def _probe_embedding(provider: BaseProvider, api_key: str, model_id: str) -> ModelResult:
    """One short input; records the vector length so `embeddings.create` can reject a mismatch."""
    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            provider.embedding(api_key, [EMBED_PROBE_TEXT], model_id, {}), PROBE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return ModelResult(id=model_id, status=Status.TIMEOUT, error="probe timed out")
    except ProviderHttpError as e:
        return ModelResult(id=model_id, status=classify(e), error=str(e)[:300])
    except NotImplementedError:
        return ModelResult(id=model_id, status=Status.SKIPPED, error="embeddings not supported")
    except Exception as e:
        return ModelResult(
            id=model_id, status=Status.SERVER_ERROR, error=f"{type(e).__name__}: {e}"[:300]
        )
    latency = round((time.monotonic() - start) * 1000)
    dims = _vector_dims(response)
    if dims is None:
        return ModelResult(
            id=model_id, status=Status.EMPTY_REPLY, latency_ms=latency, error=str(response)[:300]
        )
    return ModelResult(id=model_id, status=Status.OK, latency_ms=latency, dims=dims)


async def verify_provider(
    spec: ProviderSpec,
    provider: BaseProvider,
    api_key: str,
    *,
    free_only: bool = False,
    concurrency: int = 2,
    max_models: int | None = None,
) -> ProviderResult:
    """List then probe one provider. Never raises for upstream failures."""
    entries: list[dict[str, Any]]
    try:
        entries = await asyncio.wait_for(provider.list_models(api_key), PROBE_TIMEOUT_S)
    except NotImplementedError:
        entries = [{"id": m} for m in spec.fallback_models]
    except asyncio.TimeoutError:
        return ProviderResult(
            platform=spec.platform, key_status=Status.TIMEOUT, error="list_models timed out"
        )
    except ProviderHttpError as e:
        entries = [{"id": m} for m in spec.fallback_models]
        if not entries or classify(e) is Status.AUTH:
            return ProviderResult(
                platform=spec.platform, key_status=classify(e), error=str(e)[:300]
            )
    except Exception as e:
        return ProviderResult(
            platform=spec.platform,
            key_status=Status.SERVER_ERROR,
            error=f"list_models: {type(e).__name__}: {e}"[:300],
        )

    embed_entries = [e for e in entries if (mid := _model_id(e)) and is_embedding_model(mid)]
    entries = [e for e in entries if (mid := _model_id(e)) and is_chat_model(mid)]
    listed = len(entries)
    if free_only:
        entries = [e for e in entries if _is_free(spec, e)]
        embed_entries = [e for e in embed_entries if _is_free(spec, e)]
    ids = [_model_id(e) for e in entries]
    embed_ids = [_model_id(e) for e in embed_entries]
    if max_models:
        ids = ids[:max_models]
        embed_ids = embed_ids[:max_models]

    sem = asyncio.Semaphore(concurrency)

    async def guarded(model_id: str) -> ModelResult:
        async with sem:
            return await _probe(provider, api_key, model_id)

    async def guarded_embedding(model_id: str) -> ModelResult:
        async with sem:
            return await _probe_embedding(provider, api_key, model_id)

    results = await asyncio.gather(*(guarded(m) for m in ids if m))
    embedding_results = await asyncio.gather(*(guarded_embedding(m) for m in embed_ids if m))
    key_status = Status.OK
    if results and all(r.status is Status.AUTH for r in results):
        key_status = Status.AUTH
    return ProviderResult(
        platform=spec.platform,
        key_status=key_status,
        listed=listed,
        models=list(results),
        embedding_models=list(embedding_results),
    )


async def verify(
    client: LLMClient,
    platforms: list[str] | None = None,
    *,
    free_only: bool = False,
    concurrency: int = 2,
    max_models: int | None = None,
    on_provider: Callable[[ProviderResult], None] | None = None,
) -> ModelCatalog:
    """Verify every provider registered on ``client`` (or only ``platforms``), concurrently."""
    router = client._router
    wanted = platforms or list(router._providers)

    async def one(platform: str) -> ProviderResult:
        keys = router._keys.get(platform) or []
        if not keys:
            result = ProviderResult(platform=platform, key_status=Status.AUTH, error="no API key")
        else:
            result = await verify_provider(
                PROVIDERS[platform] if platform in PROVIDERS else ProviderSpec(platform, platform),
                router._providers[platform],
                keys[0].api_key,
                free_only=free_only,
                concurrency=concurrency,
                max_models=max_models,
            )
        if on_provider:
            on_provider(result)
        return result

    results = await asyncio.gather(*(one(p) for p in wanted))
    return ModelCatalog(providers={r.platform: r for r in results})
