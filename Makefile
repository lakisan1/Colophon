# Colophon developer entry points. Uvicorn always runs a SINGLE worker: SQLite
# writes and the in-memory rate limiter assume one process (DECISIONS T2).

VENV := .venv
PIP := $(VENV)/bin/pip

.PHONY: venv dev test lint fmt migrate seed beat doctor

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r requirements-dev.txt

# Dev server: reload on change, dev auth bypass on (login is M5).
dev:
	COLOPHON_DEV_AUTH_BYPASS=1 $(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff check .

fmt:
	$(VENV)/bin/ruff format .

migrate:
	$(VENV)/bin/alembic upgrade head

# Dev/demo fixture (M2). Rebuilds only the demo user's rows, so it is safe to
# re-run against a database that already holds other data.
seed:
	$(VENV)/bin/python -m app.seed

# Liveness heartbeat. Any agent doing work touches this; scripts/doctor.sh calls
# the run dead when it goes stale. Cheap insurance against a silent stall.
beat:
	@mkdir -p .run && date -u +%Y-%m-%dT%H:%M:%SZ > .run/heartbeat

# Status in one line. `make doctor CHECK=1` also runs the test suite.
doctor:
	@scripts/doctor.sh $(if $(CHECK),--tests,)
