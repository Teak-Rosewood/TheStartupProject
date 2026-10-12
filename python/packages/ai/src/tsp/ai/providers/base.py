from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class ProviderHttpError(Exception):
    """HTTP error from an upstream provider with status and retry info."""

    def __init__(
        self, message: str, status: int | None = None, retry_after_ms: float | None = None
    ):
        super().__init__(message)
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.daily_quota_exhausted = False


class BaseProvider(ABC):
    """Abstract base class for all LLM providers."""

    @property
    @abstractmethod
    def platform(self) -> str:
        """Unique platform identifier (e.g., 'openai', 'anthropic')."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        pass

    @property
    def keyless(self) -> bool:
        """Whether this provider has a free tier that needs no API key."""
        return False

    @abstractmethod
    async def chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion."""
        pass

    @abstractmethod
    def stream_chat_completion(
        self,
        api_key: str,
        messages: list[dict[str, Any]],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Streaming chat completion."""
        pass

    @abstractmethod
    async def validate_key(self, api_key: str) -> bool | dict[str, Any]:
        """Validate an API key. Returns True if valid, or dict with 'valid': False, 'error': msg."""
        pass

    # ── Optional capabilities ──────────────────────────────────────────────
    async def list_models(self, api_key: str) -> list[dict[str, Any]]:
        """List models served for this key. Raise ProviderHttpError on failure."""
        raise NotImplementedError(f"{self.platform} does not list models")

    async def close(self) -> None:  # noqa: B027 - optional hook, default no-op
        """Release network resources."""

    async def embedding(
        self,
        api_key: str,
        input: str | list[str],
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create embeddings. Override if the provider supports embeddings."""
        raise NotImplementedError(f"{self.platform} does not support embeddings")

    async def completion(
        self,
        api_key: str,
        prompt: str,
        model_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Plain text completion. Override if the provider supports completions."""
        raise NotImplementedError(f"{self.platform} does not support completions")

    async def fetch_quota(self, api_key: str) -> bool:
        """Try to fetch quota info. Returns True if observation was recorded."""
        return False

    @property
    def has_quota_probe(self) -> bool:
        """Whether this provider has a quota endpoint."""
        return False


# ── Common helpers ──────────────────────────────────────────────────────────


def parse_retry_after_ms(value: str | None) -> float | None:
    """Parse Retry-After header into milliseconds."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(int(value) * 1000, 24 * 60 * 60 * 1000)
    return None


def parse_prose_retry(text: str) -> float | None:
    """Extract retry delay from prose like 'retry after 30 seconds'."""
    import re

    patterns = [
        r"(?:try again|retry)\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hour|hours)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            amount = float(m.group(1))
            unit = m.group(2).lower()
            multipliers = {
                "ms": 1,
                "s": 1000,
                "sec": 1000,
                "secs": 1000,
                "second": 1000,
                "seconds": 1000,
                "m": 60000,
                "min": 60000,
                "mins": 60000,
                "minute": 60000,
                "minutes": 60000,
                "h": 3600000,
                "hour": 3600000,
                "hours": 3600000,
            }
            if unit in multipliers:
                return min(amount * multipliers[unit], 24 * 60 * 60 * 1000)
    return None


def build_error(status: int, message: str, response_body: Any | None = None) -> ProviderHttpError:
    """Build a ProviderHttpError from HTTP response details."""
    if status >= 500:
        retry_after = parse_retry_after_ms(None)  # server errors
    elif status == 429:
        retry_after = parse_retry_after_ms(None)  # rate limited
    else:
        retry_after = None

    # Try to extract retry hint from body
    if response_body:
        text = str(response_body)
        retry_after = parse_prose_retry(text) or retry_after

    err = ProviderHttpError(message, status=status, retry_after_ms=retry_after)
    if status == 402:
        err.daily_quota_exhausted = True
    return err
