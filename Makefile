# Makefile
#
# Wraps README.md Section 12 ("Reproducibility")'s documented setup script
# into four targets grouped by lifecycle purpose rather than that script's
# walk-through order: setup (environment + full schema) -> load (real data)
# -> check (lint + tests) -> app (dashboard). This is a reordering, not a
# different pipeline - none of sql/01,02,03,05,07 depend on data existing
# (views and grants are definitions, not data-dependent; indexes populate
# incrementally as rows are inserted), so applying all of them before
# src/load_postgres.py runs is functionally identical to the README's
# script, which interleaves them after the load for narrative reasons.
#
# Every recipe calls .venv/bin/<tool> explicitly rather than relying on an
# activated venv, since each recipe line runs in its own subshell (a
# `source .venv/bin/activate` wouldn't persist across lines or targets).
# For interactive work, `source .venv/bin/activate` still works exactly as
# README.md documents.
#
# Assumes local PostgreSQL with peer/trust auth for the current OS user -
# this project's actual dev environment (see sql/07_app_role.sql's own
# comment) - so, like every psql command in the README, none of these
# recipes pass -U. No local PostgreSQL install? `make docker-up` runs one
# in Docker instead (see docker-compose.yml) - export PGHOST=localhost
# PGUSER=postgres first (that file's own comment explains why both are
# needed), then every target below works unchanged.

.PHONY: help setup load check app load-fixture clean-fixture-db docker-up docker-down

VENV := .venv
PYTHON := $(VENV)/bin/python3
DB_NAME ?= volve_analytics
SCHEMA_FILES := sql/01_create_schemas.sql sql/02_create_tables.sql \
                sql/03_create_indexes.sql sql/05_views.sql sql/07_app_role.sql

help:
	@echo "make setup  - venv + pip install + create/apply schema to $(DB_NAME)"
	@echo "make load   - load data/raw/Volve production data.xlsx (obtain it"
	@echo "              yourself first - see data/README.md)"
	@echo "make check  - sqlfluff lint + pytest suite"
	@echo "make app    - run the Streamlit dashboard"
	@echo ""
	@echo "make load-fixture   - load the tiny synthetic fixture instead of"
	@echo "                      the real workbook (no license needed - lets"
	@echo "                      'make check'/'make app' run without it)"
	@echo "make clean-fixture-db - drop the fixture's known content so"
	@echo "                        load-fixture can start clean"
	@echo ""
	@echo "make docker-up   - run PostgreSQL 17 in Docker instead of installing it"
	@echo "                   (see docker-compose.yml for the export PGHOST=... /"
	@echo "                   PGUSER=... this needs before the targets above)"
	@echo "make docker-down - stop it (data persists in a named volume)"
	@echo ""
	@echo "Override the database name with DB_NAME=whatever"

$(VENV)/bin/pip: requirements.txt
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements.txt
	touch $(VENV)/bin/pip

setup: $(VENV)/bin/pip
	@psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$(DB_NAME)'" | grep -q 1 \
		&& echo "Database $(DB_NAME) already exists, skipping createdb" \
		|| createdb $(DB_NAME)
	@for f in $(SCHEMA_FILES); do \
		echo "-- $$f"; \
		psql -d $(DB_NAME) -v ON_ERROR_STOP=1 -f $$f > /dev/null || exit 1; \
	done
	@echo ""
	@echo "Schema ready on $(DB_NAME). Next:"
	@echo "  1. Obtain the real workbook (data/README.md) and run: make load"
	@echo "     - or, without the licensed data: make load-fixture"
	@echo "  2. make check"
	@echo "  3. make app"

load: $(VENV)/bin/pip
	VOLVE_DB_NAME=$(DB_NAME) $(PYTHON) src/load_postgres.py
	@echo ""
	@echo "Loaded. Sanity check: psql -d $(DB_NAME) -f sql/04_quality_checks.sql"
	@echo "  (expect: 27 total, 21 PASS, 6 REVIEW, 0 FAIL - see README.md Section 12)"

# Loads tests/fixtures/generate_sample_workbook.py's tiny 2-well synthetic
# stand-in instead of the real, licensed workbook - the same fixture
# .github/workflows/ci.yml's loader-fixture-test job uses. Lets `make
# check`'s pytest suite exercise its content-dependent tests, and `make
# app` show a working (if tiny) dashboard, without needing the real data.
load-fixture: $(VENV)/bin/pip
	$(PYTHON) tests/fixtures/generate_sample_workbook.py /tmp/volve_sample_workbook.xlsx
	VOLVE_DB_NAME=$(DB_NAME) \
	VOLVE_WORKBOOK_PATH=/tmp/volve_sample_workbook.xlsx \
	VOLVE_EXPECTED_DAILY_ROWS=20 \
	VOLVE_EXPECTED_WELLBORE_COUNT=2 \
	$(PYTHON) src/load_postgres.py

# load-fixture's truncate-and-reload only touches core/raw, not a full
# drop - this is only needed if you loaded the real data first and want
# to switch back to the fixture (load-fixture alone reproduces the same
# fixture state every time otherwise, per src/load_postgres.py's own
# idempotency guarantee).
clean-fixture-db:
	psql -d $(DB_NAME) -v ON_ERROR_STOP=1 -c \
		"TRUNCATE TABLE core.daily_production, core.monthly_reference, core.wellbore, raw.daily_production_source, raw.monthly_production_source RESTART IDENTITY"

check: $(VENV)/bin/pip
	$(VENV)/bin/sqlfluff lint sql/
	$(PYTHON) -m py_compile app/*.py app/views/*.py src/*.py
	VOLVE_DB_NAME=$(DB_NAME) $(VENV)/bin/pytest tests/ -v

app: $(VENV)/bin/pip
	VOLVE_DB_NAME=$(DB_NAME) $(VENV)/bin/streamlit run app/app.py

# Checks for a listener on the target port first, rather than letting
# `docker compose up` "succeed" straight into a real, confirmed failure
# mode on macOS: a native PostgreSQL bound to localhost:5432 and Docker's
# proxy bound to *:5432 can BOTH stay up with no error - lsof shows both
# listening - but a client connecting to localhost:5432 silently reaches
# the native install, not the container, however "healthy" it reports.
# Verified this exact collision by hand before adding the check: without
# it, `make docker-up` here reports success and `psql` connects, but to
# the wrong PostgreSQL, with a confusing "role postgres does not exist"
# error - not "port in use".
docker-up:
	@port="$${VOLVE_PG_PORT:-5432}"; \
	if command -v lsof > /dev/null && lsof -iTCP:$$port -sTCP:LISTEN > /dev/null 2>&1; then \
		echo "Port $$port is already in use (likely a local PostgreSQL install)."; \
		echo "Docker may still report success, but connections could silently"; \
		echo "reach that other process instead of this container - see"; \
		echo "docker-compose.yml. Use a different port:"; \
		echo "  VOLVE_PG_PORT=5433 make docker-up"; \
		exit 1; \
	fi
	docker compose up -d
	@echo ""
	@echo "PostgreSQL 17 running in Docker. Before make setup/load/check/app:"
	@echo "  export PGHOST=localhost PGUSER=postgres"
	@echo "  (add PGPORT=5433 too if you started this with VOLVE_PG_PORT=5433 -"
	@echo "  see docker-compose.yml if port 5432 is already taken locally)"

docker-down:
	docker compose down
