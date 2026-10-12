import pytest
from tsp.ai import SyncLLMClient
from tsp.ai.providers.base import ProviderHttpError

from .stubs import StubProvider, stub_client


def make_sync(provider=None) -> SyncLLMClient:
    sync = SyncLLMClient()
    sync._client = stub_client(provider)
    return sync


def test_create_returns_response():
    with make_sync() as client:
        resp = client.chat.create(model="auto", messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "pong"


def test_stream_returns_plain_iterator():
    with make_sync() as client:
        chunks = list(
            client.chat.create(
                model="auto", messages=[{"role": "user", "content": "hi"}], stream=True
            )
        )
    assert "".join(c.choices[0].delta.content or "" for c in chunks) == "pong"


def test_get_models_and_close_closes_providers():
    provider = StubProvider()
    client = make_sync(provider)
    assert client.get_models()[0]["model_id"] == "stub-1"
    client.close()
    assert provider.closed
    client.close()  # idempotent


def test_errors_propagate():
    provider = StubProvider(error=ProviderHttpError("bad key", status=401))
    with make_sync(provider) as client, pytest.raises(ProviderHttpError):
        client.chat.create(model="auto", messages=[{"role": "user", "content": "hi"}])
