import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import BaseProvider, ProviderHttpError, build_error


class GoogleProvider(BaseProvider):
    """Google Gemini API adapter."""

    PLATFORM = "google"

    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=60.0,
        )

    @property
    def platform(self) -> str:
        return self.PLATFORM

    @property
    def name(self) -> str:
        return "Google (Gemini)"

    def _build_payload(
        self, messages: list[dict[str, Any]], model_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        """Convert to Gemini API format."""
        contents = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                continue  # System instructions handled separately
            role = "model" if role == "assistant" else "user"
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multimodal - simplify for now
                parts = []
                for part in content:
                    if part.get("type") == "text":
                        parts.append({"text": part.get("text", "")})
                content = parts
            else:
                content = [{"text": content}]
            contents.append({"role": role, "parts": content})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {},
        }
        if "temperature" in options:
            payload["generationConfig"]["temperature"] = options["temperature"]
        if "top_p" in options:
            payload["generationConfig"]["top_p"] = options["top_p"]
        if "max_tokens" in options:
            payload["generationConfig"]["maxOutputTokens"] = options["max_tokens"]
        if options.get("stop"):
            payload["generationConfig"]["stopSequences"] = options["stop"]

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
        url = f"/models/{model_id}:generateContent?key={api_key}"

        try:
            resp = await self._http.post(url, json=payload)
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
        payload["generationConfig"]["stream"] = True
        url = f"/models/{model_id}:streamGenerateContent?key={api_key}"

        try:
            async with self._http.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise build_error(resp.status_code, body.decode())
                async for line in resp.aiter_lines():
                    if line.strip():
                        try:
                            data = json.loads(line)
                            for candidate in data.get("candidates", []):
                                for part in candidate.get("content", {}).get("parts", []):
                                    text = part.get("text", "")
                                    if text:
                                        yield {
                                            "id": f"chatcmpl-{model_id}",
                                            "object": "chat.completion.chunk",
                                            "model": model_id,
                                            "choices": [
                                                {
                                                    "index": 0,
                                                    "delta": {"content": text},
                                                    "finish_reason": None,
                                                }
                                            ],
                                        }
                        except json.JSONDecodeError:
                            continue
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")

    def _to_openai_format(self, data: dict[str, Any], model_id: str) -> dict[str, Any]:
        content = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                content += part.get("text", "")

        usage = data.get("usageMetadata", {})
        return {
            "id": f"chatcmpl-{model_id}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
        }

    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        try:
            resp = await self._http.get(
                f"/models?key={api_key}",
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception as e:
            return {"valid": False, "error": f"Validation error: {e}"}

    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        resp = await self._http.get("/models", params={"key": api_key, "pageSize": 200})
        if resp.status_code != 200:
            raise build_error(resp.status_code, resp.text[:300])
        return [
            {"id": m["name"].removeprefix("models/"), **m}
            for m in resp.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

    async def close(self):
        await self._http.aclose()
