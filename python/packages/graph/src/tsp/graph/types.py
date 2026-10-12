from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from .models import Claim, Document, Entity

NodeKind = Literal["entity", "document", "claim"]
Node = tuple[str, uuid.UUID]  # (kind, id)

_KIND_OF_TABLE = {"g_entities": "entity", "g_documents": "document", "g_claims": "claim"}


def as_node(x: Node | Entity | Document | Claim) -> Node:
    """Accept a ``(kind, id)`` pair or any node row."""
    if isinstance(x, tuple):
        kind, id_ = x
    else:
        kind, id_ = _KIND_OF_TABLE[x.__tablename__], x.id
    if kind not in ("entity", "document", "claim"):
        raise ValueError(f"unknown node kind {kind!r}")
    return kind, id_


@dataclass(frozen=True)
class Edge:
    id: uuid.UUID
    src: Node
    rel: str
    dst: Node
    valid_from: date | None
    valid_to: date | None
    depth: int = 1  # hops from the starting node (0 for point lookups)
    attrs: dict[str, Any] | None = None
    evidence_claim_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Path:
    nodes: list[Node]
    edges: list[Edge]


@dataclass(frozen=True)
class Candidate:
    entity: Entity
    score: float  # 1.0 exact alias match, else trigram similarity
    matched_alias: str
    how: Literal["exact", "trigram"]
