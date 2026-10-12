"""Tests for the scoring module."""

import pytest
from tsp.ai.scoring import (
    BANDIT_PRESETS,
    beta_posterior,
    combine_score,
    expected_reliability,
    headroom_factor,
    intelligence_score,
    resolve_strategy,
    speed_score,
)


def test_resolve_strategy():
    """Test strategy resolution."""
    assert resolve_strategy("auto:smart") == "smartest"
    assert resolve_strategy("auto:fast") == "fastest"
    assert resolve_strategy("auto:balanced") == "balanced"
    assert resolve_strategy("smart") == "smartest"
    assert resolve_strategy("fast") == "fastest"
    assert resolve_strategy("priority") == "priority"
    assert resolve_strategy("reliable") == "reliable"


def test_beta_posterior():
    """Test Beta distribution parameters."""
    alpha, beta = beta_posterior(5, 2)
    assert alpha == 6.0
    assert beta == 3.0

    alpha, beta = beta_posterior(0, 0)
    assert alpha == 1.0
    assert beta == 1.0


def test_expected_reliability():
    """Test reliability calculation."""
    # Perfect record
    reliability = expected_reliability(10, 0)
    assert reliability > 0.9

    # Poor record
    reliability = expected_reliability(1, 9)
    assert reliability < 0.2

    # Even record
    reliability = expected_reliability(5, 5)
    assert 0.4 < reliability < 0.6


def test_speed_score():
    """Test speed scoring."""
    # Fast
    score = speed_score(100.0, 100.0)
    assert score > 0.5

    # Slow
    score = speed_score(10.0, 4000.0)
    assert score < 0.5

    # Unknown
    score = speed_score(0, 0)
    assert score == 0.5


def test_intelligence_score():
    """Test intelligence scoring."""
    assert intelligence_score(100) == 1.0
    assert intelligence_score(50) == 0.5
    assert intelligence_score(0) == 0.0


def test_headroom_factor():
    """Test headroom factor."""
    # Plenty of budget
    factor = headroom_factor(100, 10000)
    assert factor == 1.0

    # Running low
    factor = headroom_factor(9500, 10000)
    assert factor < 1.0

    # No limit
    factor = headroom_factor(100, 0)
    assert factor == 1.0


def test_combine_score():
    """Test score combination."""
    score = combine_score(
        reliability=0.9,
        speed=0.7,
        intelligence=0.8,
        weights=BANDIT_PRESETS["balanced"],
    )
    assert 0.0 <= score <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
