"""Environment-file loading."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines and ``os.environ.setdefault`` each, so real env vars win.

    Returns the parsed pairs. Missing file raises FileNotFoundError.
    """
    pairs: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip().strip("'\"")
        pairs[key] = val
        os.environ.setdefault(key, val)
    return pairs
