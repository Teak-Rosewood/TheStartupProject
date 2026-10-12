"""tsp.ai: multi-provider LLM client with routing, fallback and verified model catalogs."""

__version__ = "0.1.0"

from .batches import BATCH_DISCOUNT, BatchRequest, BatchResult, BatchStatus
from .catalog import ModelCatalog, Status
from .client import LLMClient, SyncLLMClient
from .models import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Citation,
    DeltaMessage,
    Doc,
    EmbeddingDimensionError,
    EmbeddingItem,
    EmbeddingResponse,
    ModelInfo,
    Usage,
)
from .providers.base import BaseProvider, ProviderHttpError
from .providers.openai_compat import OpenAICompatProvider
from .router import LLMRouter, ModelCandidate, RouteResult, pick_model
from .structured import StructuredOutputError, structured
from .translate import Translation, translate

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
    "Citation",
    "Doc",
    "BATCH_DISCOUNT",
    "BatchRequest",
    "BatchResult",
    "BatchStatus",
    "StructuredOutputError",
    "Translation",
    "structured",
    "translate",
    "DeltaMessage",
    "EmbeddingDimensionError",
    "EmbeddingItem",
    "EmbeddingResponse",
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
