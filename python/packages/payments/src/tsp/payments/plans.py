"""Plans as code: a TOML file synced into the plans table at startup.

[[plan]]
code = "pro_monthly"
name = "Pro (monthly)"
kind = "subscription"          # subscription | one_time | credit_pack
amount = 99900                 # paise, tax inclusive
interval = "month"
provider_plan_id = "plan_xxx"  # created in the gateway dashboard
credits = 0
features = { seats = 10, exports = true }
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .service import PlanSpec

_KINDS = {"subscription", "one_time", "credit_pack"}


def load_plans(path: str | Path) -> list[PlanSpec]:
    data = tomllib.loads(Path(path).read_text())
    plans: list[PlanSpec] = []
    for p in data.get("plan", []):
        if p.get("kind") not in _KINDS:
            raise ValueError(f"plan {p.get('code')!r}: kind must be one of {sorted(_KINDS)}")
        if not isinstance(p.get("amount"), int) or p["amount"] < 0:
            raise ValueError(
                f"plan {p.get('code')!r}: amount must be a whole number of minor units (paise)"
            )
        plans.append(PlanSpec(**{k: v for k, v in p.items() if k in PlanSpec.__dataclass_fields__}))
    codes = [p.code for p in plans]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate plan codes")
    return plans
