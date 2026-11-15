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

    @property
    def supports_citations(self) -> bool:
        return True

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style messages to Anthropic format.

        Assistant ``tool_calls`` become ``tool_use`` blocks and ``role: tool`` messages become
        ``tool_result`` blocks in a user turn. Consecutive turns with the same role are merged,
        because Anthropic expects user and assistant turns to alternate.
        """
        converted: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "tool":
                result = {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": self._text_blocks(content)
                    if isinstance(content, list)
                    else str(content or ""),
                }
                self._append_turn(converted, "user", [result])
                continue
            if role == "assistant" and msg.get("tool_calls"):
                blocks = [b for b in self._text_blocks(content) if b["text"]] if content else []
                for call in msg["tool_calls"]:
                    fn = call.get("function") or {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": self._tool_input(fn.get("arguments")),
                        }
                    )
                self._append_turn(converted, "assistant", blocks)
                continue
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
                self._append_turn(converted, role, anthropic_content)
            else:
                self._append_turn(converted, role, content)
        return converted

    @classmethod
    def _append_turn(cls, turns: list[dict[str, Any]], role: str, content: Any) -> None:
        """Append a turn, merging it into the previous one when the role repeats."""
        if turns and turns[-1]["role"] == role:
            prev = turns[-1]
            prev["content"] = cls._blocks_of(prev["content"]) + cls._blocks_of(content)
        else:
            turns.append({"role": role, "content": content})

    @staticmethod
    def _blocks_of(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, list):
            return content
        return [{"type": "text", "text": str(content or "")}] if content else []

    @staticmethod
    def _tool_input(arguments: Any) -> dict[str, Any]:
        """OpenAI sends tool arguments as a JSON string; Anthropic wants an object."""
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            value = json.loads(arguments)
        except (TypeError, ValueError):
            return {"input": arguments}
        return value if isinstance(value, dict) else {"input": value}

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI function tools become Anthropic tools; native Anthropic tools pass through."""
        out = []
        for t in tools:
            fn = t.get("function") if t.get("type") == "function" else None
            if fn is None:
                out.append(dict(t))
                continue
            tool: dict[str, Any] = {
                "name": fn["name"],
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            if fn.get("description"):
                tool["description"] = fn["description"]
            out.append(tool)
        return out

    @staticmethod
    def _convert_tool_choice(choice: Any) -> dict[str, Any]:
        """``"auto"``, ``"none"``, ``"required"`` or ``{"type": "function", "function": {"name"}}``
        in the OpenAI style; Anthropic-style dicts pass through."""
        if isinstance(choice, str):
            return {"type": {"required": "any"}.get(choice, choice)}
        if isinstance(choice, dict) and choice.get("type") == "function":
            return {"type": "tool", "name": (choice.get("function") or {}).get("name", "")}
        return dict(choice)

    FINISH_REASONS = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "pause_turn": "stop",
        "refusal": "content_filter",
    }

    @classmethod
    def _finish_reason(cls, stop_reason: str | None) -> str:
        return cls.FINISH_REASONS.get(stop_reason or "end_turn", stop_reason or "stop")

    @staticmethod
    def _text_blocks(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, list):
            return [
                {"type": "text", "text": p.get("text", "")}
                for p in content
                if p.get("type") == "text"
            ]
        return [{"type": "text", "text": str(content or "")}]

    def _build_payload(
        self, messages: list[dict[str, Any]], model_id: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        # Anthropic takes system text as a top-level field, not as a message.
        system: list[dict[str, Any]] = []
        chat: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                system += self._text_blocks(m.get("content"))
            else:
                chat.append(m)
        converted = self._convert_messages(chat)
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": converted,
            "max_tokens": options.get("max_tokens", 1024),
        }
        if system:
            payload["system"] = system
        if options.get("documents"):
            self._attach_documents(converted, options["documents"], bool(options.get("citations")))
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        if "top_p" in options:
            payload["top_p"] = options["top_p"]
        if "stop" in options and options["stop"] is not None:
            payload["stop_sequences"] = options["stop"]
        if options.get("tools"):
            payload["tools"] = self._convert_tools(options["tools"])
        if options.get("tool_choice"):
            payload["tool_choice"] = self._convert_tool_choice(options["tool_choice"])
        self._apply_cache(payload, options.get("cache") or [])
        return payload

    @staticmethod
    def _as_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(msg["content"], str):
            msg["content"] = [{"type": "text", "text": msg["content"]}]
        return msg["content"]  # type: ignore[no-any-return]

    def _attach_documents(
        self, messages: list[dict[str, Any]], documents: list[dict[str, Any]], citations: bool
    ) -> None:
        """Put the documents in front of the last user message as document blocks."""
        target = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if target is None:
            raise ProviderHttpError("documents need a user message to attach to", status=400)
        blocks = [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": d["text"]},
                "title": d.get("title") or d["id"],
                "citations": {"enabled": citations},
            }
            for d in documents
        ]
        target["content"] = blocks + self._as_blocks(target)

    def _apply_cache(self, payload: dict[str, Any], targets: list[Any]) -> None:
        """Mark prompt-cache breakpoints: ``"system"`` (the whole system prompt), ``"tools"``, or a
        message index (that message up to its last block)."""
        mark = {"cache_control": {"type": "ephemeral"}}
        for t in targets:
            if t == "system" and payload.get("system"):
                payload["system"][-1].update(mark)
            elif t == "tools" and payload.get("tools"):
                payload["tools"][-1].update(mark)
            elif isinstance(t, int) and not isinstance(t, bool):
                if not -len(payload["messages"]) <= t < len(payload["messages"]):
                    raise ProviderHttpError(f"cache: no message at index {t}", status=400)
                self._as_blocks(payload["messages"][t])[-1].update(mark)
            elif t not in ("system", "tools"):
                raise ProviderHttpError(f"cache: unknown target {t!r}", status=400)

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
        return self._to_openai_format(data, model_id, options.get("documents"))

    async def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
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
                state: dict[str, Any] = {}
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":") or line.startswith("event:"):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip():
                        out = self._parse_sse_event(json.loads(line), model_id, state)
                        if out:
                            yield out
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")

    def _to_openai_format(
        self,
        data: dict[str, Any],
        model_id: str,
        documents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Convert Anthropic response to OpenAI format."""
        content = ""
        citations: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
            elif block.get("type") == "text":
                content += block.get("text", "")
                for c in block.get("citations") or []:
                    idx = c.get("document_index")
                    if (
                        c.get("type") == "char_location"
                        and documents
                        and idx is not None
                        and idx < len(documents)
                    ):
                        citations.append(
                            {
                                "doc_id": documents[idx]["id"],
                                "start": c.get("start_char_index", 0),
                                "end": c.get("end_char_index", 0),
                                "quoted_text": c.get("cited_text", ""),
                            }
                        )
        u = data.get("usage", {})
        cache_read = u.get("cache_read_input_tokens", 0) or 0
        cache_write = u.get("cache_creation_input_tokens", 0) or 0
        prompt = u.get("input_tokens", 0) + cache_read + cache_write
        completion = u.get("output_tokens", 0)

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = content or None
        response = {
            "id": f"msg_{data.get('id', '')}",
            "object": "chat.completion",
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": self._finish_reason(data.get("stop_reason")),
                }
            ],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            },
        }
        if citations:
            response["citations"] = citations
        return response

    def _parse_sse_event(
        self, event: dict[str, Any], model_id: str, state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Parse one Anthropic SSE event into an OpenAI streaming chunk (``{}`` when there is
        nothing to emit). ``state`` carries usage and tool-call indexes across events."""
        state = state if state is not None else {}
        kind = event.get("type")

        def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
            return {
                "id": f"chatcmpl-{state.get('id', '')}",
                "object": "chat.completion.chunk",
                "model": model_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        if kind == "message_start":
            msg = event.get("message") or {}
            state["id"] = msg.get("id", "")
            u = msg.get("usage") or {}
            state["cache_read"] = u.get("cache_read_input_tokens", 0) or 0
            state["cache_write"] = u.get("cache_creation_input_tokens", 0) or 0
            state["input"] = (
                (u.get("input_tokens", 0) or 0) + state["cache_read"] + state["cache_write"]
            )
            state["output"] = u.get("output_tokens", 0) or 0
            return {}
        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                tools = state.setdefault("tools", {})
                index = tools[event.get("index", 0)] = len(tools)
                return chunk(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {"name": block.get("name", ""), "arguments": ""},
                            }
                        ]
                    }
                )
            return {}
        if kind == "content_block_delta":
            delta = event.get("delta", {})
            if delta.get("type") == "input_json_delta":
                index = state.get("tools", {}).get(event.get("index", 0), 0)
                return chunk(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "function": {"arguments": delta.get("partial_json", "")},
                            }
                        ]
                    }
                )
            if delta.get("type", "text_delta") == "text_delta":
                return chunk({"content": delta.get("text", "")})
            return {}
        if kind == "message_delta":
            u = event.get("usage") or {}
            if u.get("output_tokens") is not None:
                state["output"] = u["output_tokens"]
            state["stop_reason"] = (event.get("delta") or {}).get("stop_reason")
            return {}
        if kind == "message_stop":
            out = chunk({}, self._finish_reason(state.get("stop_reason")))
            if "input" in state:
                out["usage"] = {
                    "prompt_tokens": state["input"],
                    "completion_tokens": state["output"],
                    "total_tokens": state["input"] + state["output"],
                    "cache_read_tokens": state["cache_read"],
                    "cache_write_tokens": state["cache_write"],
                }
            return out
        return {}

    # -- Message Batches ----------------------------------------------------------------------------

    async def batch_submit(
        self, api_key: str, requests: list[tuple[str, str, list[dict[str, Any]], dict[str, Any]]]
    ) -> dict[str, Any]:
        """``requests``: ``(custom_id, model_id, messages, options)``. Returns the batch object."""
        body = {
            "requests": [
                {"custom_id": cid, "params": self._build_payload(msgs, model, opts)}
                for cid, model, msgs, opts in requests
            ]
        }
        return await self._batch_call("POST", "/v1/messages/batches", api_key, json=body)

    async def batch_status(self, api_key: str, batch_id: str) -> dict[str, Any]:
        return await self._batch_call("GET", f"/v1/messages/batches/{batch_id}", api_key)

    async def batch_results(self, api_key: str, batch_id: str) -> list[dict[str, Any]]:
        """One decoded JSON object per result line (``custom_id`` and ``result``)."""
        text = await self._batch_call(
            "GET", f"/v1/messages/batches/{batch_id}/results", api_key, raw=True
        )
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    async def _batch_call(
        self, method: str, path: str, api_key: str, *, json: Any = None, raw: bool = False
    ) -> Any:
        try:
            resp = await self._http.request(
                method, path, json=json, headers={**self._http.headers, "x-api-key": api_key}
            )
        except httpx.RequestError as e:
            raise ProviderHttpError(f"Request failed: {e}")
        if resp.status_code >= 400:
            raise build_error(resp.status_code, resp.text[:500])
        return resp.text if raw else resp.json()

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
