"""
Fallback loop: Transparent retry and failover across providers.

This module implements the shared retry/fallback loop that handles:
- Automatic failover when a provider fails
- Classification of errors (rate limit, auth, timeout, etc.)
- Cooldown management for failed keys/models
- Client abort detection
- Request tracing for debugging
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from .models import ChatCompletionChunk, ChatCompletionResponse, Choice, ChoiceMessage
from .providers.base import ProviderHttpError
from .router import LLMRouter, RouteResult

# Maximum fallback hops
FALLBACK_MAX_RETRIES = 20

# Time budget per request (30 seconds total)
REQUEST_TIMEOUT_SECONDS = 30


class FallbackState:
    """Per-request state for tracking skipped keys/models/platforms."""

    def __init__(self):
        self.skip_keys: set = set()  # "platform:key_id"
        self.skip_models: set = set()  # model_ids
        self.skip_platforms: set = set()  # platform names
        self.attempts: list[dict[str, Any]] = []


def _build_exhaustion_response(
    error: ProviderHttpError, state: FallbackState, router: LLMRouter
) -> ChatCompletionResponse:
    """Construct a fallback exhaustion response.

    Returns a ChatCompletionResponse with a clear error message and
    `routed_via="exhausted"`.
    """
    message = str(error)
    return ChatCompletionResponse(
        id="fallback-exhausted",
        object="chat.completion",
        created=int(time.time()),
        model="none",
        choices=[
            Choice(
                message=ChoiceMessage(content=f"All routes exhausted: {message}"),
                finish_reason="stop",
            )
        ],
        usage=None,
        routed_via="exhausted",
    )


def classify_error(error: ProviderHttpError) -> str:
    """Classify an error into a category for fallback decisions."""
    if error.status in (401, 403):
        return "auth"
    elif error.status == 402:
        return "payment_required"
    elif error.status == 429:
        return "rate_limit"
    elif error.status == 400:
        return "bad_request"
    elif error.status is not None and error.status >= 500:
        return "server_error"
    elif "timeout" in str(error).lower():
        return "timeout"
    else:
        return "unknown"


def should_retry(error: ProviderHttpError) -> bool:
    """Determine if a request should be retried."""
    categories = ("rate_limit", "server_error", "timeout", "unknown")
    return classify_error(error) in categories


async def run_fallback_loop(
    router: LLMRouter,
    dispatch_fn,  # Callable that takes RouteResult and returns response
    model: str,
    messages: list[dict[str, Any]],
    options: dict[str, Any],
    stream: bool = False,
    abort_signal: asyncio.Event | None = None,
    capability: str = "chat",
) -> Any:
    """
    Run the fallback loop for a chat completion request.

    Args:
        router: The LLMRouter instance
        dispatch_fn: Function to call for each attempt: (route_result) -> response
        model: The requested model or routing strategy
        messages: Chat messages
        options: Additional options
        stream: Whether this is a streaming request
        abort_signal: Optional abort signal for client disconnect

    Returns:
        The final response (non-streaming) or async generator (streaming)
    """
    state = FallbackState()
    start_time = time.time()
    last_error: ProviderHttpError | None = None

    for _attempt in range(FALLBACK_MAX_RETRIES):
        # Check abort signal
        if abort_signal and abort_signal.is_set():
            return None

        # Check time budget
        elapsed = (time.time() - start_time) * 1000
        if elapsed > REQUEST_TIMEOUT_SECONDS * 1000:
            break

        # Route the request
        route = router.route_request(model, messages, options, capability=capability)
        if not route:
            last_error = ProviderHttpError(router.format_exhaustion([]), status=503)
            break

        # Check if model/platform is skipped
        model_key = f"{route.platform}:{route.model_id}"
        if model_key in state.skip_models:
            continue
        if route.platform in state.skip_platforms:
            continue

        try:
            # Dispatch to provider
            if stream:
                return _stream_dispatch(route, dispatch_fn, state, abort_signal, router, capability)
            else:
                response = await dispatch_fn(route, messages, options)

                # Record success
                if hasattr(response, "usage") and response.usage:
                    router.record_success(
                        route.platform,
                        route.model_id,
                        route.key.key_id,
                        response.usage.total_tokens,
                        int(elapsed),
                        response.usage.total_tokens / max(0.1, elapsed / 1000),
                    )
                # Prune expired cooldowns/leases
                router.rate_limiter.prune_expired()

                # Add routing metadata
                if isinstance(response, dict):
                    response["routed_via"] = f"{route.platform}/{route.model_id}"
                else:
                    response.routed_via = f"{route.platform}/{route.model_id}"
                state.attempts.append(
                    {
                        "model": route.model_id,
                        "platform": route.platform,
                        "key": route.key.label or route.key.key_id,
                        "success": True,
                    }
                )
                return response

        except ProviderHttpError as e:
            last_error = e
            category = classify_error(e)
            state.attempts.append(
                {
                    "model": route.model_id,
                    "platform": route.platform,
                    "key": route.key.label or route.key.key_id,
                    "error": str(e),
                    "category": category,
                    "success": False,
                }
            )

            # Handle different error categories
            if category == "auth":
                # Don't retry same key - mark as bad
                key_key = f"{route.platform}:{route.key.key_id}"
                state.skip_keys.add(key_key)
                # Don't skip the model entirely, might work with another key
            elif category == "payment_required":
                # Skip this model/key combination
                state.skip_keys.add(f"{route.platform}:{route.key.key_id}")
                state.skip_models.add(route.model_id)
            elif category == "rate_limit":
                # Record rate limit and set cooldown
                router.record_failure(route.platform, route.model_id, route.key.key_id, e)
                # Small backoff
                await asyncio.sleep(0.1)
            elif category == "timeout":
                router.record_failure(route.platform, route.model_id, route.key.key_id, e)
            else:
                router.record_failure(route.platform, route.model_id, route.key.key_id, e)

            # Check if we should give up on this model
            model_failures = sum(
                1
                for a in state.attempts
                if a.get("model") == route.model_id and not a.get("success", True)
            )
            if model_failures >= 5:
                state.skip_models.add(route.model_id)
                router.bench_model(route.platform, route.model_id)

            continue

        except Exception as e:
            last_error = ProviderHttpError(str(e), status=500)
            state.attempts.append(
                {
                    "model": route.model_id,
                    "platform": route.platform,
                    "key": route.key.label or route.key.key_id,
                    "error": str(e),
                    "success": False,
                }
            )
            router.record_failure(route.platform, route.model_id, route.key.key_id, e)
            continue

    # All attempts exhausted
    return _build_exhaustion_response(
        last_error or ProviderHttpError("No routes available", status=503), state, router
    )


async def _stream_dispatch(
    route: RouteResult,
    dispatch_fn,
    state: FallbackState,
    abort_signal: asyncio.Event | None,
    router: LLMRouter,
    capability: str = "chat",
) -> AsyncGenerator[ChatCompletionChunk, None]:
    """Handle streaming dispatch with fallback support.

    This implementation attempts the first provider, and if a
    ProviderHttpError occurs it records the failure, respects any
    `retry_after_ms` hint, and then falls back to a new route.
    """
    # Try the first provider
    try:
        async for chunk in dispatch_fn(route):
            if abort_signal and abort_signal.is_set():
                break
            chunk.routed_via = f"{route.platform}/{route.model_id}"
            # optional: flag successful chunk
            yield chunk
    except ProviderHttpError as e:
        # Record failure for diagnostics
        state.attempts.append(
            {
                "model": route.model_id,
                "platform": route.platform,
                "key": route.key.label or route.key.key_id,
                "error": str(e),
                "category": classify_error(e),
                "success": False,
            }
        )
        # Respect retry hint if present
        retry_after_ms = getattr(e, "retry_after_ms", None)
        if retry_after_ms:
            await asyncio.sleep(retry_after_ms / 1000.0)
        # Attempt fallback to next candidate via router
        new_route = router.route_request(route.model_id, [], {}, capability=capability)
        if new_route:
            async for fallback_chunk in _stream_dispatch(
                new_route, dispatch_fn, state, abort_signal, router, capability
            ):
                yield fallback_chunk
        else:
            # No more routes – end stream
            return
    except Exception as ex:
        raise ProviderHttpError(str(ex), status=500)
