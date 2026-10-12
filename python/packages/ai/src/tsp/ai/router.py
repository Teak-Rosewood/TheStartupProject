"""
Router: Model selection, scoring, and key management.

This module is responsible for:
1. Maintaining a registry of providers, models, and API keys
2. Scoring and ranking candidate models based on reliability, speed, and intelligence
3. Selecting the best key for a request based on rate limits and availability
4. Supporting routing strategies (auto, auto:smart, auto:fast, etc.)
"""

import threading
import time
from dataclasses import dataclass
from typing import Any

from .providers.base import BaseProvider
from .ratelimit import RateLimitTracker
from .scoring import (
    BANDIT_PRESETS,
    combine_score,
    expected_reliability,
    intelligence_score,
    resolve_strategy,
    speed_score,
)


@dataclass
class ModelCandidate:
    """A candidate model that can be routed to."""

    platform: str
    model_id: str
    display_name: str
    intelligence_rank: int = 50
    speed_rank: int = 50
    supports_vision: bool = False
    supports_tools: bool = True
    context_window: int = 128000
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    tpm_limit: int | None = None
    tpd_limit: int | None = None
    monthly_budget: int | None = None
    # Key-specific state
    successes: int = 0
    failures: int = 0
    avg_tokens_per_sec: float = 0.0
    avg_ttfb_ms: float = 0.0
    score: float = 0.5
    disabled: bool = False
    benched: float = 0.0  # timestamp when benched (0 = not benched)


@dataclass
class KeyEntry:
    """An API key for a provider."""

    key_id: str
    platform: str
    api_key: str
    label: str | None = None
    enabled: bool = True
    model_scope: list[str] | None = None  # None = all models
    base_url: str | None = None
    # Quota tracking
    tokens_used_today: int = 0
    requests_today: int = 0
    last_request_time: float = 0.0


@dataclass
class RouteResult:
    """The result of route selection."""

    provider: BaseProvider
    model_id: str
    display_name: str
    key: KeyEntry
    platform: str
    score: float
    benched: bool = False


_NON_CHAT_TOKENS = (
    "whisper",
    "asr",
    "embed",
    "tts",
    "audio",
    "image",
    "video",
    "moderat",
    "rerank",
    "transcri",
    "speech",
    "dall-e",
    "diffusion",
    "flux",
    "sdxl",
    "ocr",
)


def is_embedding_model(model_id: str) -> bool:
    return "embed" in model_id.lower()


def is_chat_model(model_id: str) -> bool:
    """Heuristic: False for ids that name an embedding/audio/image/moderation model."""
    mid = model_id.lower()
    return not any(tok in mid for tok in _NON_CHAT_TOKENS)


def pick_model(models: list[ModelCandidate], capability: str = "chat") -> ModelCandidate:
    """Select a concrete model from a list based on the requested capability.

    Currently supported capabilities:
    - "chat": any model suitable for chat completions.
    - "completion": plain text completions.
    - "embedding": vector embeddings.

    The function filters out models whose ``model_id`` appears to belong to a
    different modality (e.g., ``whisper`` for audio, ``embed`` for embeddings,
    ``image``/``video`` for media). If no model matches the capability the
    first model in the list is returned as a fallback.
    """
    if not models:
        raise ValueError("No models available for selection")

    def matches(m: ModelCandidate) -> bool:
        mid = m.model_id.lower()
        if capability == "chat":
            return is_chat_model(mid)
        if capability == "embedding":
            return "embed" in mid or "embedding" in mid
        if capability == "completion":
            # Completion models often have "completion" or lack chat‑specific tags
            return "completion" in mid or "text" in mid
        return True

    filtered = list(filter(matches, models))
    return filtered[0] if filtered else models[0]


class LLMRouter:
    """
    Main router that manages providers, models, and key selection.

    Usage:
        router = LLMRouter()
        router.add_provider("openai", OpenAIProvider(), {
            "models": [
                ModelCandidate("gpt-4o", "GPT-4o"),
                ModelCandidate("gpt-4o-mini", "GPT-4o Mini"),
            ]
        })
        router.add_key("openai", "sk-...", label="main")

        route = router.route_request(
            model="auto",
            messages=[{"role": "user", "content": "hi"}],
        )
    """

    def __init__(self, strategy: str = "balanced", max_retries: int = 20):
        self._lock = threading.RLock()
        self._providers: dict[str, BaseProvider] = {}
        self._models: dict[str, list[ModelCandidate]] = {}  # platform -> models
        self._keys: dict[str, list[KeyEntry]] = {}  # platform -> keys
        self._strategy = strategy
        self._rate_limiter = RateLimitTracker()
        self._max_retries = max_retries
        self._penalties: dict[int, float] = {}  # model_idx -> penalty
        # platform -> {embedding model id: known dims}. Kept apart from chat models so "auto"
        # routing can never pick an embedding model.
        self._embedding_models: dict[str, dict[str, int | None]] = {}

    def add_provider(
        self, platform: str, provider: BaseProvider, models: list[ModelCandidate] | None = None
    ):
        """Register a provider with its models."""
        with self._lock:
            self._providers[platform] = provider
            self._models[platform] = models or []
            if platform not in self._keys:
                self._keys[platform] = []

    def set_models(self, platform: str, models: list[ModelCandidate]) -> None:
        """Replace one provider's routable models (used when a fresh catalog arrives at runtime)."""
        with self._lock:
            if platform not in self._providers:
                raise KeyError(platform)
            self._models[platform] = list(models)

    def set_embedding_models(self, platform: str, models: dict[str, int | None]) -> None:
        """Replace one provider's embedding models (id -> dims when known)."""
        with self._lock:
            if platform not in self._providers:
                raise KeyError(platform)
            self._embedding_models[platform] = dict(models)

    def route_embedding(
        self, model: str, exclude: frozenset[str] | set[str] = frozenset()
    ) -> tuple[RouteResult, int | None] | None:
        """Pick a provider and key serving exactly ``model``, skipping keys in ``exclude``.

        No scoring and no "closest match": a different model would give incompatible vectors.
        Returns the route and the dims recorded for the model, or None.
        """
        with self._lock:
            for platform, models in self._embedding_models.items():
                provider = self._providers.get(platform)
                if provider is None or model not in models:
                    continue
                key = self._select_key(platform, model, {}, exclude)
                if key:
                    route = RouteResult(provider, model, model, key, platform, score=0.0)
                    return route, models[model]
        return None

    def add_key(
        self,
        platform: str,
        api_key: str,
        label: str | None = None,
        model_scope: list[str] | None = None,
        base_url: str | None = None,
    ) -> KeyEntry:
        """Add an API key for a provider."""
        key_id = f"{platform}_{int(time.time() * 1000)}_{id(api_key) % 10000}"
        key = KeyEntry(
            key_id=key_id,
            platform=platform,
            api_key=api_key,
            label=label,
            model_scope=model_scope,
            base_url=base_url,
        )
        with self._lock:
            if platform not in self._keys:
                self._keys[platform] = []
            self._keys[platform].append(key)
        return key

    def remove_key(self, platform: str, key_id: str):
        """Remove a key from the router."""
        with self._lock:
            if platform in self._keys:
                self._keys[platform] = [k for k in self._keys[platform] if k.key_id != key_id]

    @property
    def rate_limiter(self) -> RateLimitTracker:
        return self._rate_limiter

    def get_models(self) -> list[dict[str, Any]]:
        """Return a list of all registered models with their status."""
        with self._lock:
            models = []
            for platform, platform_models in self._models.items():
                for model in platform_models:
                    models.append(
                        {
                            "platform": platform,
                            "model_id": model.model_id,
                            "display_name": model.display_name,
                            "intelligence_rank": model.intelligence_rank,
                            "speed_rank": model.speed_rank,
                            "supports_tools": model.supports_tools,
                            "supports_vision": model.supports_vision,
                            "context_window": model.context_window,
                            "disabled": model.disabled,
                        }
                    )
            return models

    def route_request(
        self,
        model: str | None,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
        capability: str = "chat",
    ) -> RouteResult | None:
        """
        Select the best provider/model/key for a request.

        Args:
            model: Model ID or routing strategy (e.g., "auto", "auto:smart")
            messages: Chat messages
            options: Additional options (temperature, max_tokens, etc.)

        Returns:
            RouteResult if a route is found, None otherwise
        """
        with self._lock:
            return self._route_request_internal(model, messages, options, capability)

    def _route_request_internal(
        self,
        model: str | None,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
        capability: str = "chat",
    ) -> RouteResult | None:
        """Internal routing logic."""
        options = options or {}

        # Check if routing is automatic
        if not model or model == "auto":
            candidates = self._get_all_candidates()
        else:
            candidates = self._get_candidates_for_model(model)

        if not candidates:
            return None

        # Apply capability filtering
        def matches(m: ModelCandidate) -> bool:
            mid = m.model_id.lower()
            if capability == "chat":
                return not any(
                    tok in mid for tok in ["whisper", "embed", "audio", "image", "video"]
                )
            if capability == "embedding":
                return "embed" in mid or "embedding" in mid
            if capability == "completion":
                return "completion" in mid or "text" in mid
            return True

        filtered = [m for m in candidates if matches(m)]
        if not filtered:
            # fallback to original list if nothing matches
            filtered = candidates

        # Apply routing strategy
        strategy = resolve_strategy(model) if model else "balanced"
        weights = BANDIT_PRESETS.get(strategy, BANDIT_PRESETS["balanced"])

        # Score and rank candidates
        scored = []
        for idx, candidate in enumerate(filtered):
            # Skip disabled/benched models
            if candidate.disabled:
                continue
            if candidate.benched > 0 and candidate.benched > time.time():
                continue

            reliability = expected_reliability(candidate.successes, candidate.failures)
            spd = speed_score(candidate.avg_tokens_per_sec, candidate.avg_ttfb_ms)
            intel = intelligence_score(candidate.intelligence_rank)
            penalty = self._penalties.get(idx, 1.0)

            score = combine_score(reliability, spd, intel, weights, penalty=penalty)
            candidate.score = score
            scored.append((score, candidate))

        # Sort by score descending
        scored.sort(reverse=True, key=lambda x: x[0])

        # Try to find a usable key for each candidate
        for score, candidate in scored:
            key = self._select_key(candidate.platform, candidate.model_id, options)
            if key:
                provider = self._providers.get(candidate.platform)
                if provider:
                    return RouteResult(
                        provider=provider,
                        model_id=candidate.model_id,
                        display_name=candidate.display_name,
                        key=key,
                        platform=candidate.platform,
                        score=score,
                    )

        return None

    def _get_all_candidates(self) -> list[ModelCandidate]:
        """Get all registered model candidates."""
        all_candidates = []
        for platform, models in self._models.items():
            for model in models:
                model.platform = platform
                all_candidates.append(model)
        return all_candidates

    def _get_candidates_for_model(self, model: str) -> list[ModelCandidate]:
        """Get candidates matching a specific model or strategy."""
        model_lower = model.lower()

        # Handle strategy prefixes
        if model_lower.startswith("auto:"):
            return self._get_all_candidates()

        # Try exact model match
        for platform, models in self._models.items():
            for m in models:
                if m.model_id.lower() == model_lower or m.display_name.lower() == model_lower:
                    m.platform = platform
                    return [m]

        # Try partial match
        for platform, models in self._models.items():
            for m in models:
                if model_lower in m.model_id.lower() or model_lower in m.display_name.lower():
                    m.platform = platform
                    return [m]

        return self._get_all_candidates()

    def _select_key(
        self,
        platform: str,
        model_id: str,
        options: dict[str, Any],
        exclude: frozenset[str] | set[str] = frozenset(),
    ) -> KeyEntry | None:
        """Select an available key for the platform and model."""
        keys = self._keys.get(platform, [])
        if not keys:
            return None

        time.time()
        for key in keys:
            if not key.enabled or key.key_id in exclude:
                continue

            # Check key cooldown using is_cooled_down
            if not self._rate_limiter.is_cooled_down(platform, model_id, key.key_id):
                continue

            # Check model scope
            if key.model_scope:
                if model_id not in key.model_scope:
                    continue

            # Check rate limits
            rpm_limit = None
            tpm_limit = None
            for model in self._models.get(platform, []):
                if model.model_id == model_id:
                    rpm_limit = model.rpm_limit
                    tpm_limit = model.tpm_limit
                    break

            if not self._rate_limiter.can_make_request(platform, model_id, key.key_id, rpm_limit):
                continue
            if not self._rate_limiter.can_use_tokens(platform, model_id, key.key_id, tpm_limit):
                continue

            return key

        return None

    def record_success(
        self,
        platform: str,
        model_id: str,
        key_id: str,
        tokens: int,
        duration_ms: float,
        tokens_per_sec: float,
    ):
        """Record a successful request."""
        self._rate_limiter.record_request(platform, model_id, key_id)
        self._rate_limiter.record_tokens(platform, model_id, key_id, tokens)

        with self._lock:
            # Reset escalation
            for model in self._models.get(platform, []):
                if model.model_id == model_id:
                    model.successes += 1
                    model.avg_tokens_per_sec = model.avg_tokens_per_sec * 0.9 + tokens_per_sec * 0.1
                    break

    def record_failure(
        self, platform: str, model_id: str, key_id: str, error: Exception | None = None
    ):
        """Record a failed request."""
        with self._lock:
            for model in self._models.get(platform, []):
                if model.model_id == model_id:
                    model.failures += 1
                    break

            # Set cooldown based on error type
            if error:
                error_status = getattr(error, "status", None)
                if error_status == 429:
                    self._rate_limiter.set_cooldown(
                        platform,
                        model_id,
                        key_id,
                        expires_at=time.time() * 1000 + (2 * 60 * 1000),
                        source="heuristic",
                    )
                elif error_status in (401, 403):
                    self._rate_limiter.set_cooldown(
                        platform,
                        model_id,
                        key_id,
                        expires_at=time.time() * 1000 + (60 * 60 * 1000),
                        source="authoritative",
                    )
                elif error_status == 402:
                    self._rate_limiter.set_cooldown(
                        platform,
                        model_id,
                        key_id,
                        expires_at=time.time() * 1000 + (24 * 60 * 60 * 1000),
                        source="credit",
                    )

    def bench_model(self, platform: str, model_id: str, duration_ms: int = 600_000):
        """Temporarily disable a model (e.g., after repeated failures)."""
        with self._lock:
            for model in self._models.get(platform, []):
                if model.model_id == model_id:
                    model.benched = time.time() + (duration_ms / 1000)
                    break

    def is_model_benched(self, platform: str, model_id: str) -> bool:
        """Check if a model is currently benched."""
        with self._lock:
            for model in self._models.get(platform, []):
                if model.model_id == model_id:
                    return model.benched > 0 and model.benched > time.time()
        return False

    def format_exhaustion(self, diagnostics: list[str]) -> str:
        """Format exhaustion message for client."""
        if not diagnostics:
            return "All models exhausted. Add more API keys or wait for rate limits to reset."
        reasons = set()
        for diag in diagnostics:
            d = diag.lower()
            if "no usable key" in d:
                reasons.add("no usable key configured")
            elif "cooldown" in d or "rate-limit" in d:
                reasons.add("rate-limited")
            elif "context too large" in d or "prompt too large" in d:
                reasons.add("prompt too large")
            else:
                reasons.add("unavailable")
        return f"All models exhausted ({len(diagnostics)} routes checked): {', '.join(reasons)}. Add more API keys or wait for rate limits to reset."
