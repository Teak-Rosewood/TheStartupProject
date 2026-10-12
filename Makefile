.PHONY: help up down test lint check synth backup-test legal-check docs-check landing
PY = cd python && uv run

help:        ## list tasks
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

up:          ## start local Postgres, Valkey, DynamoDB Local
	docker compose up -d

down:        ## stop local services
	docker compose down

test:        ## run all tests (start `make up` first for the integration tests)
	$(PY) pytest -q

lint:        ## ruff + mypy
	$(PY) ruff check . && $(PY) ruff format --check . && $(PY) mypy packages apps/api/src

check: lint test  ## everything CI runs for Python

synth:       ## synthesize the CDK app (cdk-nag runs in the infra tests)
	$(PY) pytest packages/infra -q && cd python/apps/infra && npx --yes aws-cdk@2 synth --quiet

backup-test: ## prove backup and point-in-time restore end to end (needs docker)
	docker build -t tsp-postgres:test docker/postgres && docker/postgres/test_backup.sh

legal-check: ## every {{placeholder}} in docs/legal is defined in company.example.toml
	python3 docs/legal/check_placeholders.py

docs-check:  ## relative Markdown links resolve
	python3 docs/check_links.py

legal-render: ## render docs/legal into python/apps/api/legal (uses docs/legal/company.toml, else the example)
	cd python && C=../docs/legal/company.toml; [ -f $$C ] || C=../docs/legal/company.example.toml; \
	uv run tsp-compliance render-legal --config $$C --src ../docs/legal --out apps/api/legal

landing:     ## build and verify the landing site (needs `cd js && npm ci` once)
	$(MAKE) legal-render && cd js && npm run build -w @tsp/landing && npm run verify -w @tsp/landing && npm test -w @tsp/landing
