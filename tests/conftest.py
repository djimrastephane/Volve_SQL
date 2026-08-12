"""
conftest.py

Makes app/ and src/ importable as plain modules (they're not packages -
every view under app/views/ imports them the same way, via
sys.path.insert, not a package install) and provides the DB-dependent
fixtures shared across the test suite.

DB-dependent tests assume a database matching sql/01-03,05,07 already
exists and is loaded with tests/fixtures/generate_sample_workbook.py's
synthetic fixture - this suite does not orchestrate that itself (see
.github/workflows/ci.yml's loader-fixture-test job for the exact
sequence: apply schema -> generate fixture -> load via
src/load_postgres.py -> run pytest). Connection follows the same
standard libpq env vars (PGHOST, PGPORT, PGUSER, PGPASSWORD) plus
VOLVE_DB_NAME that src/load_postgres.py and app/db.py already use.

Tests that need a live database are skipped, not failed, when one isn't
reachable - so a contributor without PostgreSQL running locally still
gets a clean, useful run of the pure-function tests (test_nlsql.py's SQL
validation, test_load_postgres.py's value converters).
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

FIXTURE_DAILY_ROWS = 20
FIXTURE_WELLBORE_COUNT = 2
FIXTURE_WELL_A_CODE = 90001  # producer, see tests/fixtures/generate_sample_workbook.py
FIXTURE_WELL_B_CODE = 90002  # injector


def _try_connect(**kwargs):
    try:
        return psycopg2.connect(connect_timeout=3, **kwargs)
    except psycopg2.OperationalError:
        return None


@pytest.fixture(scope="session")
def admin_conn():
    """Connects as whatever PGUSER is set to (the role that applied the
    schema) - used by tests that need to see core/raw directly, not just
    what the app's restricted volve_app role can see.
    """
    import os

    conn = _try_connect(dbname=os.environ.get("VOLVE_DB_NAME", "volve_analytics"))
    if conn is None:
        pytest.skip("No PostgreSQL connection available (see conftest.py docstring)")
    conn.set_session(readonly=True, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def app_conn(admin_conn):
    """Connects as volve_app (sql/07_app_role.sql) - the same restricted,
    analytics-only role the dashboard itself uses (app/db.py). Depends on
    admin_conn only to reuse its skip-if-unreachable behavior, not its
    connection.
    """
    import os

    conn = _try_connect(dbname=os.environ.get("VOLVE_DB_NAME", "volve_analytics"), user="volve_app")
    if conn is None:
        pytest.skip("Could not connect as volve_app - is sql/07_app_role.sql applied?")
    conn.set_session(readonly=True, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def loaded_fixture(admin_conn):
    """Confirms the database actually has the synthetic fixture loaded
    (not just schema, and not the real 15,634-row dataset), so a test
    accidentally run against the wrong database fails with a clear
    message instead of a confusing assertion mismatch deep in a test.
    """
    with admin_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.daily_production")
        count = cur.fetchone()[0]
    if count != FIXTURE_DAILY_ROWS:
        pytest.skip(
            f"core.daily_production has {count} rows, expected the synthetic "
            f"fixture's {FIXTURE_DAILY_ROWS} - load it first with "
            "tests/fixtures/generate_sample_workbook.py + src/load_postgres.py "
            "(see .github/workflows/ci.yml's loader-fixture-test job)"
        )
    return count
