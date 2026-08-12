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
