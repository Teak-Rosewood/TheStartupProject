"""Live smoke tests: real network, real services, real libraries.

Not part of the normal test run (``testpaths`` leaves this folder out). Run them with
``TSP_LIVE=1 uv run pytest live -v``; the nightly ``live`` workflow does. A test whose service
needs a key or URL that is not set is skipped, not failed.
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TSP_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set TSP_LIVE=1 to run")
    for item in items:
        item.add_marker(skip)
