import time
import uuid
from typing import Any

from pydantic import BaseModel, Field


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ChatToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")
    type: str = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    role: str  # "system", "user", "assistant", "tool"
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[ChatToolCall] | None = None
    tool_call_id: str | None = None


class Usage(BaseModel):
    """``prompt_tokens`` counts every input token, cached or not (the OpenAI convention); the two
    cache fields say how many of them were written to or read from the provider's prompt cache."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Doc(BaseModel):
    """A source document the model may quote; with ``citations=True`` answers point back to it."""

    id: str
    text: str
    title: str | None = None


class Citation(BaseModel):
    doc_id: str
    start: int  # character offsets into the document text
    end: int
    quoted_text: str


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ChatToolCall] | None = None


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str | None = "stop"


class DeltaMessage(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[ChatToolCall] | None = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage | None = None
    routed_via: str | None = None
    citations: list[Citation] | None = None


class ChatCompletionChunk(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[StreamChoice]
    routed_via: str | None = None


class ModelInfo(BaseModel):
    id: str
    platform: str
    model_id: str
    display_name: str
    intelligence_rank: int = 50
    speed_rank: int = 50
    supports_vision: bool = False
    supports_tools: bool = True
    context_window: int = 128000
    available: bool = True
    execution_status: str = "ready"  # ready, needsKey, exhausted


class EmbeddingItem(BaseModel):
    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    object: str = "list"
    model: str
    provider: str
    dims: int
    data: list[EmbeddingItem]
    usage: Usage = Field(default_factory=Usage)


class EmbeddingDimensionError(ValueError):
    """A provider returned vectors of a different length than requested or recorded in the catalog."""
