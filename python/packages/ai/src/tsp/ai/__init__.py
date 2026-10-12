"""tsp.ai: multi-provider LLM client with routing, fallback and verified model catalogs."""

__version__ = "0.1.0"

from .catalog import ModelCatalog, Status
from .client import LLMClient, SyncLLMClient
from .models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    DeltaMessage,
    ModelInfo,
    Usage,
)
from .providers.base import BaseProvider, ProviderHttpError
from .providers.openai_compat import OpenAICompatProvider
from .router import LLMRouter, ModelCandidate, RouteResult, pick_model

__all__ = [
    "__version__",
    "LLMClient",
    "SyncLLMClient",
    "ModelCatalog",
    "Status",
    "ChatMessage",
    "ChatCompletionResponse",
    "ChatCompletionChunk",
    "Choice",
    "DeltaMessage",
    "Usage",
    "ModelInfo",
    "LLMRouter",
    "ModelCandidate",
    "RouteResult",
    "pick_model",
    "BaseProvider",
    "ProviderHttpError",
    "OpenAICompatProvider",
]
