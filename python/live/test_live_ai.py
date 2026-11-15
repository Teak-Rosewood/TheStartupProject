"""One real call per provider whose key is set, including a Claude tool-calling round trip."""

import json
import os

import pytest
from live_support import need_env
from tsp.ai import LLMClient, ModelCandidate
from tsp.ai.providers.registry import get_spec

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
CHAT = [
    ("anthropic", "ANTHROPIC_KEY", os.environ.get("TSP_LIVE_ANTHROPIC_MODEL", "claude-haiku-5-5")),
    ("openai", "OPENAI_KEY", os.environ.get("TSP_LIVE_OPENAI_MODEL", "gpt-5-mini")),
]


def client_for(platform: str, key: str, model: str) -> LLMClient:
    client = LLMClient()
    client._router.add_provider(
        platform, get_spec(platform).build(), [ModelCandidate(platform, model, model)]
    )
    client._router.add_key(platform, key)
    return client


@pytest.mark.parametrize(("platform", "env", "model"), CHAT)
async def test_chat_and_tool_round_trip(platform, env, model):
    key = need_env(env)[env]
    client = client_for(platform, key, model)
    msgs = [{"role": "user", "content": "What is the weather in Pune? Use the tool."}]
    first = await client.chat.create(
        model=model, messages=msgs, tools=[TOOL], tool_choice="required", max_tokens=200
    )
    calls = first.choices[0].message.tool_calls
    assert first.choices[0].finish_reason == "tool_calls" and calls
    assert "pune" in json.loads(calls[0].function.arguments)["city"].lower()
    msgs += [
        {
            "role": "assistant",
            "content": first.choices[0].message.content,
            "tool_calls": [c.model_dump() for c in calls],
        },
        {"role": "tool", "tool_call_id": calls[0].id, "content": "31 C and sunny"},
    ]
    final = await client.chat.create(model=model, messages=msgs, tools=[TOOL], max_tokens=200)
    assert "31" in (final.choices[0].message.content or "")
    assert final.usage and final.usage.prompt_tokens > 0


@pytest.mark.parametrize(("platform", "env", "model"), CHAT)
async def test_streamed_usage_is_reported(platform, env, model):
    key = need_env(env)[env]
    client = client_for(platform, key, model)
    chunks = [
        c
        async for c in client.chat.create(
            model=model,
            messages=[{"role": "user", "content": "Say hi."}],
            stream=True,
            max_tokens=20,
        )
    ]
    assert any(c.usage and c.usage.completion_tokens for c in chunks)
