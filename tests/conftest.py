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
reachable BY DEFAULT - so a contributor without PostgreSQL running
locally still gets a clean, useful run of the pure-function tests
(test_nlsql.py's SQL validation, test_load_postgres.py's value
converters). Finding 13 of the 2026-09-09 security review: that
convenience made it possible for the entire DB-dependent 2/3 of this
suite to silently skip in CI with a green checkmark, if the database
ever failed to come up or the fixture load ever failed before pytest
ran - nothing then or since actually asserted those tests RAN. Setting
VOLVE_STRICT_DB_TESTS=1 (only in CI - see
.github/workflows/ci.yml's loader-fixture-test job, the one job that
both provisions the database itself and needs this guarantee) turns
every "no DB"/"wrong fixture" skip in this file into a hard failure, and
a session-end hook fails the whole run if ANY test skipped for ANY
reason - not only the ones this file's own fixtures gate.
"""

from __future__ import annotations

import os
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

STRICT_DB_TESTS = os.environ.get("VOLVE_STRICT_DB_TESTS") == "1"


def _skip_or_fail(reason: str) -> None:
    if STRICT_DB_TESTS:
        pytest.fail(f"[VOLVE_STRICT_DB_TESTS] {reason}", pytrace=False)
    pytest.skip(reason)


def _try_connect(**kwargs):
    try:
        return psycopg2.connect(connect_timeout=3, **kwargs)
    except psycopg2.OperationalError:
        return None


def pytest_sessionfinish(session, exitstatus):
    """Under VOLVE_STRICT_DB_TESTS, a skip ANYWHERE in the run (not only
    from this file's own fixtures) fails the session - the mandatory-CI
    half of finding 13's fix: "fails ... on ... unexpected skips", not
    only on the specific conditions admin_conn/app_conn/loaded_fixture
    already turn into hard failures above.
    """
    if not STRICT_DB_TESTS or exitstatus != 0:
        return
    terminal_reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = terminal_reporter.stats.get("skipped", []) if terminal_reporter else []
    if skipped:
        names = ", ".join(r.nodeid for r in skipped)
        print(f"\n[VOLVE_STRICT_DB_TESTS] {len(skipped)} test(s) skipped, which strict mode "
              f"treats as a failure: {names}")
        session.exitstatus = 1


@pytest.fixture(scope="session")
def admin_conn():
    """Connects as whatever PGUSER is set to (the role that applied the
    schema) - used by tests that need to see core/raw directly, not just
    what the app's restricted volve_app role can see.
    """
    import os

    conn = _try_connect(dbname=os.environ.get("VOLVE_DB_NAME", "volve_analytics"))
    if conn is None:
        _skip_or_fail("No PostgreSQL connection available (see conftest.py docstring)")
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

    kwargs = {"dbname": os.environ.get("VOLVE_DB_NAME", "volve_analytics"), "user": "volve_app"}
    # See app/db.py's DB_PASSWORD comment - volve_app needs its own
    # password (VOLVE_APP_DB_PASSWORD), not whatever PGPASSWORD happens
    # to be set to for the admin role this suite's admin_conn connects as.
    app_password = os.environ.get("VOLVE_APP_DB_PASSWORD")
    if app_password:
        kwargs["password"] = app_password
    conn = _try_connect(**kwargs)
    if conn is None:
        _skip_or_fail("Could not connect as volve_app - is sql/07_app_role.sql applied?")
    conn.set_session(readonly=True, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture
def admin_write_conn():
    """A writable connection for tests that need to insert scratch state
    (e.g. simulating a populated real dataset to test the fixture-safety
    guard) - function-scoped and ALWAYS rolled back in teardown, never
    committed, so it can never actually mutate the shared session-scoped
    data other tests depend on (loaded_fixture, admin_conn, app_conn).
    """
    import os

    conn = _try_connect(dbname=os.environ.get("VOLVE_DB_NAME", "volve_analytics"))
    if conn is None:
        _skip_or_fail("No PostgreSQL connection available (see conftest.py docstring)")
    try:
        yield conn
    finally:
        conn.rollback()
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
        _skip_or_fail(
            f"core.daily_production has {count} rows, expected the synthetic "
            f"fixture's {FIXTURE_DAILY_ROWS} - load it first with "
            "tests/fixtures/generate_sample_workbook.py + src/load_postgres.py "
            "(see .github/workflows/ci.yml's loader-fixture-test job)"
        )
    return count
