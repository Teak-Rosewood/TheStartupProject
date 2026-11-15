"""Helpers for the live smoke tests."""

import os

import pytest


def need_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to run this live test")
    return {n: os.environ[n] for n in names}
