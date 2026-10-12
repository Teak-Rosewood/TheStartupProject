import json

import httpx
import pytest
from tsp.ai import EmbeddingDimensionError, LLMClient, SyncLLMClient
from tsp.ai.catalog import ModelCatalog, ModelResult, ProviderResult, Status
from tsp.ai.discovery import verify_provider
from tsp.ai.providers.base import ProviderHttpError
from tsp.ai.providers.bedrock import COHERE, TITAN, BedrockProvider
from tsp.ai.providers.cohere import CohereProvider
from tsp.ai.providers.google import GoogleProvider
from tsp.ai.providers.openai_compat import OpenAICompatProvider
from tsp.ai.providers.registry import PROVIDERS

from .stubs import EmbeddingStub, StubProvider, embedding_client

# -- providers: request shape and response normalisation, from recorded payloads --------------------


def mock(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_openai_compat_sends_dimensions_and_returns_the_body():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(
            url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content)
        )
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )

    p = OpenAICompatProvider("openai", "OpenAI", "https://api.example.com/v1")
    p._client = httpx.AsyncClient(transport=mock(handler))
    out = await p.embedding("sk-1", ["hello"], "text-embedding-3-small", {"dims": 2})
    assert seen["url"] == "https://api.example.com/v1/embeddings"
    assert seen["auth"] == "Bearer sk-1"
    assert seen["body"] == {"model": "text-embedding-3-small", "input": ["hello"], "dimensions": 2}
    assert out["data"][0]["embedding"] == [0.1, 0.2] and out["usage"]["total_tokens"] == 3


async def test_openai_compat_maps_errors_with_status_and_retry_after():
    def handler(req):
        return httpx.Response(
            429, json={"error": {"message": "slow down"}}, headers={"Retry-After": "7"}
        )

    p = OpenAICompatProvider("openai", "OpenAI", "https://api.example.com/v1")
    p._client = httpx.AsyncClient(transport=mock(handler))
    with pytest.raises(ProviderHttpError) as e:
        await p.embedding("k", ["x"], "m")
    assert e.value.status == 429 and e.value.retry_after_ms == 7000


async def test_cohere_maps_input_type_and_float_embeddings():
    seen = {}

    def handler(req):
        seen.update(
            path=req.url.path, auth=req.headers["authorization"], body=json.loads(req.content)
        )
        return httpx.Response(
            200,
            json={
                "embeddings": {"float": [[1.0, 2.0], [3.0, 4.0]]},
                "meta": {"billed_units": {"input_tokens": 9}},
            },
        )

    p = CohereProvider()
    p._http = httpx.AsyncClient(base_url="https://api.cohere.com/v1", transport=mock(handler))
    out = await p.embedding("ck", ["a", "b"], "embed-multilingual-v3.0", {"input_type": "query"})
    assert seen["path"] == "/v1/embed" and seen["auth"] == "Bearer ck"
    assert seen["body"]["input_type"] == "search_query" and seen["body"]["texts"] == ["a", "b"]
    assert [d["embedding"] for d in out["data"]] == [[1.0, 2.0], [3.0, 4.0]]
    assert out["usage"]["total_tokens"] == 9
    await p.close()


async def test_google_batch_embed_uses_task_type_and_output_dimensionality():
    seen = {}

    def handler(req):
        seen.update(
            path=req.url.path, key=req.headers["x-goog-api-key"], body=json.loads(req.content)
        )
        return httpx.Response(
            200, json={"embeddings": [{"values": [0.5, 0.6]}, {"values": [0.7, 0.8]}]}
        )

    p = GoogleProvider()
    p._http = httpx.AsyncClient(
        base_url="https://generativelanguage.googleapis.com/v1beta", transport=mock(handler)
    )
    out = await p.embedding("gk", ["a", "b"], "text-embedding-004", {"dims": 2})
    assert seen["path"].endswith("/models/text-embedding-004:batchEmbedContents")
    assert seen["key"] == "gk"
    reqs = seen["body"]["requests"]
    assert [r["taskType"] for r in reqs] == ["RETRIEVAL_DOCUMENT"] * 2
    assert all(r["outputDimensionality"] == 2 for r in reqs)
    assert reqs[0]["model"] == "models/text-embedding-004"
    assert [d["embedding"] for d in out["data"]] == [[0.5, 0.6], [0.7, 0.8]]
    await p.close()


class FakeBedrock:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def invoke_model(self, modelId, body, accept, contentType):
        self.calls.append((modelId, json.loads(body)))
        reply = self.replies.pop(0)

        class Body:
            def read(self_):
                return json.dumps(reply).encode()

        return {"body": Body()}


async def test_bedrock_titan_embeds_one_text_per_call_and_sums_tokens():
    p = BedrockProvider(region="ap-south-1")
    p._runtime = FakeBedrock(
        [
            {"embedding": [1.0, 0.0], "inputTextTokenCount": 2},
            {"embedding": [0.0, 1.0], "inputTextTokenCount": 3},
        ]
    )
    out = await p.embedding("iam", ["a", "b"], TITAN, {"dims": 2})
    assert [c[1] for c in p._runtime.calls] == [
        {"inputText": "a", "normalize": True, "dimensions": 2},
        {"inputText": "b", "normalize": True, "dimensions": 2},
    ]
    assert [d["embedding"] for d in out["data"]] == [[1.0, 0.0], [0.0, 1.0]]
    assert out["usage"]["total_tokens"] == 5


async def test_bedrock_cohere_batches_in_one_call():
    p = BedrockProvider()
    p._runtime = FakeBedrock([{"embeddings": [[1.0], [2.0]]}])
    out = await p.embedding("iam", ["a", "b"], COHERE, {"input_type": "query"})
    assert p._runtime.calls == [(COHERE, {"texts": ["a", "b"], "input_type": "search_query"})]
    assert len(out["data"]) == 2


async def test_bedrock_error_keeps_the_http_status():
    class Boom:
        def invoke_model(self, **_):
            err = Exception("throttled")
            err.response = {"ResponseMetadata": {"HTTPStatusCode": 429}}
            raise err

    p = BedrockProvider()
    p._runtime = Boom()
    with pytest.raises(ProviderHttpError) as e:
        await p.embedding("iam", ["a"], COHERE)
    assert e.value.status == 429


def test_bedrock_is_registered_and_keyless():
    assert PROVIDERS["bedrock"].keyless
    assert isinstance(PROVIDERS["bedrock"].build(), BedrockProvider)


# -- client: batching, order, dims, fallback -----------------------------------------------------


async def test_batches_are_split_and_order_is_preserved():
    p = EmbeddingStub()
    client = embedding_client(p)
    texts = ["a", "bb", "ccc", "dddd", "eeeee"]
    resp = await client.embeddings.create("embed-1", texts, batch_size=2)
    assert [c[1] for c in p.calls] == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert [d.index for d in resp.data] == [0, 1, 2, 3, 4]
    assert [d.embedding[0] for d in resp.data] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert resp.dims == 4 and resp.provider == "emb" and resp.model == "embed-1"
    assert resp.usage.total_tokens == 5


async def test_input_type_and_dims_are_passed_to_the_provider():
    p = EmbeddingStub(dims=3)
    client = embedding_client(p, dims=None)
    resp = await client.embeddings.create("embed-1", ["q"], dims=3, input_type="query")
    assert p.calls[0][2] == {"input_type": "query", "dims": 3} and resp.dims == 3


async def test_a_single_string_is_accepted():
    resp = await embedding_client(EmbeddingStub()).embeddings.create("embed-1", "hello")
    assert len(resp.data) == 1


@pytest.mark.parametrize("model", ["auto", "auto:smart", ""])
async def test_auto_routing_is_refused(model):
    with pytest.raises(ValueError, match="explicit model"):
        await embedding_client(EmbeddingStub()).embeddings.create(model, ["x"])


async def test_empty_input_is_refused():
    with pytest.raises(ValueError):
        await embedding_client(EmbeddingStub()).embeddings.create("embed-1", [])


async def test_unknown_model_is_a_404_not_a_guess():
    client = embedding_client(EmbeddingStub())
    with pytest.raises(ProviderHttpError) as e:
        await client.embeddings.create("other-model", ["x"])
    assert e.value.status == 404


async def test_chat_only_models_are_never_used_for_embeddings():
    client = LLMClient()
    client._router.add_provider("chatty", StubProvider("chatty"))
    client._router.add_key("chatty", "k")
    with pytest.raises(ProviderHttpError) as e:
        await client.embeddings.create("stub-1", ["x"])
    assert e.value.status == 404


async def test_dimension_mismatch_against_the_catalog_raises():
    client = embedding_client(EmbeddingStub(dims=4), dims=8)  # catalog says 8, provider gives 4
    with pytest.raises(EmbeddingDimensionError, match="4 dimensions, expected 8"):
        await client.embeddings.create("embed-1", ["x"])


async def test_dimension_mismatch_against_the_requested_dims_raises():
    client = embedding_client(EmbeddingStub(dims=4), dims=None)
    with pytest.raises(EmbeddingDimensionError):
        await client.embeddings.create("embed-1", ["x"], dims=2)  # provider ignored the request


async def test_ragged_vectors_in_one_batch_raise():
    class Ragged(EmbeddingStub):
        async def embedding(self, *a, **k):
            return {
                "data": [{"index": 0, "embedding": [1.0, 2.0]}, {"index": 1, "embedding": [1.0]}]
            }

    with pytest.raises(EmbeddingDimensionError):
        await embedding_client(Ragged(), dims=None).embeddings.create("embed-1", ["a", "b"])


async def test_fallback_goes_to_another_provider_serving_the_same_model():
    down = EmbeddingStub("down", error=ProviderHttpError("boom", status=503))
    up = EmbeddingStub("up")
    resp = await embedding_client(down, up).embeddings.create("embed-1", ["x"])
    assert resp.provider == "up" and len(down.calls) == 1


async def test_fallback_goes_to_the_next_key_of_the_same_provider():
    p = EmbeddingStub("one")
    client = embedding_client(p)
    client._router.add_key("one", "second-key")

    class FlakyFirstKey(EmbeddingStub):
        async def embedding(self, api_key, input, model_id, options=None):
            if api_key == "key-one":
                raise ProviderHttpError("rate limited", status=429)
            return await super().embedding(api_key, input, model_id, options)

    flaky = FlakyFirstKey("one")
    client._router.add_provider("one", flaky)
    resp = await client.embeddings.create("embed-1", ["x"])
    assert [c[0] for c in flaky.calls] == ["second-key"] and len(resp.data) == 1


async def test_bad_requests_are_not_retried_elsewhere():
    bad = EmbeddingStub("bad", error=ProviderHttpError("input too long", status=400))
    other = EmbeddingStub("other")
    with pytest.raises(ProviderHttpError, match="input too long"):
        await embedding_client(bad, other).embeddings.create("embed-1", ["x"])
    assert other.calls == []


async def test_all_providers_failing_raises_the_last_error():
    a = EmbeddingStub("a", error=ProviderHttpError("a down", status=503))
    b = EmbeddingStub("b", error=ProviderHttpError("b down", status=503))
    with pytest.raises(ProviderHttpError, match="b down"):
        await embedding_client(a, b).embeddings.create("embed-1", ["x"])


async def test_short_response_is_treated_as_a_provider_error():
    class Short(EmbeddingStub):
        async def embedding(self, *a, **k):
            return {"data": [{"index": 0, "embedding": [1.0]}]}

    ok = EmbeddingStub("ok", dims=1)
    resp = await embedding_client(Short("short"), ok, dims=1).embeddings.create(
        "embed-1", ["a", "b"]
    )
    assert resp.provider == "ok" and len(resp.data) == 2


async def test_a_provider_without_embeddings_is_skipped_as_a_bad_request():
    client = LLMClient()
    client._router.add_provider("plain", StubProvider("plain"))
    client._router.add_key("plain", "k")
    client._router.set_embedding_models("plain", {"embed-1": None})
    with pytest.raises(ProviderHttpError, match="does not support embeddings"):
        await client.embeddings.create("embed-1", ["x"])


def test_sync_client_exposes_embeddings():
    sync = SyncLLMClient()
    sync._client = embedding_client(EmbeddingStub())
    with sync:
        resp = sync.embeddings.create("embed-1", ["a"])
    assert resp.data[0].embedding[0] == 1.0


# -- catalog and verify -----------------------------------------------------------------------------


class ListingProvider(EmbeddingStub):
    async def list_models(self, api_key):
        return [{"id": "chat-1"}, {"id": "embed-1"}, {"id": "embed-broken"}]

    async def embedding(self, api_key, input, model_id, options=None):
        if model_id == "embed-broken":
            raise ProviderHttpError("model not found", status=404)
        return await super().embedding(api_key, input, model_id, options)


async def test_verify_probes_embedding_models_and_records_dims():
    from tsp.ai.providers.registry import ProviderSpec

    result = await verify_provider(ProviderSpec("emb", "Emb"), ListingProvider(dims=6), "k")
    assert [m.id for m in result.models] == ["chat-1"]  # embeddings never leak into chat routing
    by_id = {m.id: m for m in result.embedding_models}
    assert by_id["embed-1"].status is Status.OK and by_id["embed-1"].dims == 6
    assert by_id["embed-broken"].status is Status.MODEL_UNAVAILABLE
    assert result.ok_embedding_models == {"embed-1": 6}


def test_client_built_from_a_catalog_routes_embeddings_with_its_dims(tmp_path):
    catalog = ModelCatalog(
        providers={
            "ollama": ProviderResult(
                platform="ollama",
                key_status=Status.OK,
                embedding_models=[
                    ModelResult(id="nomic-embed-text", status=Status.OK, dims=768),
                    ModelResult(id="gone-embed", status=Status.MODEL_UNAVAILABLE),
                ],
            )
        }
    )
    path = catalog.save(tmp_path / "c.json")
    client = LLMClient(providers={"ollama": {"api_key": "no-key"}}, catalog=path)
    found = client._router.route_embedding("nomic-embed-text")
    assert found is not None and found[1] == 768
    assert client._router.route_embedding("gone-embed") is None


def test_configured_embedding_models_accept_a_list_or_a_dict():
    client = LLMClient(
        providers={
            "ollama": {"api_key": "x", "embedding_models": ["a-embed"]},
            "openai": {"api_key": "x", "embedding_models": {"text-embedding-3-small": 1536}},
        }
    )
    assert client._router.route_embedding("a-embed")[1] is None
    assert client._router.route_embedding("text-embedding-3-small")[1] == 1536
