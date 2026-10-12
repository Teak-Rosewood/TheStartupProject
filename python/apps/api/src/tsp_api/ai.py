"""The AI gateway: OpenAI-compatible endpoints for customers' applications, authenticated by API key,
rate limited per plan, and billed in credits per request."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from tsp.accounts import Accounts, ApiKeyInfo
from tsp.ai import LLMClient, ProviderHttpError
from tsp.ai.models import ChatCompletionChunk, ChatCompletionResponse
from tsp.ai.server import ChatRequest, error_body, http_status, sse_frame
from tsp.cache import Cache
from tsp.flags import Flags
from tsp.metering import Rates, estimate_tokens, price_credits, record_usage
from tsp.payments import InsufficientCredits, Payments

log = logging.getLogger("tsp_api.ai")
KIND = "ai.chat"


@dataclass
class AiGateway:
    client: LLMClient
    accounts: Accounts
    flags: Flags
    payments: Payments | None = None
    rates: Rates = Rates()
    default_rpm: int = 20  # requests per minute for organizations whose plan sets no `ai_rpm`
    min_balance: int = 1  # credits needed to start a request
    # Return a reason to refuse (and record) a request; None lets it through. Plug in a moderation service here.
    moderate: Callable[[list[dict[str, Any]]], Awaitable[str | None]] | None = None


def _err(status: int, kind: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"type": kind, "message": message})


def build_router(gw: AiGateway) -> APIRouter:
    r = APIRouter()

    async def authenticate(request: Request) -> ApiKeyInfo:
        auth = request.headers.get("authorization", "")
        raw = (
            auth[7:] if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
        )
        async with (
            request.app.state.db.session() as s
        ):  # short session: never held open during a model call
            info = await gw.accounts.verify_api_key(s, raw)
        if info is None:
            raise _err(401, "invalid_api_key", "invalid or revoked API key")
        if "chat" not in info.scopes:
            raise _err(403, "insufficient_scope", "this API key does not have the 'chat' scope")
        return info

    async def preflight(request: Request, key: ApiKeyInfo, messages: list[dict[str, Any]]) -> None:
        db = request.app.state.db
        async with db.session() as s:
            if await gw.flags.killed(s, "ai"):
                raise _err(503, "feature_disabled", "AI requests are temporarily unavailable")
            ent = await gw.payments.entitlements(s, key.org_id) if gw.payments else None
            balance = (
                await gw.payments.credit_balance(s, key.org_id) if gw.payments else gw.min_balance
            )
        if balance < gw.min_balance:
            raise _err(
                402,
                "insufficient_credits",
                "you have no credits left; buy a credit pack or upgrade your plan",
            )
        rpm = int((ent.features.get("ai_rpm") if ent else None) or gw.default_rpm)
        cache: Cache = request.app.state.cache
        if not await cache.allow(f"ai:{key.org_id}", rpm, 60):
            raise _err(429, "rate_limited", f"your plan allows {rpm} AI requests per minute")
        if gw.moderate and (reason := await gw.moderate(messages)):
            async with db.session() as s:
                await record_usage(
                    s,
                    org_id=key.org_id,
                    api_key_id=key.id,
                    kind=KIND,
                    status="blocked",
                    request_id=request.state.request_id,
                    reason=reason[:200],
                )
            raise _err(400, "content_blocked", "this request was blocked by content rules")

    async def settle(
        request: Request,
        key: ApiKeyInfo,
        *,
        model: str,
        provider: str | None,
        tin: int,
        tout: int,
        started: float,
        status: str = "ok",
    ) -> int:
        """Charge and record. Idempotent per request id; never charges more than the balance holds."""
        credits = price_credits(tin, tout, gw.rates) if status == "ok" else 0
        rid = request.state.request_id
        charged = 0
        async with request.app.state.db.session() as s:
            if credits and gw.payments:
                balance = await gw.payments.credit_balance(s, key.org_id)
                charged = min(
                    credits, balance
                )  # a request that overruns the balance takes what is left; the next one is refused
                if charged < credits:
                    log.warning("org %s overdrew by %s credits", key.org_id, credits - charged)
                if charged:
                    try:
                        await gw.payments.spend_credits(
                            s, key.org_id, charged, reason="ai.chat", ref=f"ai:{rid}"
                        )
                    except InsufficientCredits:  # raced with another request
                        charged = 0
            await record_usage(
                s,
                org_id=key.org_id,
                api_key_id=key.id,
                kind=KIND,
                model=model,
                provider=provider,
                units_in=tin,
                units_out=tout,
                credits=charged,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status=status,
                request_id=rid,
            )
        return charged

    @r.post("/v1/chat/completions")
    async def chat(body: ChatRequest, request: Request) -> Any:
        key = await authenticate(request)
        await preflight(request, key, body.messages)
        params = body.model_dump(exclude={"model", "messages", "stream"}, exclude_none=True)
        prompt_text = " ".join(str(m.get("content") or "") for m in body.messages)
        started = time.perf_counter()
        try:
            if not body.stream:
                resp = await cast(
                    "Awaitable[ChatCompletionResponse]",
                    gw.client.chat.create(
                        model=body.model, messages=body.messages, stream=False, **params
                    ),
                )
                usage = resp.usage
                out_text = " ".join((c.message.content or "") for c in resp.choices)
                tin = (
                    usage.prompt_tokens
                    if usage and usage.prompt_tokens
                    else estimate_tokens(prompt_text)
                )
                tout = (
                    usage.completion_tokens
                    if usage and usage.completion_tokens
                    else estimate_tokens(out_text)
                )
                routed = resp.routed_via or ""
                charged = await settle(
                    request,
                    key,
                    model=routed.split("/", 1)[-1] or body.model,
                    provider=routed.split("/", 1)[0] or None,
                    tin=tin,
                    tout=tout,
                    started=started,
                )
                return JSONResponse(
                    resp.model_dump(exclude_none=True), headers={"x-credits-charged": str(charged)}
                )

            stream = cast(
                "AsyncGenerator[ChatCompletionChunk, None]",
                gw.client.chat.create(
                    model=body.model, messages=body.messages, stream=True, **params
                ),
            )
            first = (
                await stream.__anext__()
            )  # surface routing errors as a real status code before streaming starts
        except StopAsyncIteration:
            first = None
            stream = cast("AsyncGenerator[ChatCompletionChunk, None]", _empty())
        except ProviderHttpError as e:
            await settle(
                request,
                key,
                model=body.model,
                provider=None,
                tin=0,
                tout=0,
                started=started,
                status="error",
            )
            return JSONResponse(error_body(str(e), "upstream_error"), status_code=http_status(e))

        async def frames() -> AsyncIterator[str]:
            out: list[str] = []
            routed = ""
            try:
                chunks = _chain(first, stream)
                async for chunk in chunks:
                    routed = chunk.routed_via or routed
                    out.extend(c.delta.content or "" for c in chunk.choices)
                    yield sse_frame(chunk.model_dump(exclude_none=True))
            except ProviderHttpError as e:
                yield sse_frame(error_body(str(e), "upstream_error"))
            finally:
                # Streams carry no usage numbers, so charge from an estimate. Shielded so a client that
                # disconnects mid-stream still pays for what it was sent.
                text_out = "".join(out)
                try:
                    await asyncio.shield(
                        settle(
                            request,
                            key,
                            model=routed.split("/", 1)[-1] or body.model,
                            provider=routed.split("/", 1)[0] or None,
                            tin=estimate_tokens(prompt_text),
                            tout=estimate_tokens(text_out),
                            started=started,
                            status="ok" if text_out else "error",
                        )
                    )
                except Exception:
                    log.exception("could not settle streamed request")
            yield "data: [DONE]\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    @r.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        await authenticate(request)
        seen: dict[str, str] = {}
        for m in gw.client.get_models():
            if not m.get("disabled"):
                seen.setdefault(m["model_id"], m["platform"])
        return {
            "object": "list",
            "data": [{"id": i, "object": "model", "owned_by": p} for i, p in sorted(seen.items())],
        }

    return r


async def _empty() -> AsyncIterator[ChatCompletionChunk]:
    return
    yield  # pragma: no cover  (makes this an async generator)


async def _chain(
    first: ChatCompletionChunk | None, rest: AsyncGenerator[ChatCompletionChunk, None]
) -> AsyncIterator[ChatCompletionChunk]:
    if first is not None:
        yield first
        async for c in rest:
            yield c
