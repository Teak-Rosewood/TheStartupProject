"""Tests for the router module."""

import pytest
from tsp.ai.router import LLMRouter, ModelCandidate


def test_add_provider_and_model():
    """Test adding a provider with models."""
    router = LLMRouter()

    # Mock provider
    class MockProvider:
        platform = "test"
        name = "Test Provider"

    provider = MockProvider()
    models = [
        ModelCandidate(platform="test", model_id="test-1", display_name="Test 1"),
        ModelCandidate(platform="test", model_id="test-2", display_name="Test 2"),
    ]
    router.add_provider("test", provider, models)

    assert len(router._models["test"]) == 2
    assert router._providers["test"] == provider


def test_add_key():
    """Test adding an API key."""
    router = LLMRouter()
    key = router.add_key("test", "sk-test123", label="main")

    assert key.api_key == "sk-test123"
    assert key.label == "main"
    assert key.platform == "test"
    assert len(router._keys["test"]) == 1


def test_get_models():
    """Test listing all models."""
    router = LLMRouter()

    class MockProvider:
        platform = "test"
        name = "Test Provider"

    provider = MockProvider()
    models = [
        ModelCandidate(platform="test", model_id="test-1", display_name="Test 1"),
    ]
    router.add_provider("test", provider, models)

    model_list = router.get_models()
    assert len(model_list) == 1
    assert model_list[0]["model_id"] == "test-1"


def test_remove_key():
    """Test removing a key."""
    router = LLMRouter()
    key1 = router.add_key("test", "sk-key1")
    router.add_key("test", "sk-key2")

    assert len(router._keys["test"]) == 2
    router.remove_key("test", key1.key_id)
    assert len(router._keys["test"]) == 1


def test_rate_limiter_integration():
    """Test rate limiter tracking."""
    LLMRouter()
    from tsp.ai.ratelimit import RateLimitTracker

    tracker = RateLimitTracker()

    # Record some requests
    for _i in range(5):
        tracker.record_request("test", "model-1", "key-1")

    # Should be able to make more requests (no limit set)
    assert tracker.can_make_request("test", "model-1", "key-1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
