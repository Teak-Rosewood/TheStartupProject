"""Single source of truth for supported providers.

Each :class:`ProviderSpec` knows its platform name, env var, and how to build its adapter.
Add a provider here and ``LLMClient``, ``from_env`` and ``tsp-ai verify`` all pick it up.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .anthropic import AnthropicProvider
from .base import BaseProvider
from .bedrock import BedrockProvider
from .cohere import CohereProvider
from .google import GoogleProvider
from .openai_compat import OpenAICompatProvider


@dataclass(frozen=True)
class ProviderSpec:
    platform: str
    name: str
    base_url: str | None = None
    native: Callable[[], BaseProvider] | None = None
    # Used only when the provider cannot list its models.
    fallback_models: tuple[str, ...] = ()
    # Every model the provider lists is free to call (so --free-only probes all of them).
    free_tier_only: bool = False
    # Under --free-only, a listed model whose id contains one of these is treated as free.
    free_markers: tuple[str, ...] = (":free", "-free")
    models_url: str | None = None
    keyless: bool = False

    @property
    def env_var(self) -> str:
        return f"{self.platform.upper()}_KEY"

    def build(self, base_url: str | None = None, name: str | None = None) -> BaseProvider:
        if self.native:
            return self.native()
        url = base_url or self.base_url
        if not url:
            raise ValueError(f"provider {self.platform!r} needs a base_url")
        return OpenAICompatProvider(
            platform=self.platform,
            name=name or self.name,
            base_url=url,
            keyless=self.keyless,
            models_url=self.models_url,
        )


def _compat(platform: str, name: str, base_url: str, **kw) -> ProviderSpec:
    return ProviderSpec(platform, name, base_url=base_url, **kw)


_SPECS = [
    _compat("openai", "OpenAI", "https://api.openai.com/v1"),
    ProviderSpec("anthropic", "Anthropic", native=AnthropicProvider),
    ProviderSpec("google", "Google Gemini", native=GoogleProvider, free_tier_only=True),
    ProviderSpec("cohere", "Cohere", native=CohereProvider),
    ProviderSpec("bedrock", "AWS Bedrock", native=BedrockProvider, keyless=True),
    _compat("agnes", "Agnes AI", "https://apihub.agnes-ai.com/v1"),
    _compat("blockrun", "BlockRun", "https://api.blockrun.ai/v1"),
    _compat("cerebras", "Cerebras", "https://api.cerebras.ai/v1", free_tier_only=True),
    _compat(
        "github",
        "GitHub Models",
        "https://models.github.ai/inference",
        free_tier_only=True,
        models_url="https://models.github.ai/catalog/models",
    ),
    _compat("gizmo", "Gizmo", "https://api.gizmoplatforms.com/api/v1"),
    _compat("groq", "Groq", "https://api.groq.com/openai/v1", free_tier_only=True),
    _compat("huggingface", "Hugging Face", "https://router.huggingface.co/v1"),
    _compat("kilo", "Kilo", "https://api.kilo.ai/api/gateway/v1"),
    _compat("llm7", "LLM7", "https://api.llm7.io/v1", free_tier_only=True),
    _compat("llmtr", "LLMTR", "https://llmtr.com/v1"),
    _compat("mistral", "Mistral", "https://api.mistral.ai/v1", free_tier_only=True),
    _compat("moondream", "Moondream", "https://api.moondream.ai/v1", free_tier_only=True),
    _compat("navy", "Navy", "https://api.navy/v1"),
    _compat("nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", free_tier_only=True),
    _compat("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    _compat("sail", "SAIL", "https://api.sailresearch.com/v1"),
    _compat("typhoon", "Typhoon", "https://api.opentyphoon.ai/v1", free_tier_only=True),
    _compat("xkiro", "Xkiro", "https://api.xkiro.com/v1"),
    _compat("zhipu", "Zhipu AI", "https://open.bigmodel.cn/api/paas/v4", free_markers=("flash",)),
    _compat("ollama", "Ollama", "http://localhost:11434/v1", keyless=True),
]

PROVIDERS: dict[str, ProviderSpec] = {s.platform: s for s in _SPECS}


def get_spec(platform: str) -> ProviderSpec:
    try:
        return PROVIDERS[platform]
    except KeyError:
        raise ValueError(
            f"unknown provider {platform!r}; known: {', '.join(sorted(PROVIDERS))}. "
            "Pass base_url to use a custom OpenAI-compatible endpoint."
        ) from None
