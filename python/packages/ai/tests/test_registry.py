import pytest
from tsp.ai import LLMClient, ModelCatalog, Status
from tsp.ai.catalog import ModelResult, ProviderResult


def test_from_env_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("# c\nGROQ_KEY='gsk_file'\n")
    client = LLMClient.from_env(env)
    assert "groq" in client._router._providers
    assert client._router._keys["groq"][0].api_key == "gsk_file"


def test_real_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_KEY", "from_env")
    env = tmp_path / ".env"
    env.write_text("GROQ_KEY=from_file\n")
    client = LLMClient.from_env(env)
    assert client._router._keys["groq"][0].api_key == "from_env"


def test_unknown_platform_without_base_url_raises():
    with pytest.raises(ValueError):
        LLMClient(providers={"nope": {"api_key": "x"}})


def test_unknown_platform_with_base_url_ok():
    client = LLMClient(providers={"nope": {"api_key": "x", "base_url": "http://x/v1"}})
    assert "nope" in client._router._providers


def test_catalog_registers_only_ok_models(tmp_path):
    catalog = ModelCatalog(
        providers={
            "groq": ProviderResult(
                platform="groq",
                key_status=Status.OK,
                models=[
                    ModelResult(id="good", status=Status.OK, latency_ms=120),
                    ModelResult(id="dead", status=Status.MODEL_UNAVAILABLE),
                ],
            )
        }
    )
    path = catalog.save(tmp_path / "c.json")
    client = LLMClient(providers={"groq": {"api_key": "k"}}, catalog=path)
    assert [m.model_id for m in client._router._models["groq"]] == ["good"]


def test_missing_and_corrupt_catalog_raise(tmp_path):
    with pytest.raises(FileNotFoundError, match="tsp-ai verify"):
        LLMClient(catalog=tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="corrupt"):
        LLMClient(catalog=bad)
