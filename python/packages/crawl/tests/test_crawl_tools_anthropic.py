"""The live-tools loop works the same on Claude as on OpenAI-compatible providers."""

import json
from datetime import UTC, datetime

import httpx
from tsp.ai import LLMClient, ModelCandidate
from tsp.ai.providers.anthropic import AnthropicProvider
from tsp.crawl import LiveTools, Page, run_with_tools


class OnePage:
    async def fetch(self, url, *, etag=None, last_modified=None):
        return Page(
            url,
            url,
            200,
            "Doc",
            "The answer is 42.",
            [],
            "h",
            None,
            None,
            datetime.now(UTC),
            "text/html",
            "fake",
        )


async def test_run_with_tools_on_anthropic():
    bodies: list[dict] = []
    replies = [
        {
            "id": "m1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "fetch_url",
                    "input": {"url": "https://docs.example.com/a"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        {
            "id": "m2",
            "content": [{"type": "text", "text": "42"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 30, "output_tokens": 1},
        },
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    p = AnthropicProvider()
    p._http = httpx.AsyncClient(
        base_url="https://api.anthropic.com", transport=httpx.MockTransport(handler)
    )
    client = LLMClient()
    client._router.add_provider(
        "anthropic", p, [ModelCandidate("anthropic", "claude-x", "claude-x")]
    )
    client._router.add_key("anthropic", "k")

    resp = await run_with_tools(
        client, LiveTools(OnePage()), "claude-x", [{"role": "user", "content": "what is it?"}]
    )

    assert resp.choices[0].message.content == "42"
    assert {t["name"] for t in bodies[0]["tools"]} == {"fetch_url"}
    assert all("input_schema" in t for t in bodies[0]["tools"])
    second = bodies[1]["messages"]
    assert second[-2]["content"][0]["type"] == "tool_use"
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert "42" in result["content"]
