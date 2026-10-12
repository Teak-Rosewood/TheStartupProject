import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import BaseProvider, ProviderHttpError, build_error


class AnthropicProvider(BaseProvider):
    """Native Anthropic API adapter (Claude models)."""

    PLATFORM = "anthropic"
    API_VERSION = "2023-06-01"

    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "anthropic-version": self.API_VERSION,
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    @property
    def platform(self) -> str:
        return self.PLATFORM

    @property
    def name(self) -> str:
        return "Anthropic"

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style messages to Anthropic format."""
        converted = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multimodal content
                anthropic_content = []
                for part in content:
                    if part.get("type") == "text":
                        anthropic_content.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        url_data = part.get("image_url", {}).get("url", "")
                        if url_data.startswith("data:"):
                            media_type, data = url_data.split(";base64,")
                            anthropic_content.append(
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type.split(":")[1],
                                        "data": data,
                                    },
                                }
                            )
                        else:
                            anthropic_content.append(
                                {"type": "image", "source": {"type": "url", "url": url_data}}
                            )
                converted.append({"role": role, "content": anthropic_content})
            else:
                converted.append({"role": role, "content": content})
        return converted

    def _build_payload(
        self, messages: list[dict[str, Any]], model_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "model": model_id,
            "messages": self._convert_messages(messages),
            "max_tokens": options.get("max_tokens", 1024),
        }
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "top_p" in options:
            payload["top_p"] = options["top_p"]
        if "stop" in options and options["stop"] is not None:
            payload["stop_sequences"] = options["stop"]
        if options.get("tools"):
            payload["tools"] = options["tools"]
        if options.get("tool_choice"):
            payload["tool_choice"] = options["tool_choice"]
        return payload

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        payload = self._build_payload(messages, model_id, options)
        headers = {**self._http.headers, "x-api-key": api_key}

        try:
            resp = await self._http.post("/v1/messages", json=payload, headers=headers)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")

        if resp.status_code >= 400:
            raise build_error(
                resp.status_code,
                resp.text,
                resp.json() if resp.headers.get("content-type") == "application/json" else None,
            )

        data = resp.json()
        return self._to_openai_format(data, model_id)

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        options = options or {}
        payload = self._build_payload(messages, model_id, options)
        payload["stream"] = True
        headers = {**self._http.headers, "x-api-key": api_key}

        try:
            async with self._http.stream(
                "POST", "/v1/messages", json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise build_error(resp.status_code, body.decode())
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip():
                        yield self._parse_sse_event(json.loads(line), model_id)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")

    def _to_openai_format(self, data: dict[str, Any], model_id: str) -> dict[str, Any]:
        """Convert Anthropic response to OpenAI format."""
        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        response = {
            "id": f"msg_{data.get('id', '')}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": data.get("stop_reason", "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                "total_tokens": sum(data.get("usage", {}).values()),
            },
        }
        return response

    def _parse_sse_event(self, event: dict[str, Any], model_id: str) -> dict[str, Any]:
        """Parse Anthropic SSE event into OpenAI streaming chunk."""
        if event.get("type") == "content_block_delta":
            delta = event.get("delta", {})
            return {
                "id": f"chatcmpl-{event.get('id', '')}",
                "object": "chat.completion.chunk",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": delta.get("text", "")},
                        "finish_reason": None,
                    }
                ],
            }
        elif event.get("type") == "message_stop":
            return {
                "id": f"chatcmpl-{event.get('id', '')}",
                "object": "chat.completion.chunk",
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        return {}

    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        try:
            await self.list_models(api_key)
            return True
        except ProviderHttpError as e:
            return {"valid": False, "error": str(e)}

    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        resp = await self._http.get(
            "/v1/models", headers={"x-api-key": api_key}, params={"limit": 100}, timeout=15.0
        )
        if resp.status_code != 200:
            raise build_error(resp.status_code, resp.text[:300])
        return resp.json().get("data", [])

    async def close(self):
        await self._http.aclose()
