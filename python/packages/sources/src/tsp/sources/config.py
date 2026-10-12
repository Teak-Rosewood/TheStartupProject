from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourcesConfig:
    batch_size: int = 50  # items per transaction; the cursor advances once per committed batch
    raw_prefix: str = "sources"
    max_wait_seconds: float = 60  # longest a fetch waits on the per-domain limiter before failing
    retries: int = 2  # extra attempts on 429 and 5xx
