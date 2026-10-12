import json

import httpx
import pytest
from tsp.ai import BATCH_DISCOUNT, BatchRequest, LLMClient, ModelCandidate, ProviderHttpError
from tsp.ai.providers.anthropic import AnthropicProvider

RESULTS = "\n".join(
    json.dumps(x)
    for x in [
        {"custom_id": "a", "result": {"type": "succeeded", "message": {
            "id": "m1", "model": "claude-x", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Summary A"}],
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50}}}},
        {"custom_id": "b", "result": {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error", "message": "too long"}}}},
        {"custom_id": "c", "result": {"type": "expired"}},
    ]
)  # fmt: skip


def client_with(handler) -> tuple[LLMClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req):
        seen.append(req)
        return handler(req)

    p = AnthropicProvider()
    p._http = httpx.AsyncClient(
        base_url="https://api.anthropic.com", transport=httpx.MockTransport(record)
    )
    c = LLMClient()
    c._router.add_provider("anthropic", p, [ModelCandidate("anthropic", "claude-x", "claude-x")])
    c._router.add_key("anthropic", "sk-ant")
    return c, seen


def api(state="ended"):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "POST":
            return httpx.Response(
                200, json={"id": "msgbatch_1", "processing_status": "in_progress"}
            )
        if path.endswith("/results"):
            return httpx.Response(200, text=RESULTS)
        return httpx.Response(
            200,
            json={
                "id": "msgbatch_1",
                "processing_status": state,
                "request_counts": {"succeeded": 1, "errored": 1},
            },
        )

    return handler


async def test_submit_sends_each_request_with_its_own_params():
    client, seen = client_with(api())
    bid = await client.batches.submit(
        [
            BatchRequest(
                "a",
                "claude-x",
                [{"role": "system", "content": "S"}, {"role": "user", "content": "one"}],
                {"max_tokens": 200},
            ),
            BatchRequest("b", "claude-x", [{"role": "user", "content": "two"}]),
        ]
    )
    body = json.loads(seen[0].content)
    assert bid == "msgbatch_1" and seen[0].url.path == "/v1/messages/batches"
    assert seen[0].headers["x-api-key"] == "sk-ant"
    assert [r["custom_id"] for r in body["requests"]] == ["a", "b"]
    assert (
        body["requests"][0]["params"]["max_tokens"] == 200
        and body["requests"][0]["params"]["system"][0]["text"] == "S"
    )


async def test_poll_and_results_map_each_outcome():
    client, _ = client_with(api())
    st = await client.batches.poll("msgbatch_1")
    assert st.done and st.counts["succeeded"] == 1
    got = {r.custom_id: r for r in await client.batches.results("msgbatch_1")}
    assert (
        got["a"].status == "succeeded"
        and got["a"].response.choices[0].message.content == "Summary A"
    )
    assert (
        got["a"].response.usage.prompt_tokens == 150
        and got["a"].response.usage.cache_read_tokens == 50
    )
    assert (got["b"].status, got["b"].error) == ("errored", "too long")
    assert got["c"].status == "expired" and got["c"].response is None


async def test_results_of_a_running_batch_raise():
    client, _ = client_with(api("in_progress"))
    assert not (await client.batches.poll("x")).done
    with pytest.raises(ProviderHttpError, match="not ended") as e:
        await client.batches.results("x")
    assert e.value.status == 409


async def test_validation_and_missing_provider():
    client, _ = client_with(api())
    with pytest.raises(ValueError):
        await client.batches.submit([])
    with pytest.raises(ValueError, match="unique"):
        await client.batches.submit([BatchRequest("a", "m", []), BatchRequest("a", "m", [])])
    with pytest.raises(ProviderHttpError, match="Anthropic") as e:
        await LLMClient().batches.submit([BatchRequest("a", "m", [])])
    assert e.value.status == 404
    assert BATCH_DISCOUNT == 0.5
