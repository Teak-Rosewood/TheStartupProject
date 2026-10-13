# Contributing

## Setup
```bash
cd python
uv sync --all-extras
docker compose -f ../docker-compose.yml up -d     # Postgres, Valkey, DynamoDB Local for tests
```
`make help` lists the common tasks.

## Before you open a pull request
```bash
make check      # ruff, format check, mypy, tests
make synth      # if you touched infrastructure
```
Tests that need a service skip themselves when it is not running, but CI runs all of them.

## Conventions
- Python 3.13+, typed, formatted by ruff. A new module lives in `python/packages/<name>/src/tsp/<name>/` and never adds `src/tsp/__init__.py` (`tsp` is a namespace package).
- Modules take explicit parameters and do not import each other, except through `tsp.db` and `tsp.audit`.
- Money is integer minor units plus a currency code. Timestamps are UTC.
- A security-relevant or architectural decision gets a short record in `docs/adr/`.
- Commit messages: imperative mood, one logical change each. Update `CHANGELOG.md` under "Unreleased".
- Infrastructure changes must pass cdk-nag; every acknowledged finding needs a written reason in `tsp/infra/nag.py`.

## Reporting bugs
Use the issue templates. For security issues, see `SECURITY.md`.
