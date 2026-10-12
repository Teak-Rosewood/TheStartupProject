from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VectorConfig:
    """``dims`` must equal the ``VECTOR_DIMS`` the database was migrated with."""

    dims: int = 1024
    embed_model: str = "text-embedding-3-small"
    chunk_tokens: int = 400
    chunk_overlap: int = 60
    embed_batch: int = 96
    candidates: int = 4  # each ranking contributes k * candidates hits to the fusion
    max_document_bytes: int = 20 * 1024 * 1024
