.PHONY: install install-test test smoke test-all coverage lint docker-build docker-test run status clean

# ── Setup ───────────────────────────────────────────────────────────

install:
	pip install -r requirements.txt

install-test:
	pip install -r requirements-test.txt

# ── Testing ─────────────────────────────────────────────────────────

test:                    ## Run unit tests
	pytest tests/ --ignore=tests/test_smoke.py -v

smoke:                   ## Run smoke/integration tests
	pytest tests/test_smoke.py -v

test-all:                ## Run all tests (unit + smoke)
	pytest tests/ -v

coverage:                ## Run tests with coverage report
	pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80

# ── Docker ──────────────────────────────────────────────────────────

docker-build:            ## Build Docker image (runs tests inside)
	docker build --target test -t varden:test .
	docker build --target production -t varden:latest .

docker-test:             ## Run tests in Docker only
	docker build --target test -t varden:test .

# ── Run ─────────────────────────────────────────────────────────────

run:                     ## Run one analysis cycle
	python main.py run

status:                  ## Show current state
	python main.py status

daemon:                  ## Start daemon mode
	python main.py daemon

# ── Cleanup ─────────────────────────────────────────────────────────

clean:                   ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov

help:                    ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
