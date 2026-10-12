from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class BudgetExceeded(Exception):
    """The run has spent, or would with the next call, more than its budget."""


class UnpricedModel(Exception):
    """A budgeted run called a model with no entry in the price table (fail closed)."""


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    in_per_mtok: float
    out_per_mtok: float

    def cost(self, tokens_in: int, tokens_out: int) -> Decimal:
        return (
            Decimal(str(self.in_per_mtok)) * tokens_in
            + Decimal(str(self.out_per_mtok)) * tokens_out
        ) / 1_000_000


def estimate_tokens(text: str) -> int:
    return max(1, -(-len(text) // 4)) if text else 0
