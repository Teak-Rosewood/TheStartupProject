from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event


async def emit(
    session: AsyncSession, run_id: uuid.UUID, kind: str, step: str | None = None, **data: Any
) -> None:
    """Append to the run's trace. Values must be JSON-able; anything else is stringified."""
    session.add(
        Event(run_id=run_id, step=step, kind=kind, data=json.loads(json.dumps(data, default=str)))
    )
