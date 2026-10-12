"""OpenAI-compatible HTTP server over :class:`LLMClient` (needs the ``server`` extra)."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .client import LLMClient
from .models import ChatCompletionChunk, ChatCompletionResponse
from .providers.base import ProviderHttpError


class ChatRequest(BaseModel):
    """Body of ``POST /v1/chat/completions``. Unknown OpenAI params pass through to providers."""

    model_config = ConfigDict(extra="allow")

    model: str = "auto"
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


def _error_body(message: str, kind: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": kind}}


def _http_status(err: ProviderHttpError) -> int:
    return err.status if err.status and 400 <= err.status < 600 else 502


def create_app(client: LLMClient, api_key: str | None = None) -> FastAPI:
    """Build the app. With ``api_key`` set, every ``/v1`` route needs ``Authorization: Bearer``."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await client.close()

    app = FastAPI(title="tsp-ai", lifespan=lifespan)

    async def require_auth(authorization: str | None = Header(default=None)) -> None:
        if api_key is None:
            return
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token.encode(), api_key.encode()
        ):
            raise ProviderHttpError("invalid or missing API key", status=401)

    @app.exception_handler(ProviderHttpError)
    async def provider_error(_: Request, exc: ProviderHttpError) -> JSONResponse:
        status = _http_status(exc)
        kind = "authentication_error" if status == 401 else "upstream_error"
        return JSONResponse(_error_body(str(exc), kind), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            _error_body(json.dumps(exc.errors(), default=str), "invalid_request_error"),
            status_code=400,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models", dependencies=[Depends(require_auth)])
    async def list_models() -> dict[str, Any]:
        data = [
            {"id": m["model_id"], "object": "model", "owned_by": m["platform"]}
            for m in client.get_models()
            if not m["disabled"]
        ]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions", dependencies=[Depends(require_auth)])
    async def chat_completions(body: ChatRequest) -> Any:
        params = body.model_dump(exclude={"model", "messages", "stream"}, exclude_none=True)
        if not body.stream:
            result = await cast(
                Coroutine[Any, Any, ChatCompletionResponse],
                client.chat.create(
                    model=body.model, messages=body.messages, stream=False, **params
                ),
            )
            return result.model_dump(exclude_none=True)

        stream = cast(
            AsyncGenerator[ChatCompletionChunk, None],
            client.chat.create(model=body.model, messages=body.messages, stream=True, **params),
        )
        # Pull the first chunk before committing to 200 so routing errors get a real status.
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            first = None
        return StreamingResponse(_sse(first, stream), media_type="text/event-stream")

    return app


def _frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _sse(
    first: ChatCompletionChunk | None, rest: AsyncGenerator[ChatCompletionChunk, None]
) -> AsyncIterator[str]:
    try:
        if first is not None:
            yield _frame(first.model_dump(exclude_none=True))
            async for chunk in rest:
                yield _frame(chunk.model_dump(exclude_none=True))
    except ProviderHttpError as e:
        yield _frame(_error_body(str(e), "upstream_error"))
    yield "data: [DONE]\n\n"
