"""Tool calling through the Anthropic adapter: OpenAI-style in, OpenAI-style out."""

import json

import httpx
from tsp.ai import LLMClient, ModelCandidate
from tsp.ai.providers.anthropic import AnthropicProvider

FETCH_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "Fetch a page.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
    },
}


def client_with(handler) -> tuple[LLMClient, list[dict]]:
    seen: list[dict] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return handler(req)

    p = AnthropicProvider()
    p._http = httpx.AsyncClient(
        base_url="https://api.anthropic.com", transport=httpx.MockTransport(record)
    )
    client = LLMClient()
    client._router.add_provider(
        "anthropic", p, [ModelCandidate("anthropic", "claude-x", "claude-x")]
    )
    client._router.add_key("anthropic", "sk-ant")
    return client, seen


def tool_use_message() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "m1",
            "content": [
                {"type": "text", "text": "Let me look."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "fetch_url",
                    "input": {"url": "https://example.com"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 12, "output_tokens": 7},
        },
    )


def text_message(text="done", stop="end_turn") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "m2",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
    )


async def test_openai_tools_and_tool_choice_are_translated():
    client, seen = client_with(lambda r: text_message())
    await client.chat.create(
        model="claude-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[FETCH_TOOL, {"type": "web_search_20250305", "name": "web_search"}],
        tool_choice="required",
    )
    assert seen[0]["tools"] == [
        {
            "name": "fetch_url",
            "description": "Fetch a page.",
            "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}},
        },
        {"type": "web_search_20250305", "name": "web_search"},
    ]
    assert seen[0]["tool_choice"] == {"type": "any"}


async def test_named_tool_choice_and_cache_mark_on_converted_tools():
    client, seen = client_with(lambda r: text_message())
    await client.chat.create(
        model="claude-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[FETCH_TOOL],
        tool_choice={"type": "function", "function": {"name": "fetch_url"}},
        cache=["tools"],
    )
    assert seen[0]["tool_choice"] == {"type": "tool", "name": "fetch_url"}
    assert seen[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "function" not in seen[0]["tools"][-1]


async def test_tool_use_comes_back_as_tool_calls():
    client, _ = client_with(lambda r: tool_use_message())
    resp = await client.chat.create(
        model="claude-x", messages=[{"role": "user", "content": "go"}], tools=[FETCH_TOOL]
    )
    choice = resp.choices[0]
    assert choice.finish_reason == "tool_calls"
    assert choice.message.content == "Let me look."
    call = choice.message.tool_calls[0]
    assert call.id == "toolu_1" and call.function.name == "fetch_url"
    assert json.loads(call.function.arguments) == {"url": "https://example.com"}


async def test_stop_reasons_map_to_openai_finish_reasons():
    client, _ = client_with(lambda r: text_message(stop="max_tokens"))
    resp = await client.chat.create(model="claude-x", messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].finish_reason == "length"
    client, _ = client_with(lambda r: text_message())
    resp = await client.chat.create(model="claude-x", messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].finish_reason == "stop"


async def test_tool_history_becomes_tool_use_and_merged_tool_results():
    client, seen = client_with(lambda r: text_message())
    await client.chat.create(
        model="claude-x",
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "find two things"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "fetch_url", "arguments": '{"url": "https://a"}'},
                    },
                    {
                        "id": "b",
                        "type": "function",
                        "function": {"name": "fetch_url", "arguments": "not json"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "page a"},
            {"role": "tool", "tool_call_id": "b", "content": "page b"},
            {"role": "user", "content": "now answer"},
        ],
        tools=[FETCH_TOOL],
    )
    msgs = seen[0]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == [
        {"type": "tool_use", "id": "a", "name": "fetch_url", "input": {"url": "https://a"}},
        {"type": "tool_use", "id": "b", "name": "fetch_url", "input": {"input": "not json"}},
    ]
    assert msgs[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": "page a"},
        {"type": "tool_result", "tool_use_id": "b", "content": "page b"},
        {"type": "text", "text": "now answer"},
    ]


def sse(*events: dict) -> httpx.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def test_streamed_tool_calls_finish_reason_and_usage():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "m1",
                "usage": {"input_tokens": 20, "cache_read_input_tokens": 5, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_9",
                "name": "fetch_url",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"url": '},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '"https://x"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 9},
        },
        {"type": "message_stop"},
    ]
    client, _ = client_with(lambda r: sse(*events))
    chunks = [
        c
        async for c in client.chat.create(
            model="claude-x",
            messages=[{"role": "user", "content": "go"}],
            tools=[FETCH_TOOL],
            stream=True,
        )
    ]
    text = "".join(c.choices[0].delta.content or "" for c in chunks)
    calls = [tc for c in chunks for tc in (c.choices[0].delta.tool_calls or [])]
    assert text == "Hi"
    assert calls[0].id == "toolu_9" and calls[0].function.name == "fetch_url"
    assert "".join(tc.function.arguments or "" for tc in calls) == '{"url": "https://x"}'
    assert {tc.index for tc in calls} == {0}
    last = chunks[-1]
    assert last.choices[0].finish_reason == "tool_calls"
    assert last.usage.prompt_tokens == 25 and last.usage.completion_tokens == 9
    assert last.usage.cache_read_tokens == 5
