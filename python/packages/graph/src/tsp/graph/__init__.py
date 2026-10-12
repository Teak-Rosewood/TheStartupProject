"""Knowledge graph on Postgres: entities, claims with provenance, dated links, stances, time series."""

from pathlib import Path

from .api import build_router
from .enrich import EnrichContext, Enricher, enrich_on_demand
from .models import (
    Alias,
    Claim,
    Document,
    Enrichment,
    Entity,
    Link,
    Observation,
    Series,
    Stance,
)
from .resolve import normalise
from .series import Comparison, SeriesService
from .service import GraphService
from .types import Candidate, Edge, Node, as_node
from .types import Path as GraphPath

__all__ = [
    "Alias",
    "Candidate",
    "Claim",
    "Comparison",
    "Document",
    "Edge",
    "EnrichContext",
    "Enricher",
    "Enrichment",
    "Entity",
    "GraphPath",
    "GraphService",
    "Link",
    "Node",
    "Observation",
    "Series",
    "SeriesService",
    "Stance",
    "as_node",
    "build_router",
    "enrich_on_demand",
    "migrations_path",
    "normalise",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.graph"])``."""
    return Path(__file__).parent / "migrations" / "versions"
