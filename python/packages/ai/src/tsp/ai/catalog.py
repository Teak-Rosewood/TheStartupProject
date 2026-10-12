"""Verified model catalog: what discovery found, and which models actually answered."""

from __future__ import annotations

import time
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CATALOG_PATH = Path.home() / ".cache" / "tsp-ai" / "catalog.json"


class Status(str, Enum):
    OK = "ok"
    AUTH = "auth"
    QUOTA_OR_BILLING = "quota_or_billing"
    RATE_LIMITED = "rate_limited"
    MODEL_UNAVAILABLE = "model_unavailable"
    BAD_REQUEST = "bad_request"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    EMPTY_REPLY = "empty_reply"
    SKIPPED = "skipped"


class ModelResult(BaseModel):
    id: str
    status: Status
    latency_ms: float | None = None
    error: str | None = None
    dims: int | None = None  # embedding models only: length of the vector the probe returned
    checked_at: float = Field(default_factory=time.time)


class ProviderResult(BaseModel):
    platform: str
    # "ok" if the key could list/serve models, else the Status explaining why not.
    key_status: Status
    error: str | None = None
    listed: int = 0
    models: list[ModelResult] = Field(default_factory=list)
    embedding_models: list[ModelResult] = Field(default_factory=list)

    @property
    def ok_models(self) -> list[ModelResult]:
        return [m for m in self.models if m.status is Status.OK]

    @property
    def ok_embedding_models(self) -> dict[str, int | None]:
        """Embedding models that answered, with the dims the probe saw."""
        return {m.id: m.dims for m in self.embedding_models if m.status is Status.OK}


class ModelCatalog(BaseModel):
    generated_at: float = Field(default_factory=time.time)
    providers: dict[str, ProviderResult] = Field(default_factory=dict)

    def save(self, path: str | Path = DEFAULT_CATALOG_PATH) -> Path:
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
        return path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CATALOG_PATH) -> ModelCatalog:
        """Raises FileNotFoundError / ValueError with the path in the message."""
        path = Path(path).expanduser()
        try:
            return cls.model_validate_json(path.read_text())
        except FileNotFoundError:
            raise FileNotFoundError(
                f"model catalog not found: {path}. Run `tsp-ai verify` to create it."
            ) from None
        except ValueError as e:
            raise ValueError(f"model catalog {path} is corrupt: {e}") from e
