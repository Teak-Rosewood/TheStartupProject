import json

import httpx
import pytest
from pydantic import BaseModel
from tsp.ai import Doc, LLMClient
from tsp.ai.providers.anthropic import AnthropicProvider

from .stubs import StubProvider


def anthropic_client(handler) -> tuple[LLMClient, list[dict]]:
    seen: list[dict] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return handler(req)

    p = AnthropicProvider()
    p._http = httpx.AsyncClient(
        base_url="https://api.anthropic.com", transport=httpx.MockTransport(record)
    )
    client = LLMClient()
    from tsp.ai import ModelCandidate

    client._router.add_provider(
        "anthropic", p, [ModelCandidate("anthropic", "claude-x", "claude-x")]
    )
    client._router.add_key("anthropic", "sk-ant")
    return client, seen


def message(text="hello", citations=None, usage=None):
    block = {"type": "text", "text": text}
    if citations:
        block["citations"] = citations
    return httpx.Response(
        200,
        json={
            "id": "m1",
            "content": [block],
            "stop_reason": "end_turn",
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
        },
    )


async def test_system_messages_become_the_top_level_system_field():
    client, seen = anthropic_client(lambda r: message())
    await client.chat.create(
        model="claude-x",
        messages=[{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
    )
    assert seen[0]["system"] == [{"type": "text", "text": "Be brief."}]
    assert [m["role"] for m in seen[0]["messages"]] == ["user"]


async def test_cache_marks_breakpoints_and_usage_reports_cache_tokens():
    usage = {
        "input_tokens": 20,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 100,
        "output_tokens": 7,
    }
    client, seen = anthropic_client(lambda r: message(usage=usage))
    resp = await client.chat.create(
        model="claude-x",
        messages=[
            {"role": "system", "content": "Long instructions"},
            {"role": "user", "content": "context"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "question"},
        ],
        cache=["system", 0],
    )
    sent = seen[0]
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in json.dumps(sent["messages"][1:])  # only the marked message
    u = resp.usage
    assert (u.prompt_tokens, u.cache_read_tokens, u.cache_write_tokens, u.completion_tokens) == (
        1020,
        900,
        100,
        7,
    )
    assert u.total_tokens == 1027


async def test_bad_cache_targets_are_rejected():
    client, _ = anthropic_client(lambda r: message())
    with pytest.raises(Exception, match="cache"):
        await client.chat.create(
            model="claude-x", messages=[{"role": "user", "content": "x"}], cache=[5]
        )


async def test_documents_with_citations_are_sent_as_document_blocks_and_mapped_back():
    cites = [
        {
            "type": "char_location",
            "cited_text": "Whisk the eggs.",
            "document_index": 1,
            "start_char_index": 4,
            "end_char_index": 19,
        }
    ]
    client, seen = anthropic_client(lambda r: message("Whisk first.", citations=cites))
    resp = await client.chat.create(
        model="claude-x",
        messages=[{"role": "user", "content": "How do I start?"}],
        documents=[
            Doc(id="r1", text="Oven recipe", title="Roast"),
            Doc(id="r2", text="Step: Whisk the eggs. Then bake."),
        ],
        citations=True,
    )
    blocks = seen[0]["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["document", "document", "text"]
    assert blocks[0]["title"] == "Roast" and blocks[1]["title"] == "r2"
    assert blocks[1]["citations"] == {"enabled": True}
    assert resp.citations[0].model_dump() == {
        "doc_id": "r2",
        "start": 4,
        "end": 19,
        "quoted_text": "Whisk the eggs.",
    }


async def test_other_providers_get_documents_as_quoted_text_without_citations():
    client = LLMClient()
    stub = StubProvider("stub")
    seen = []
    orig = stub.chat_completion

    async def spy(api_key, messages, model_id, options=None):
        seen.append((messages, options))
        return await orig(api_key, messages, model_id, options)

    stub.chat_completion = spy
    from tsp.ai import ModelCandidate

    client._router.add_provider("stub", stub, [ModelCandidate("stub", "m", "m")])
    client._router.add_key("stub", "k")
    resp = await client.chat.create(
        model="m", messages=[{"role": "user", "content": "Question?"}],
        documents=[Doc(id="d1", text="Body text")], citations=True, cache=["system"],
    )  # fmt: skip
    msgs, opts = seen[0]
    assert '<document id="d1"' in msgs[0]["content"] and msgs[0]["content"].endswith("Question?")
    assert "documents" not in opts and "cache" not in opts and resp.citations is None


async def test_response_schema_returns_a_validated_model_with_one_repair():
    class Verdict(BaseModel):
        label: str

    replies = iter([message("not json"), message('{"label": "ok"}')])
    client, seen = anthropic_client(lambda r: next(replies))
    got = await client.chat.create(
        model="claude-x", messages=[{"role": "user", "content": "rate"}], response_schema=Verdict
    )
    assert got == Verdict(label="ok") and len(seen) == 2
    with pytest.raises(ValueError, match="stream"):
        client.chat.create(model="claude-x", messages=[], response_schema=Verdict, stream=True)
