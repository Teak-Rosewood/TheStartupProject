"""Vector and hybrid search: pgvector store, chunking, ingestion jobs, per-organization isolation."""

from pathlib import Path

from .api import build_router
from .chunker import Chunk, chunk, estimate_tokens
from .compliance import register_with_compliance
from .config import VectorConfig
from .extract import UnsupportedDocument, extract
from .jobs import register_jobs
from .lang import detect_lang, normalize_devanagari
from .models import Collection, Document
from .pg import PgVectorStore
from .s3 import S3VectorStore
from .search import hybrid_search, rrf
from .service import (
    BadRequest,
    Embedded,
    LimitExceeded,
    NotFound,
    VectorError,
    VectorService,
    ai_embedder,
    chunk_id,
)
from .store import Hit, VectorItem, VectorStore

__all__ = [
    "BadRequest",
    "Chunk",
    "Collection",
    "Document",
    "Embedded",
    "Hit",
    "LimitExceeded",
    "NotFound",
    "PgVectorStore",
    "S3VectorStore",
    "UnsupportedDocument",
    "VectorConfig",
    "VectorError",
    "VectorItem",
    "VectorService",
    "VectorStore",
    "ai_embedder",
    "build_router",
    "chunk",
    "chunk_id",
    "estimate_tokens",
    "extract",
    "detect_lang",
    "hybrid_search",
    "normalize_devanagari",
    "migrations_path",
    "register_jobs",
    "register_with_compliance",
    "rrf",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.vector"])``."""
    return Path(__file__).parent / "migrations" / "versions"
