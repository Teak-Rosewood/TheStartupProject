import pytest
from tsp.ai import LLMClient, ModelCatalog, Status
from tsp.ai.catalog import ModelResult, ProviderResult
from tsp.ai.providers.base import BaseProvider


class Stub(BaseProvider):
    platform = "stub"
    name = "Stub"

    async def chat_completion(self, api_key, messages, model_id, options=None):
        return {"choices": [{"message": {"role": "assistant", "content": f"from {model_id}"}}]}

    def stream_chat_completion(self, *a, **k):
        raise NotImplementedError

    async def validate_key(self, api_key):
        return True


def client():
    c = LLMClient()
    c._router.add_provider("stub", Stub())
    c._router.add_key("stub", "k")
    return c


def catalog(*ok):
    return ModelCatalog(
        providers={
            "stub": ProviderResult(
                platform="stub",
                key_status=Status.OK,
                models=[
                    ModelResult(
                        id=i,
                        status=Status.OK if i in ok else Status.MODEL_UNAVAILABLE,
                        latency_ms=50,
                    )
                    for i in ("m1", "m2", "m3")
                ],
            )
        }
    )


async def test_catalog_replaces_routable_models_at_runtime():
    c = client()
    assert c.get_models() == []
    assert c.use_catalog(catalog("m1", "m2")) == 2
    assert sorted(m["model_id"] for m in c.get_models()) == ["m1", "m2"]
    r = await c.chat.create(model="auto", messages=[{"role": "user", "content": "hi"}])
    assert r.choices[0].message.content.startswith("from m")
    assert c.use_catalog(catalog("m3")) == 1  # a later catalog fully replaces the earlier one
    assert [m["model_id"] for m in c.get_models()] == ["m3"]


def test_a_provider_missing_from_the_catalog_is_not_routed_to():
    c = client()
    c.use_catalog(catalog("m1"))
    assert c.use_catalog(ModelCatalog()) == 0 and c.get_models() == []


def test_unknown_platform_is_an_error():
    with pytest.raises(KeyError):
        client()._router.set_models("nope", [])
