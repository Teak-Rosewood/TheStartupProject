import pytest
from tsp.ai import ProviderHttpError, Status
from tsp.ai.discovery import classify, verify, verify_provider
from tsp.ai.providers.registry import ProviderSpec

from .stubs import StubProvider, stub_client


class Listing(StubProvider):
    def __init__(self, entries, failing=None, **kw):
        super().__init__(**kw)
        self.entries = entries
        self.failing = failing or {}

    async def list_models(self, api_key):
        return self.entries

    async def chat_completion(self, api_key, messages, model_id, options=None):
        if model_id in self.failing:
            raise self.failing[model_id]
        return await super().chat_completion(api_key, messages, model_id, options)


@pytest.mark.parametrize(
    "status,msg,expected",
    [
        (401, "bad key", Status.AUTH),
        (402, "pay up", Status.QUOTA_OR_BILLING),
        (403, "insufficient balance", Status.QUOTA_OR_BILLING),
        (404, "nope", Status.MODEL_UNAVAILABLE),
        (400, "model not found", Status.MODEL_UNAVAILABLE),
        (429, "slow down", Status.RATE_LIMITED),
        (500, "boom", Status.SERVER_ERROR),
        (400, "bad json", Status.BAD_REQUEST),
    ],
)
def test_classify(status, msg, expected):
    assert classify(ProviderHttpError(msg, status=status)) is expected


async def test_verify_provider_filters_non_chat_and_classifies():
    provider = Listing(
        [{"id": "chat-a"}, {"id": "chat-b"}, {"id": "whisper-large"}, {"id": "text-embed-1"}],
        failing={"chat-b": ProviderHttpError("model not found", status=404)},
    )
    result = await verify_provider(ProviderSpec("stub", "Stub"), provider, "k")
    assert result.listed == 2
    by_id = {m.id: m.status for m in result.models}
    assert by_id == {"chat-a": Status.OK, "chat-b": Status.MODEL_UNAVAILABLE}
    assert [m.id for m in result.ok_models] == ["chat-a"]


async def test_free_only_probes_free_models_only():
    provider = Listing([{"id": "paid-1"}, {"id": "cheap:free"}])
    result = await verify_provider(ProviderSpec("stub", "Stub"), provider, "k", free_only=True)
    assert [m.id for m in result.models] == ["cheap:free"]


async def test_fallback_models_used_when_listing_unsupported():
    # StubProvider.list_models is the base default and raises NotImplementedError.
    spec = ProviderSpec("stub", "Stub", fallback_models=("fb-1",))
    result = await verify_provider(spec, StubProvider(), "k")
    assert [m.id for m in result.ok_models] == ["fb-1"]


async def test_auth_failure_on_listing_is_reported():
    class Denied(StubProvider):
        async def list_models(self, api_key):
            raise ProviderHttpError("unauthorized", status=401)

    result = await verify_provider(ProviderSpec("stub", "Stub"), Denied(), "k")
    assert result.key_status is Status.AUTH and not result.models


async def test_verify_roundtrip_feeds_client(tmp_path):
    client = stub_client(Listing([{"id": "chat-a"}]))
    catalog = await verify(client)
    path = catalog.save(tmp_path / "c.json")
    from tsp.ai import ModelCatalog

    assert ModelCatalog.load(path).providers["stub"].ok_models[0].id == "chat-a"
