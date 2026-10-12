"""Tests for the rate limiter module."""

import pytest
from tsp.ai.ratelimit import RateLimitTracker


def test_rate_limit_tracker():
    """Test basic rate limiting."""
    tracker = RateLimitTracker()

    # No limit set - should always allow
    assert tracker.can_make_request("test", "model-1", "key-1", rpm_limit=None)
    assert tracker.can_use_tokens("test", "model-1", "key-1", tpm_limit=None)


def test_rpm_tracking():
    """Test requests per minute tracking."""
    tracker = RateLimitTracker()

    # Set RPM limit
    limit = 5
    for _i in range(5):
        tracker.record_request("test", "model-1", "key-1")

    # Should now be at limit
    assert not tracker.can_make_request("test", "model-1", "key-1", rpm_limit=limit)


def test_cooldown():
    """Test cooldown setting."""
    tracker = RateLimitTracker()

    import time

    expires_at = time.time() * 1000 + 60_000  # 1 minute from now
    tracker.set_cooldown("test", "model-1", "key-1", expires_at)

    cooldown = tracker.get_cooldown("test", "model-1", "key-1")
    assert cooldown is not None
    assert cooldown.platform == "test"


def test_format_reset_eta():
    """Test human-readable eta formatting."""
    tracker = RateLimitTracker()
    import time

    now = time.time() * 1000
    eta = tracker.format_reset_eta(now + 45_000)
    assert "s" in eta


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
