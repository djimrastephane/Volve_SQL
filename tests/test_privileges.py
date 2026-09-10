"""
test_privileges.py

Finding 13 of the 2026-09-09 security review: "There are also no negative
privilege tests proving raw/core denial or dashboard write denial; the
app_conn fixture is not itself that proof." tests/conftest.py has
connected as volve_app since this project's early commits, but nothing
in the suite ever queried through it to prove the role boundary actually
holds at the database layer - every other test exercises it only
indirectly, through app/queries.py's own SELECT-only, analytics-only
queries, which would look identical whether or not the underlying grants
were correct.

These tests connect directly as volve_app (app_conn, sql/07_app_role.sql)
and assert PostgreSQL itself refuses what it should refuse - independent
of app/nlsql.py's parser-based validation (tested separately in
test_nlsql.py) and independent of application code ever calling the
right query in the first place. If sql/07_app_role.sql's grants ever
regress (a broader grant added by mistake, a role recreated with extra
privileges), these are the tests that catch it - not the SQL validator,
which was never meant to be the only line of defense (see app/nlsql.py's
module docstring).
"""

from __future__ import annotations

import psycopg2
import psycopg2.errors
import pytest


class TestCoreAndRawDenied:
    @pytest.mark.parametrize("table", [
        "core.daily_production", "core.wellbore", "core.monthly_reference",
        "raw.daily_production_source", "raw.monthly_production_source",
    ])
    def test_cannot_select_core_or_raw(self, app_conn, table):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with app_conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
        app_conn.rollback()

    @pytest.mark.parametrize("schema", ["core", "raw"])
    def test_no_usage_privilege_on_schema(self, app_conn, schema):
        """Even schema-level USAGE is denied, not only per-table SELECT -
        a real second line of defense, not one indistinguishable from a
        forgotten single-table grant. has_schema_privilege() is a plain
        function anyone can call to check any role's privileges - no
        special access of its own needed to ask the question."""
        with app_conn.cursor() as cur:
            cur.execute("SELECT has_schema_privilege('volve_app', %s, 'USAGE')", (schema,))
            has_usage = cur.fetchone()[0]
        assert has_usage is False


class TestAnalyticsReadAllowed:
    @pytest.mark.parametrize("view", [
        "analytics.vw_daily_well_performance", "analytics.vw_monthly_well_performance",
        "analytics.vw_well_lifetime_summary", "analytics.vw_field_monthly_summary",
        "analytics.vw_data_quality_review", "analytics.vw_downtime_episodes",
        "analytics.vw_load_revision",
    ])
    def test_can_select_every_analytics_view(self, app_conn, view):
        with app_conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {view} LIMIT 1")


class TestWriteDenied:
    """volve_app is read-only both by grant (sql/07_app_role.sql never
    grants INSERT/UPDATE/DELETE on anything) and by connection setting
    (conn.set_session(readonly=True) - app/db.py). Each write attempt
    below exercises the grant itself, independent of the connection
    setting: a fresh, non-readonly connection as the same role, so a
    grant regression is caught even if the readonly connection setting
    were ever accidentally dropped.
    """

    @pytest.fixture
    def app_write_conn(self, app_conn):
        import os

        kwargs = {"dbname": os.environ.get("VOLVE_DB_NAME", "volve_analytics"), "user": "volve_app"}
        password = os.environ.get("VOLVE_APP_DB_PASSWORD")
        if password:
            kwargs["password"] = password
        import conftest

        try:
            conn = psycopg2.connect(connect_timeout=3, **kwargs)
        except psycopg2.OperationalError:
            conftest._skip_or_fail("Could not open a second volve_app connection")
        yield conn
        conn.rollback()
        conn.close()

    def test_cannot_insert_into_core(self, app_write_conn):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with app_write_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO core.wellbore (npd_well_bore_code, npd_well_bore_name, "
                    "well_bore_code, npd_field_code, npd_field_name, npd_facility_code, npd_facility_name) "
                    "VALUES (1, 'x', 'x', 1, 'x', 1, 'x')"
                )
        app_write_conn.rollback()

    def test_cannot_write_through_an_analytics_view(self, app_write_conn):
        """analytics.* objects are plain views over core, with no INSTEAD
        OF rule making them writable - an attempted write must fail the
        same way any other unauthorized write does, not silently no-op."""
        with pytest.raises((psycopg2.errors.InsufficientPrivilege, psycopg2.errors.ObjectNotInPrerequisiteState)):
            with app_write_conn.cursor() as cur:
                cur.execute(
                    "UPDATE analytics.vw_daily_well_performance SET bore_oil_vol = 0 WHERE false"
                )
        app_write_conn.rollback()

    def test_cannot_truncate_anything(self, app_write_conn):
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            with app_write_conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE core.daily_production")
        app_write_conn.rollback()


class TestRoleAttributes:
    """Phase 4 of the security review ("make privilege intent
    executable"): assert the role's own attributes, not only its object
    grants - a role recreated with SUPERUSER or CREATEDB by an unrelated
    admin action would still pass every grant-based test above."""

    def test_volve_app_is_not_superuser_or_createdb_or_createrole(self, admin_conn):
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                "FROM pg_roles WHERE rolname = 'volve_app'"
            )
            row = cur.fetchone()
        assert row is not None, "volve_app role does not exist"
        rolsuper, rolcreatedb, rolcreaterole, rolreplication = row
        assert not rolsuper
        assert not rolcreatedb
        assert not rolcreaterole
        assert not rolreplication
