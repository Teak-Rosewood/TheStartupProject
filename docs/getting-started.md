# Getting started

## Work inside the kit

```bash
git clone <this repo> TheStartupProject
cd TheStartupProject/python
uv sync --all-extras          # installs every module plus dev tools
uv run pytest                 # sanity check
```

## Use a module in your own project

Each module is an independent package under the shared `tsp` namespace.

```bash
uv add ../TheStartupProject/python/packages/ai            # or: pip install -e <path>
uv add "../TheStartupProject/python/packages/ai[server]"  # with the HTTP server
```

```python
from tsp.ai import LLMClient
```

### Pin a released version from Git

For a product in its own repository, depend on a release tag instead of a path. Every package in a release shares one version ([ADR 0002](adr/0002-versioning-and-releases.md)), so use the same tag for all of them:

```toml
# pyproject.toml of your product
[project]
dependencies = ["tsp-cms", "tsp-vector"]

[tool.uv.sources]
tsp-cms = { git = "https://github.com/Teak-Rosewood/TheStartupProject", tag = "v0.2.0", subdirectory = "python/packages/cms" }
tsp-vector = { git = "https://github.com/Teak-Rosewood/TheStartupProject", tag = "v0.2.0", subdirectory = "python/packages/vector" }
```

The kit packages they depend on (`tsp-db`, `tsp-jobs`, ...) come from the same tag automatically. To upgrade, change every `tag` together and read the **Breaking** list for each version in between in [CHANGELOG.md](../CHANGELOG.md).

## Add a module

Create `python/packages/<name>/` with `pyproject.toml` (`name = "tsp-<name>"`) and `src/tsp/<name>/`. Do not add `src/tsp/__init__.py`: `tsp` is a namespace package shared by all modules. `uv sync` picks the new package up through the workspace glob.

## Local services for the integration tests
```bash
docker compose up -d        # Postgres :55432, Valkey :56379, DynamoDB Local :58000
cd python && uv run pytest  # tests that need a service skip themselves when it is absent
docker/postgres/test_backup.sh   # backup + point-in-time restore proof (needs docker; builds an image)
```

## Checks

```bash
cd python
uv run ruff check . && uv run ruff format --check . && uv run mypy packages && uv run pytest
```
