"""
test_load_postgres.py

Two layers:
  - unit tests for load_postgres.py's pure value converters (_clean,
    _monthly_measurement_to_text) - no database needed, always run
  - integration checks against an already-loaded database (see
    conftest.py's loaded_fixture fixture) - verify that
    tests/fixtures/generate_sample_workbook.py's known synthetic content
    survived load_postgres.py's Excel -> raw -> core pipeline correctly,
    including the specific behaviors it's designed to exercise: a real
    recorded zero staying 0 (not NULL), a genuinely blank day staying
    NULL (not 0), and the documented stray monthly row being excluded
    and counted, not silently dropped.

This suite does not re-run the loader itself (see conftest.py's
docstring for why) - it trusts that whatever loaded the database before
pytest ran (a developer, or .github/workflows/ci.yml's
loader-fixture-test job) already exercised load_postgres.py's own 8
validation checks, which fail loudly and roll back the transaction on
any mismatch. What this suite adds is checking the loaded *content*
against the fixture's specific known values, not just the row counts
load_postgres.py's own checks already cover.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import load_postgres as lp
from conftest import FIXTURE_WELL_A_CODE, FIXTURE_WELL_B_CODE


class TestClean:
    def test_nan_becomes_none(self):
        assert lp._clean(float("nan")) is None

    def test_none_stays_none(self):
        assert lp._clean(None) is None

    def test_pandas_nat_becomes_none(self):
        assert lp._clean(pd.NaT) is None

    def test_timestamp_becomes_date(self):
        import datetime

        result = lp._clean(pd.Timestamp(2020, 1, 15))
        assert result == datetime.date(2020, 1, 15)
        assert isinstance(result, datetime.date)
        assert not isinstance(result, pd.Timestamp)

    def test_numpy_integer_becomes_python_int(self):
        result = lp._clean(np.int64(42))
        assert result == 42
        assert type(result) is int

    def test_numpy_float_becomes_python_float(self):
        result = lp._clean(np.float64(3.14))
        assert result == pytest.approx(3.14)
        assert type(result) is float

    def test_plain_string_passes_through(self):
        assert lp._clean("15/9-F-1 C") == "15/9-F-1 C"

    def test_plain_int_passes_through(self):
        assert lp._clean(7) == 7


class TestMonthlyMeasurementToText:
    def test_nan_becomes_none(self):
        assert lp._monthly_measurement_to_text(float("nan")) is None

    def test_real_number_becomes_decimal_string(self):
        # Genuine numeric cells arrive as pandas float64 - stored as the
        # decimal string raw.monthly_production_source's TEXT column expects.
        assert lp._monthly_measurement_to_text(192.0) == "192.0"
        assert lp._monthly_measurement_to_text(np.float64(2010.0)) == "2010.0"

    def test_stray_unit_string_passes_through_unchanged(self):
        # The documented Section 5 anomaly: literal unit-header text
        # ("hrs", "Sm3") in what would otherwise be a numeric column.
        # raw's job is to preserve it, not decide it's invalid.
        assert lp._monthly_measurement_to_text("hrs") == "hrs"
        assert lp._monthly_measurement_to_text("Sm3") == "Sm3"

    def test_integer_becomes_decimal_string(self):
        assert lp._monthly_measurement_to_text(5) == "5.0"


class TestRowTuples:
    def test_selects_columns_in_order_and_applies_converters(self):
        df = pd.DataFrame({"b": [2, 4], "a": [1, 3]})
        rows = lp._row_tuples(df, ["a", "b"], [lp._clean, lp._clean])
        assert rows == [(1, 2), (3, 4)]

    def test_applies_different_converter_per_column(self):
        df = pd.DataFrame({"n": [192.0], "s": ["hrs"]})
        rows = lp._row_tuples(df, ["n", "s"], [lp._clean, lp._monthly_measurement_to_text])
        assert rows == [(192.0, "hrs")]


# ---------------------------------------------------------------------------
# Integration: verify the fixture's known content actually loaded correctly
# ---------------------------------------------------------------------------

class TestLoadedFixtureContent:
    def test_well_a_and_well_b_present(self, admin_conn, loaded_fixture):
        with admin_conn.cursor() as cur:
            cur.execute("SELECT npd_well_bore_code, npd_well_bore_name FROM core.wellbore ORDER BY npd_well_bore_code")
            rows = cur.fetchall()
        assert rows == [
            (FIXTURE_WELL_A_CODE, "15/9-TEST-A"),
            (FIXTURE_WELL_B_CODE, "15/9-TEST-B"),
        ]

    def test_well_a_real_zero_stays_zero_not_null(self, admin_conn, loaded_fixture):
        """Day 5: on_stream_hrs=0 with oil/gas/water actually recorded as 0
        - a real shut-in day, not a missing measurement (0 != NULL).
        """
        with admin_conn.cursor() as cur:
            cur.execute("""
                SELECT on_stream_hrs, bore_oil_vol, bore_gas_vol, bore_wat_vol
                FROM core.daily_production
                WHERE npd_well_bore_code = %s AND production_date = '2020-01-05'
            """, (FIXTURE_WELL_A_CODE,))
            hrs, oil, gas, water = cur.fetchone()
        assert (hrs, oil, gas, water) == (0, 0, 0, 0)
        assert hrs is not None and oil is not None  # explicit, not implied by == 0

    def test_well_a_blank_day_stays_null_not_zero(self, admin_conn, loaded_fixture):
        """Day 6: every measurement column genuinely blank in the source -
        must load as NULL, never coerced to 0 (see 0 != NULL discussion,
        this project's core data-quality principle)."""
        with admin_conn.cursor() as cur:
            cur.execute("""
                SELECT on_stream_hrs, bore_oil_vol, bore_gas_vol, bore_wat_vol
                FROM core.daily_production
                WHERE npd_well_bore_code = %s AND production_date = '2020-01-06'
            """, (FIXTURE_WELL_A_CODE,))
            row = cur.fetchone()
        assert row == (None, None, None, None)

    def test_well_b_zero_hours_with_positive_injection_preserved(self, admin_conn, loaded_fixture):
        """Day 5: on_stream_hrs=0 alongside bore_wi_vol=180 - the same
        shape of discrepancy DQ-006 documents for the real data. The
        loader must not silently "fix" or drop this."""
        with admin_conn.cursor() as cur:
            cur.execute("""
                SELECT on_stream_hrs, bore_wi_vol
                FROM core.daily_production
                WHERE npd_well_bore_code = %s AND production_date = '2020-01-05'
            """, (FIXTURE_WELL_B_CODE,))
            hrs, wi = cur.fetchone()
        assert hrs == 0
        assert wi == 180

    def test_well_b_never_has_oil_gas_water(self, admin_conn, loaded_fixture):
        with admin_conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM core.daily_production
                WHERE npd_well_bore_code = %s
                  AND (bore_oil_vol IS NOT NULL OR bore_gas_vol IS NOT NULL OR bore_wat_vol IS NOT NULL)
            """, (FIXTURE_WELL_B_CODE,))
            count = cur.fetchone()[0]
        assert count == 0

    def test_stray_monthly_row_excluded_from_core(self, admin_conn, loaded_fixture):
        """3 rows in raw (2 real + 1 stray with blank keys), 2 in core -
        the stray row excluded by name (NPDCode/Year/Month IS NULL), not
        a broad dropna()."""
        with admin_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM raw.monthly_production_source")
            raw_count = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM core.monthly_reference")
            core_count = cur.fetchone()[0]
        assert raw_count == 3
        assert core_count == 2

    def test_stray_row_unit_strings_preserved_in_raw_as_text(self, admin_conn, loaded_fixture):
        with admin_conn.cursor() as cur:
            cur.execute("""
                SELECT on_stream, oil FROM raw.monthly_production_source
                WHERE npdcode IS NULL
            """)
            row = cur.fetchone()
        assert row == ("hrs", "Sm3")

    def test_monthly_reference_sums_reconcile_with_daily(self, admin_conn, loaded_fixture):
        """core.monthly_reference's oil/water-injection sums must match
        the daily rows they were rolled up from exactly (NUMERIC is exact
        arithmetic in PostgreSQL) - the same reconciliation
        04_quality_checks.sql's QC-016 performs."""
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT oil_vol FROM core.monthly_reference WHERE npd_well_bore_code = %s",
                (FIXTURE_WELL_A_CODE,),
            )
            monthly_oil = cur.fetchone()[0]
            cur.execute(
                "SELECT sum(bore_oil_vol) FROM core.daily_production WHERE npd_well_bore_code = %s",
                (FIXTURE_WELL_A_CODE,),
            )
            daily_oil_sum = cur.fetchone()[0]
        assert math.isclose(float(monthly_oil), float(daily_oil_sum), abs_tol=1e-6)


class TestLoadProvenanceAndLock:
    """Phase 4 foundation added alongside the security review remediation:
    core.load_runs (sql/08_load_provenance.sql) and the bounded
    transaction-scoped load lock (_acquire_load_lock)."""

    def test_record_load_provenance_inserts_a_row(self, admin_write_conn, loaded_fixture):
        with admin_write_conn.cursor() as cur:
            cur.execute("SELECT to_regclass('core.load_runs')")
            if cur.fetchone()[0] is None:
                pytest.skip("core.load_runs not present - sql/08_load_provenance.sql not applied")
            cur.execute("SELECT count(*) FROM core.load_runs")
            before = cur.fetchone()[0]
        lp.record_load_provenance(admin_write_conn, {
            "daily_production": 20, "monthly_reference": 2, "wellbore": 2,
        })
        with admin_write_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.load_runs")
            after = cur.fetchone()[0]
        assert after == before + 1

    def test_load_lock_blocks_a_concurrent_holder_and_times_out(self, admin_write_conn):
        """One connection holds the advisory lock for the whole test (its
        transaction is never committed - admin_write_conn rolls back in
        teardown, releasing the lock automatically). A second, independent
        connection attempting the same lock with a short timeout must fail
        with LoadError, not hang."""
        import os

        import psycopg2

        with admin_write_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (lp.LOAD_LOCK_KEY,))

        conn2 = psycopg2.connect(dbname=os.environ.get("VOLVE_DB_NAME", "volve_analytics"))
        try:
            old_timeout = lp.LOAD_LOCK_TIMEOUT_S
            lp.LOAD_LOCK_TIMEOUT_S = 1
            try:
                with pytest.raises(lp.LoadError, match="load lock"):
                    lp._acquire_load_lock(conn2)
            finally:
                lp.LOAD_LOCK_TIMEOUT_S = old_timeout
        finally:
            conn2.rollback()
            conn2.close()


class TestDailyMonthlyReconciliation:
    """Finding 5 of the 2026-09-09 security review: a successful load
    never independently reconciled core.daily_production against
    core.monthly_reference before committing, so a corrupted monthly
    value with unchanged row counts could survive undetected. See
    load_postgres._daily_monthly_reconciliation_checks, called from
    validate_load() before every commit.
    """

    def test_passes_against_the_unmodified_fixture(self, admin_write_conn, loaded_fixture):
        checks = lp._daily_monthly_reconciliation_checks(admin_write_conn)
        failed = [name for name, passed, _ in checks if not passed]
        assert failed == []

    def test_detects_a_corrupted_monthly_value_with_unchanged_row_counts(self, admin_write_conn, loaded_fixture):
        """The audit's exact acceptance case: change one monthly value,
        touch no rows in either table - row counts alone could never
        catch this, only reconciling values does."""
        with admin_write_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.monthly_reference")
            before_count = cur.fetchone()[0]
            cur.execute(
                "UPDATE core.monthly_reference SET oil_vol = oil_vol + 9999 "
                "WHERE npd_well_bore_code = %s",
                (FIXTURE_WELL_A_CODE,),
            )
            cur.execute("SELECT count(*) FROM core.monthly_reference")
            after_count = cur.fetchone()[0]
        assert before_count == after_count  # row count is unchanged - the whole point of this case

        checks = lp._daily_monthly_reconciliation_checks(admin_write_conn)
        results = {name: passed for name, passed, _ in checks}
        assert results["Daily/monthly reconciliation: oil/gas/water/water-injection sums match (tolerance 0.000001)"] is False

    def test_detects_a_monthly_group_missing_from_daily(self, admin_write_conn, loaded_fixture):
        """An unexpected missing monthly group: monthly_reference holds a
        (wellbore, year, month) with no corresponding daily rows at all."""
        with admin_write_conn.cursor() as cur:
            cur.execute(
                "UPDATE core.monthly_reference SET reference_month = 2 "
                "WHERE npd_well_bore_code = %s AND reference_month = 1",
                (FIXTURE_WELL_A_CODE,),
            )
        checks = lp._daily_monthly_reconciliation_checks(admin_write_conn)
        results = {name: passed for name, passed, _ in checks}
        assert results[
            "Daily/monthly reconciliation: (wellbore, year, month) groups in daily but not monthly_reference"
        ] is False
        assert results[
            "Daily/monthly reconciliation: (wellbore, year, month) groups in monthly_reference but not daily"
        ] is False


class TestFixtureSafetyGuard:
    """Finding 11 of the 2026-09-09 security review: `make load-fixture`
    must refuse to truncate/reload a database that already holds a
    non-fixture wellbore, rather than silently replacing a populated
    real-data database's contents with 20 fixture rows. See
    load_postgres._refuse_unless_fixture_target_is_disposable.
    """

    def test_disabled_by_default(self, admin_write_conn):
        """enabled=False (the default when VOLVE_FIXTURE_LOAD is unset) -
        `make load`, not `make load-fixture` - must never apply this
        check, even against a database holding arbitrary content."""
        lp._refuse_unless_fixture_target_is_disposable(admin_write_conn, enabled=False)

    def test_allows_an_empty_wellbore_table(self, admin_write_conn, loaded_fixture):
        """Deliberately empties core.wellbore WITHIN admin_write_conn's
        own never-committed transaction (see conftest.py) rather than
        relying on the database incidentally already being empty (which
        it never is once the fixture is loaded, the only way this suite
        runs against a real database - a skip-if-not-empty version of
        this test would always skip in that environment, which is
        exactly the kind of "unexpected skip" finding 13 of the
        2026-09-09 security review flags CI for silently accepting)."""
        with admin_write_conn.cursor() as cur:
            cur.execute("DELETE FROM core.daily_production")
            cur.execute("DELETE FROM core.monthly_reference")
            cur.execute("DELETE FROM core.wellbore")
            cur.execute("SELECT count(*) FROM core.wellbore")
            assert cur.fetchone()[0] == 0
        lp._refuse_unless_fixture_target_is_disposable(admin_write_conn, enabled=True)

    def test_allows_the_fixtures_own_wellbore_codes(self, admin_write_conn, loaded_fixture):
        """loaded_fixture already guarantees core.wellbore holds exactly
        the fixture's own 2 codes - the guard must not refuse this,
        or `make load-fixture` could never run twice."""
        lp._refuse_unless_fixture_target_is_disposable(admin_write_conn, enabled=True)

    def test_refuses_when_a_non_fixture_wellbore_is_present(self, admin_write_conn):
        """Simulates the exact scenario the review flagged: a database
        holding a wellbore code the fixture never loaded (stand-in for
        the real dataset's 5351/5599/... codes). admin_write_conn is
        rolled back in teardown (conftest.py), so this insert never
        actually reaches any other test's view of the database."""
        with admin_write_conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.wellbore (
                    npd_well_bore_code, npd_well_bore_name, well_bore_code,
                    npd_field_code, npd_field_name, npd_facility_code, npd_facility_name
                ) VALUES (5351, 'REAL-WELL', 'WB-5351', 1, 'VOLVE', 1, 'FAC')
                ON CONFLICT (npd_well_bore_code) DO NOTHING
            """)
        with pytest.raises(lp.LoadError, match="wellbore code"):
            lp._refuse_unless_fixture_target_is_disposable(admin_write_conn, enabled=True)
