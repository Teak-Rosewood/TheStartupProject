from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import BaseProvider, ProviderHttpError, build_error


class CohereProvider(BaseProvider):
    """Cohere API adapter (Command models)."""

    PLATFORM = "cohere"
    API_VERSION = "2023-03-24"

    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url="https://api.cohere.com/v1",
            headers={"Authorization": "Bearer placeholder", "Cohere-Version": self.API_VERSION},
            timeout=60.0,
        )

    @property
    def platform(self) -> str:
        return self.PLATFORM

    @property
    def name(self) -> str:
        return "Cohere"

    def _build_payload(
        self, messages: list[dict[str, Any]], model_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {"message": "", "model": model_id}
        if options.get("temperature"):
            payload["temperature"] = options["temperature"]
        if options.get("max_tokens"):
            payload["max_tokens"] = options["max_tokens"]
        if options.get("p"):
            payload["p"] = options["p"]
        return payload

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        payload = self._build_payload(messages, model_id, options)
        self._http.headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = await self._http.post("/generate", json=payload)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")

        if resp.status_code >= 400:
            raise build_error(resp.status_code, resp.text, resp.json())

        data = resp.json()
        return self._to_openai_format(data, model_id, last_user_msg)

    def _to_openai_format(
        self, data: dict[str, Any], model_id: str, user_msg: str
    ) -> dict[str, Any]:
        text = data.get("text", "")
        return {
            "id": f"chatcmpl-{model_id}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": data.get("prompt_tokens", 0),
                "completion_tokens": data.get("token_count", 0),
                "total_tokens": data.get("token_count", 0),
            },
        }

    async def embedding(
        self,
        api_key: str,
        input: str | list[str],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /embed``. Options: ``input_type`` ("document" or "query")."""
        options = options or {}
        texts = [input] if isinstance(input, str) else input
        payload = {
            "model": model_id,
            "texts": texts,
            "input_type": "search_query"
            if options.get("input_type") == "query"
            else "search_document",
            "embedding_types": ["float"],
        }
        try:
            resp = await self._http.post(
                "/embed", json=payload, headers={"Authorization": f"Bearer {api_key}"}
            )
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}", status=500)
        if resp.status_code >= 400:
            raise build_error(resp.status_code, resp.text[:300])
        data = resp.json()
        vectors = data.get("embeddings", [])
        if isinstance(vectors, dict):  # shape when embedding_types is requested
            vectors = vectors.get("float", [])
        tokens = (data.get("meta") or {}).get("billed_units", {}).get("input_tokens", 0)
        return {
            "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
        }

    def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        # Cohere streaming not fully implemented
        raise NotImplementedError("Cohere streaming not yet supported")

    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        try:
            await self.list_models(api_key)
            return True
        except ProviderHttpError as e:
            return {"valid": False, "error": str(e)}

    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for endpoint in ("chat", "embed"):
            resp = await self._http.get(
                "/models",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"endpoint": endpoint, "page_size": 100},
                timeout=15.0,
            )
            if resp.status_code != 200:
                raise build_error(resp.status_code, resp.text[:300])
            found += [{"id": m["name"], **m} for m in resp.json().get("models", [])]
        return found

    async def close(self):
        await self._http.aclose()
