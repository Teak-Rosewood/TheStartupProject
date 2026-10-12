"""Durable multi-step AI workflows on Postgres jobs."""

from pathlib import Path

from .api import build_router
from .budget import BudgetExceeded, Price, UnpricedModel
from .models import Event, Prompt, PromptActive, Run, Step
from .prompts import PromptRegistry, PromptVersion, parse_prompt_file
from .runner import PipelineDisabled, Runner, StepContext
from .workflow import Workflow

__all__ = [
    "BudgetExceeded",
    "Event",
    "PipelineDisabled",
    "Price",
    "Prompt",
    "PromptActive",
    "PromptRegistry",
    "PromptVersion",
    "Run",
    "Runner",
    "Step",
    "StepContext",
    "UnpricedModel",
    "Workflow",
    "build_router",
    "migrations_path",
    "parse_prompt_file",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.pipeline"])``."""
    return Path(__file__).parent / "migrations" / "versions"
