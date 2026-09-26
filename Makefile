# GleasonAI - common commands. `make help` lists them.
SHELL := /bin/bash
PROD := docker compose -f docker-compose.yml
DEV := docker compose -f docker-compose.yml -f docker-compose.override.yml
DEVBACKEND := docker compose -f docker-compose.dev.yml
THESIS_DIR ?= $(HOME)/Desktop/abdurahman FYP/v2_pipeline/submission_files
TEST_IMAGE := gleasonai-worker-test

.DEFAULT_GOAL := help
.PHONY: help env bundle fetch up up-gpu down dev dev-backend monitoring logs ps migrate seed-admin seed-demo seed-e2e \
        test test-inference test-backend test-frontend e2e lint build test-image backup restore restore-test \
        security-scan validate loadtest openapi clean-cache

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## create .env with random secrets (once)
	@./scripts/make-env.sh

bundle: ## package the thesis models into models/bundle (THESIS_DIR=.../submission_files)
	docker build -f docker/worker.Dockerfile --target test -t $(TEST_IMAGE) .
	docker run --rm -v "$(THESIS_DIR):/thesis:ro" -v "$(CURDIR)/models:/models" $(TEST_IMAGE) \
	  python -m prostate_infer build-bundle --fl-dir /thesis/fl_outputs_phikon --results-dir /thesis/results_phikon --out /models/bundle

fetch: ## verify the model bundle and download Phikon into the model cache (needs internet once)
	$(PROD) build worker
	$(PROD) run --rm --no-deps -e OFFLINE=0 worker python -m prostate_infer fetch

up: ## start the production stack (https://$$DOMAIN)
	$(PROD) up -d --build
	@echo "GleasonAI is starting at $$(grep ^PUBLIC_URL .env | cut -d= -f2 | cut -d' ' -f1)"

up-gpu: ## start with the NVIDIA GPU worker
	$(PROD) -f docker-compose.gpu.yml up -d --build

down: ## stop everything (data volumes are kept)
	$(PROD) --profile monitoring --profile antivirus down

dev: ## full stack with hot reload (API + dashboard), API docs at /api/v1/docs
	$(DEV) up -d --build

dev-backend: ## backend only (api, worker, scheduler, postgres, redis) on http://localhost:8000
	$(DEVBACKEND) up -d --build

monitoring: ## start Prometheus (127.0.0.1:9090), Alertmanager (:9093), Grafana (:3001)
	$(PROD) --profile monitoring up -d

logs: ## follow logs
	$(PROD) logs -f --tail 100

ps: ## container status
	$(PROD) ps

migrate: ## apply database migrations
	$(PROD) exec api alembic -c /app/backend/alembic.ini upgrade head

seed-admin: ## create an administrator: make seed-admin EMAIL=you@hospital.org HOSPITAL="Radboud UMC"
	$(PROD) exec api python -m backend.scripts.seed admin --email "$(EMAIL)" --hospital "$(HOSPITAL)"

seed-demo: ## demo accounts + 6 demo cases (prints the demo password)
	$(PROD) exec worker python -m backend.scripts.seed demo

seed-e2e: ## accounts used by the Playwright end-to-end tests
	$(PROD) exec -e DEMO_PASSWORD="$${E2E_PASSWORD:-E2e-Demo-Password-2026!}" worker python -m backend.scripts.seed demo

test-image:
	docker build -f docker/worker.Dockerfile --target test -t $(TEST_IMAGE) .

test: test-inference test-backend test-frontend ## run every unit/integration test suite

test-inference: test-image ## inference package tests (+ thesis-metric checks when models/bundle exists)
	docker run --rm -v "$(CURDIR)/inference:/src/inference" -v "$(CURDIR)/models:/models:ro" -e THESIS_BUNDLE=/models/bundle \
	  -w /src/inference $(TEST_IMAGE) python -m pytest -q --cov=prostate_infer --cov-report=term

test-backend: test-image ## API tests (SQLite + fakeredis + real worker task with fake models)
	docker run --rm -v "$(CURDIR)/backend:/src/backend" -v "$(CURDIR)/inference:/src/inference" -w /src/backend \
	  $(TEST_IMAGE) python -m pytest -q --cov --cov-report=term

test-frontend: ## component tests (Vitest) + type check + lint
	cd frontend && npm run type-check && npm run lint && npm test

e2e: ## Playwright end-to-end tests against the running stack (make up && make seed-e2e first)
	cd frontend && BASE_URL=$${BASE_URL:-https://localhost} E2E_PASSWORD=$${E2E_PASSWORD:-E2e-Demo-Password-2026!} npx playwright test

lint: test-image ## ruff + mypy (Python), eslint + prettier + tsc (frontend)
	docker run --rm -v "$(CURDIR)/backend:/src/backend" -v "$(CURDIR)/inference:/src/inference" -w /src $(TEST_IMAGE) \
	  sh -c "ruff check backend inference --exclude backend/migrations && ruff format --check backend inference \
	         && mypy --config-file backend/pyproject.toml backend && cd inference && mypy prostate_infer"
	cd frontend && npm run lint && npm run type-check && npm run format:check

build: ## build all images
	$(PROD) build

backup: ## run an encrypted backup now (database + object storage) into ./backups
	$(PROD) run --rm backup backup

restore: ## restore a backup: make restore STAMP=20260926T013000Z (or STAMP=latest)
	$(PROD) run --rm backup restore $(or $(STAMP),latest)

restore-test: ## prove backups work: back up, wipe a scratch copy, restore, compare row counts
	./scripts/restore-test.sh

security-scan: ## pip-audit, npm audit, gitleaks, trivy (image scan)
	./scripts/security-scan.sh

validate: ## compare the website with the thesis metrics (add PANDA_DIR=... to run slides through the API)
	docker run --rm --network host -v "$(CURDIR)/models:/models:ro" -v "$(CURDIR)/docs:/docs" -v "$(CURDIR)/scripts:/scripts:ro" \
	  $(if $(PANDA_DIR),-v "$(PANDA_DIR):/panda:ro",) $(TEST_IMAGE) python /scripts/validate_on_test_split.py \
	  --bundle /models/bundle --out /docs/validation_report.md $(if $(PANDA_DIR),--panda /panda,) $(ARGS)

loadtest: ## Locust: 20 viewers + 5 parallel uploads for 3 minutes; report in docs/loadtest/
	./scripts/loadtest.sh

openapi: ## regenerate the typed frontend client from the running API
	cd frontend && OPENAPI_URL=$${OPENAPI_URL:-http://localhost:8000/api/v1/openapi.json} npm run gen:api
