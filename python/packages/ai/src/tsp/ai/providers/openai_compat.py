"""OpenAI-compatible provider adapter.

This is the generic adapter used by most providers that expose an
OpenAI-compatible API (Groq, Cerebras, Mistral, NVIDIA, OpenRouter, etc.).
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .base import BaseProvider, ProviderHttpError, parse_prose_retry, parse_retry_after_ms


class OpenAICompatProvider(BaseProvider):
    """Generic OpenAI-compatible API provider."""

    def __init__(
        self,
        platform: str,
        name: str,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        validate_url: str | None = None,
        timeout_ms: int = 60_000,
        keyless: bool = False,
        force_single_tool_call: bool = False,
        models_url: str | None = None,
    ):
        self._platform = platform
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._extra_headers = extra_headers or {}
        self._validate_url = validate_url
        self._models_url = models_url
        self._timeout_ms = timeout_ms
        self._keyless = keyless
        self._force_single_tool_call = force_single_tool_call
        self._client: httpx.AsyncClient | None = None

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def name(self) -> str:
        return self._name

    @property
    def keyless(self) -> bool:
        return self._keyless

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_ms / 1000),
                headers={
                    "Content-Type": "application/json",
                    **self._extra_headers,
                },
            )
        return self._client

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        """Build authorization headers."""
        if self._keyless and api_key == "no-key":
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the request payload."""
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
        }

        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "max_tokens" in options:
                payload["max_tokens"] = options["max_tokens"]
            if "top_p" in options:
                payload["top_p"] = options["top_p"]
            if "frequency_penalty" in options:
                payload["frequency_penalty"] = options["frequency_penalty"]
            if "presence_penalty" in options:
                payload["presence_penalty"] = options["presence_penalty"]
            if "stop" in options:
                payload["stop"] = options["stop"]
            if "tools" in options:
                payload["tools"] = options["tools"]
                if self._force_single_tool_call:
                    payload["parallel_tool_calls"] = False
                elif "parallel_tool_calls" in options:
                    payload["parallel_tool_calls"] = options["parallel_tool_calls"]
            if "tool_choice" in options:
                payload["tool_choice"] = options["tool_choice"]
            if "response_format" in options:
                payload["response_format"] = options["response_format"]
            if "seed" in options:
                payload["seed"] = options["seed"]
            if "stream" in options:
                payload["stream"] = options["stream"]

        return payload

    def _extract_error_message(self, response: httpx.Response) -> str:
        """Extract error message from response."""
        try:
            body = response.json()
            if isinstance(body, dict):
                # OpenAI-style error
                if "error" in body:
                    err = body["error"]
                    if isinstance(err, dict):
                        return err.get("message", str(err))
                    return str(err)
                # RFC7807-style error
                if "detail" in body:
                    return str(body["detail"])
                if "title" in body:
                    return str(body["title"])
                if "message" in body:
                    return str(body["message"])
        except Exception:
            pass
        return response.text or f"HTTP {response.status_code}"

    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion."""
        client = await self._get_client()
        url = f"{self._base_url}/chat/completions"
        headers = self._auth_headers(api_key)
        payload = self._build_payload(messages, model_id, options)

        try:
            response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderHttpError(f"Request timeout: {e}", status=408)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}", status=500)

        if response.status_code != 200:
            error_msg = self._extract_error_message(response)
            retry_after = parse_retry_after_ms(response.headers.get("Retry-After"))
            if not retry_after:
                retry_after = parse_prose_retry(response.text)
            raise ProviderHttpError(
                error_msg,
                status=response.status_code,
                retry_after_ms=retry_after,
            )

        return response.json()

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming chat completion."""
        client = await self._get_client()
        url = f"{self._base_url}/chat/completions"
        headers = self._auth_headers(api_key)
        payload = self._build_payload(messages, model_id, options)
        payload["stream"] = True

        try:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_msg = self._extract_error_message(response)
                    retry_after = parse_retry_after_ms(response.headers.get("Retry-After"))
                    raise ProviderHttpError(
                        error_msg,
                        status=response.status_code,
                        retry_after_ms=retry_after,
                    )

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            yield chunk
                        except json.JSONDecodeError:
                            continue
        except httpx.TimeoutException as e:
            raise ProviderHttpError(f"Stream timeout: {e}", status=408)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Stream failed: {e}", status=500)

    async def embedding(
        self,
        api_key: str,
        input: str | list[str],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``POST /embeddings``. Options: ``dims`` (sent as ``dimensions``)."""
        options = options or {}
        payload: dict[str, Any] = {"model": model_id, "input": input}
        if options.get("dims"):
            payload["dimensions"] = options["dims"]
        client = await self._get_client()
        try:
            response = await client.post(
                f"{self._base_url}/embeddings", json=payload, headers=self._auth_headers(api_key)
            )
        except httpx.TimeoutException as e:
            raise ProviderHttpError(f"Request timeout: {e}", status=408)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}", status=500)
        if response.status_code != 200:
            raise ProviderHttpError(
                self._extract_error_message(response),
                status=response.status_code,
                retry_after_ms=parse_retry_after_ms(response.headers.get("Retry-After")),
            )
        try:
            return response.json()
        except ValueError:
            raise ProviderHttpError("embeddings endpoint returned non-JSON", status=502) from None

    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        """Validate an API key by making a minimal request."""
        client = await self._get_client()
        url = self._validate_url or f"{self._base_url}/models"
        headers = self._auth_headers(api_key)

        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                return {"valid": False, "error": "Invalid API key"}
            elif response.status_code == 403:
                return {"valid": False, "error": "Access forbidden"}
            else:
                return {"valid": False, "error": f"HTTP {response.status_code}"}
        except httpx.RequestError as e:
            return {"valid": False, "error": str(e)}

    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        """List models from ``/models``. Raises ProviderHttpError on failure."""
        client = await self._get_client()
        try:
            response = await client.get(
                self._models_url or f"{self._base_url}/models", headers=self._auth_headers(api_key)
            )
        except httpx.TimeoutException as e:
            raise ProviderHttpError(f"Request timeout: {e}", status=408)
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}", status=500)
        if response.status_code != 200:
            raise ProviderHttpError(
                self._extract_error_message(response), status=response.status_code
            )
        try:
            data = response.json()
        except ValueError:
            raise ProviderHttpError(
                f"models endpoint returned non-JSON ({response.headers.get('content-type')}): "
                f"{response.text[:80]!r}",
                status=502,
            ) from None
        if isinstance(data, dict):
            data = data.get("data", data.get("models", []))
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
