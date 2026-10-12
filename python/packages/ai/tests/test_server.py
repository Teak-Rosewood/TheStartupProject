import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from tsp.ai.providers.base import ProviderHttpError  # noqa: E402
from tsp.ai.server import create_app  # noqa: E402

from .stubs import StubProvider, stub_client  # noqa: E402

BODY = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
AUTH = {"Authorization": "Bearer secret"}


def app_client(key=None, provider=None):
    return TestClient(create_app(stub_client(provider), api_key=key))


def test_health_is_open_and_v1_requires_key():
    c = app_client("secret")
    assert c.get("/health").status_code == 200
    assert c.post("/v1/chat/completions", json=BODY).status_code == 401
    assert (
        c.post(
            "/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer no"}
        ).status_code
        == 401
    )
    assert c.get("/v1/models").status_code == 401


def test_chat_completion_json():
    r = app_client("secret").post("/v1/chat/completions", json=BODY, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "pong"


def test_no_key_configured_means_open():
    assert app_client().post("/v1/chat/completions", json=BODY).status_code == 200


def test_stream_ends_with_done():
    r = app_client().post("/v1/chat/completions", json={**BODY, "stream": True})
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [f[len("data: ") :] for f in r.text.split("\n\n") if f]
    assert frames[-1] == "[DONE]"
    text = "".join(json.loads(f)["choices"][0]["delta"].get("content") or "" for f in frames[:-1])
    assert text == "pong"


def test_models_listing():
    r = app_client().get("/v1/models")
    assert r.json()["data"][0]["id"] == "stub-1"


def test_invalid_body_is_400_openai_style():
    r = app_client().post("/v1/chat/completions", json={"messages": []})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_upstream_error_maps_status():
    provider = StubProvider(error=ProviderHttpError("quota gone", status=402))
    r = app_client(provider=provider).post("/v1/chat/completions", json=BODY)
    assert r.status_code >= 400
    assert "error" in r.json()
