import random

# Strategy Presets
BANDIT_PRESETS = {
    "priority": {"reliability": 1.0, "speed": 0.0, "intelligence": 0.0},
    "balanced": {"reliability": 0.50, "speed": 0.25, "intelligence": 0.25},
    "smartest": {"reliability": 0.35, "speed": 0.10, "intelligence": 0.55},
    "fastest": {"reliability": 0.35, "speed": 0.55, "intelligence": 0.10},
    "reliable": {"reliability": 0.70, "speed": 0.15, "intelligence": 0.15},
}

# Alias resolution
STRATEGY_ALIASES = {
    "smart": "smartest",
    "fast": "fastest",
    "speed": "fastest",
    "cheap": "balanced",
    "cheapest": "balanced",
    "budget": "balanced",
    "reliable": "reliable",
    "balanced": "balanced",
    "priority": "priority",
}


def resolve_strategy(strategy: str) -> str:
    s = strategy.lower().strip()
    if s.startswith("auto:"):
        s = s[5:]
    return STRATEGY_ALIASES.get(s, "balanced")


def beta_posterior(
    successes: int, failures: int, prior_alpha: float = 1.0, prior_beta: float = 1.0
) -> tuple[float, float]:
    """Compute Beta distribution parameters for Thompson sampling."""
    return prior_alpha + max(0, successes), prior_beta + max(0, failures)


def sample_beta(alpha: float, beta_param: float) -> float:
    """Sample from a Beta distribution."""
    return random.betavariate(alpha, beta_param)


def expected_reliability(
    successes: int, failures: int, prior_alpha: float = 1.0, prior_beta: float = 1.0
) -> float:
    """Expected value of Beta posterior: alpha / (alpha + beta)."""
    a, b = beta_posterior(successes, failures, prior_alpha, prior_beta)
    return a / (a + b)


def speed_score(tokens_per_sec: float, ttfb_ms: float) -> float:
    """Normalized speed score between 0 and 1."""
    if tokens_per_sec <= 0 and ttfb_ms <= 0:
        return 0.5  # neutral default

    throughput_score = min(1.0, max(0.0, tokens_per_sec / 100.0))
    ttfb_score = max(0.0, 1.0 - (ttfb_ms / 5000.0)) if ttfb_ms > 0 else 0.5

    return 0.6 * throughput_score + 0.4 * ttfb_score


def intelligence_score(intelligence_rank: int) -> float:
    """Normalize intelligence rank (1-100) to 0.0-1.0."""
    return min(1.0, max(0.0, intelligence_rank / 100.0))


def headroom_factor(used_tokens: int, limit_tokens: int) -> float:
    """Compute headroom factor based on remaining token budget."""
    if limit_tokens <= 0:
        return 1.0
    remaining = max(0, limit_tokens - used_tokens)
    fraction = remaining / limit_tokens
    if fraction > 0.2:
        return 1.0
    return max(0.1, fraction / 0.2)


def combine_score(
    reliability: float,
    speed: float,
    intelligence: float,
    weights: dict[str, float],
    headroom: float = 1.0,
    penalty: float = 1.0,
) -> float:
    """Combine components into a final score."""
    w_rel = weights.get("reliability", 0.5)
    w_spd = weights.get("speed", 0.25)
    w_intel = weights.get("intelligence", 0.25)

    base = (reliability * w_rel) + (speed * w_spd) + (intelligence * w_intel)
    return base * headroom * penalty
