"""
test_queries.py

app/queries.py's own functions, called directly (not just the raw SQL
underneath) - these run through db.py's real connection and caching
path (st.cache_resource / st.cache_data), the same one the dashboard
itself uses, as the volve_app role. Values are checked against
tests/fixtures/generate_sample_workbook.py's known synthetic content.

VOLVE_DB_NAME must be set to the database the fixture was loaded into
*before* pytest starts (see conftest.py's module docstring) - db.py
reads it once at import time via a Streamlit-cached connection, not
per-call, so setting it inside a test or fixture would be too late once
another test file has already imported queries/db.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

import queries as q
from conftest import FIXTURE_WELL_A_CODE, FIXTURE_WELL_B_CODE


@pytest.fixture(scope="module", autouse=True)
def _require_fixture(loaded_fixture):
    """Every test in this file needs the loaded fixture - one shared
    autouse dependency instead of repeating it on every test function."""
    return loaded_fixture


class TestListWells:
    def test_returns_exactly_the_two_fixture_wells(self):
        wells = q.list_wells()
        assert sorted(wells["npd_well_bore_code"].tolist()) == [FIXTURE_WELL_A_CODE, FIXTURE_WELL_B_CODE]

    def test_well_type_labels_match_dominant_type(self):
        wells = q.list_wells()
        by_code = wells.set_index("npd_well_bore_code")
        assert by_code.loc[FIXTURE_WELL_A_CODE, "well_type_label"] == "Producer"
        assert by_code.loc[FIXTURE_WELL_B_CODE, "well_type_label"] == "Injector"


class TestWellDaily:
    def test_real_zero_day_is_zero_not_none(self):
        """Same 0 != NULL check as test_load_postgres.py, one layer up:
        this is what the dashboard's own query function returns, not just
        what's sitting in the table."""
        daily = q.well_daily(FIXTURE_WELL_A_CODE)
        day5 = daily.loc[daily["production_date"] == pd.Timestamp("2020-01-05")].iloc[0]
        assert day5["on_stream_hrs"] == 0
        assert day5["bore_oil_vol"] == 0
        assert not pd.isna(day5["on_stream_hrs"])
        assert not pd.isna(day5["bore_oil_vol"])

    def test_blank_day_is_none_not_zero(self):
        daily = q.well_daily(FIXTURE_WELL_A_CODE)
        day6 = daily.loc[daily["production_date"] == pd.Timestamp("2020-01-06")].iloc[0]
        assert pd.isna(day6["on_stream_hrs"])
        assert pd.isna(day6["bore_oil_vol"])

    def test_ten_days_per_well(self):
        assert len(q.well_daily(FIXTURE_WELL_A_CODE)) == 10
        assert len(q.well_daily(FIXTURE_WELL_B_CODE)) == 10


class TestWellLifetime:
    def test_well_a_cumulative_oil(self):
        lifetime = q.well_lifetime(FIXTURE_WELL_A_CODE).iloc[0]
        assert float(lifetime["total_oil"]) == pytest.approx(888.0)

    def test_well_a_peak_daily_oil(self):
        lifetime = q.well_lifetime(FIXTURE_WELL_A_CODE).iloc[0]
        assert float(lifetime["peak_daily_oil"]) == pytest.approx(120.0)  # day 10

    def test_well_b_never_produces_oil(self):
        """A pure injector's total_oil is NULL, not 0 - see the fixture's
        own docstring and this project's 0 != NULL data-quality principle."""
        lifetime = q.well_lifetime(FIXTURE_WELL_B_CODE).iloc[0]
        assert lifetime["total_oil"] is None or pd.isna(lifetime["total_oil"])

    def test_well_b_cumulative_water_injection(self):
        lifetime = q.well_lifetime(FIXTURE_WELL_B_CODE).iloc[0]
        assert float(lifetime["total_water_injection"]) == pytest.approx(2010.0)


class TestWellDowntimeEpisodes:
    def test_well_a_shutdown_and_restart(self):
        """Hand-verified against the fixture's known data: day 5 (0 hrs,
        the real recorded zero) is a shutdown, day 6 is excluded entirely
        (on_stream_hrs IS NULL - the blank day), day 7 (24 hrs) is the
        restart - oil_before is day 4's value, oil_after is day 7's."""
        episodes = q.well_downtime_episodes(FIXTURE_WELL_A_CODE)
        assert len(episodes) == 1
        ep = episodes.iloc[0]
        assert ep["shutdown_date"] == pd.Timestamp("2020-01-05")
        assert ep["restart_date"] == pd.Timestamp("2020-01-07")
        assert float(ep["oil_before"]) == pytest.approx(108.0)
        assert float(ep["oil_after"]) == pytest.approx(112.0)

    def test_well_a_elapsed_vs_observed_vs_unknown_days(self):
        """Finding 7 of the 2026-09-09 security review: this is exactly
        the regression case the fixture was designed for (see
        tests/fixtures/generate_sample_workbook.py's own docstring) -
        day 6's NULL on_stream_hrs reading falls INSIDE well A's only
        episode. Elapsed span is 2 days (Jan 5 -> Jan 7); only day 5 is
        an OBSERVED zero-hours reading (day 6 is unknown, not observed
        inactive) - so observed_inactive_days must be 1, not 2, and
        unknown_days must be exactly the difference, not silently folded
        into "offline"."""
        episodes = q.well_downtime_episodes(FIXTURE_WELL_A_CODE)
        ep = episodes.iloc[0]
        assert int(ep["elapsed_span_days"]) == 2
        assert int(ep["observed_inactive_days"]) == 1
        assert int(ep["unknown_days"]) == 1

    def test_well_b_shutdown_and_restart_no_oil_values(self):
        episodes = q.well_downtime_episodes(FIXTURE_WELL_B_CODE)
        assert len(episodes) == 1
        ep = episodes.iloc[0]
        assert ep["shutdown_date"] == pd.Timestamp("2020-01-05")
        assert ep["restart_date"] == pd.Timestamp("2020-01-06")
        assert ep["oil_before"] is None or pd.isna(ep["oil_before"])
        assert ep["oil_after"] is None or pd.isna(ep["oil_after"])

    def test_well_b_elapsed_equals_observed_no_gap(self):
        """Contrast with well A: well B's episode has no NULL-hours day in
        it, so elapsed span and observed inactive days must agree exactly
        (unknown_days == 0), not just "close"."""
        episodes = q.well_downtime_episodes(FIXTURE_WELL_B_CODE)
        ep = episodes.iloc[0]
        assert int(ep["elapsed_span_days"]) == 1
        assert int(ep["observed_inactive_days"]) == 1
        assert int(ep["unknown_days"]) == 0


class TestRanking:
    def test_well_a_ranks_first_in_oil(self):
        rank = q.ranking()
        well_a = rank.loc[rank["wellbore_name"] == "15/9-TEST-A"].iloc[0]
        assert well_a["oil_rank"] == 1

    def test_well_b_ranks_first_in_water_injection(self):
        rank = q.ranking()
        well_b = rank.loc[rank["wellbore_name"] == "15/9-TEST-B"].iloc[0]
        assert well_b["injection_rank"] == 1


class TestActiveWellsByType:
    def test_january_2020_shows_both_wells_active(self):
        """Both wells have at least one on_stream_hrs > 0 day in the
        fixture's only month - active_wells_by_type()'s zero-fill logic
        should still show exactly 1 Producer and 1 Injector for it, not
        drop the month or show 0."""
        by_type = q.active_wells_by_type()
        jan = by_type.loc[by_type["month_start"] == pd.Timestamp("2020-01-01")]
        by_label = jan.set_index("well_type")["active_wells"]
        assert by_label["Producer"] == 1
        assert by_label["Injector"] == 1


class TestEmptyStateHandling:
    """Finding 14 of the 2026-09-09 security review: several dashboard
    query functions indexed the first/last row of a result without
    checking it existed, crashing the page instead of showing a no-data
    state. These reproduce the two named crash scenarios directly against
    the real query functions - a nonexistent well_code stands in for "a
    well without known operating hours" (well_snapshot's `latest` CTE
    returns zero rows either way); a schema-only/no-data database (the
    other named scenario, "a schema-only database") was additionally
    verified manually against a freshly schema-only database with no
    fixture loaded - not reproducible inside this suite without a second,
    differently-provisioned database connection (see conftest.py's
    module docstring: VOLVE_DB_NAME is read once at import time).
    """

    def test_well_snapshot_does_not_crash_for_a_well_with_no_known_hours_day(self):
        """Reproduces the exact structural bug: well_snapshot()'s `latest`
        CTE requires on_stream_hrs IS NOT NULL, so a well_code with zero
        such rows (a nonexistent code is the simplest way to get zero
        rows deterministically) made the old `FROM latest, first_oil`
        implicit CROSS JOIN return zero rows overall and crash at
        .iloc[0]. The fixed query (LEFT JOIN, first_oil as the guaranteed
        side) must always return exactly one row."""
        snapshot = q.well_snapshot(999999999)
        assert len(snapshot) == 1
        row = snapshot.iloc[0]
        assert row["latest_oil_rate"] is None
        assert pd.isna(row["latest_record_date"])

    def test_peak_oil_query_pattern_returns_one_row_with_nulls_when_no_data_matches(self, admin_conn):
        """Verifies the structural fix in field_lifetime_summary()'s peak
        query directly: a bare SELECT of two scalar subqueries (not
        GROUP BY + ORDER BY + LIMIT 1, which returns ZERO rows when no
        data matches) always returns exactly one row, NULL when the
        underlying data is empty."""
        with admin_conn.cursor() as cur:
            cur.execute("""
                WITH daily_oil AS (
                    SELECT production_date, SUM(bore_oil_vol) AS oil_rate
                    FROM core.daily_production
                    WHERE bore_oil_vol IS NOT NULL AND npd_well_bore_code = -1
                    GROUP BY production_date
                )
                SELECT
                    (SELECT production_date FROM daily_oil ORDER BY oil_rate DESC LIMIT 1) AS peak_date,
                    (SELECT MAX(oil_rate) FROM daily_oil) AS peak_oil_rate
            """)
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0] == (None, None)


class TestActiveWellsByTypeStartMonth:
    """Finding 8 of the 2026-09-09 security review: the first month of
    the Active Wells chart was built from MIN(year) and MIN(month) taken
    INDEPENDENTLY across the whole recorded history, not from MIN of the
    actual earliest date - silently wrong whenever the smallest MONTH
    value on record (across ALL years) is earlier than the month the
    true earliest DATE falls in. These tests exercise the corrected SQL
    fragment (date_trunc('month', MIN(production_date))) directly against
    synthetic, uncommitted rows (admin_write_conn - never committed, see
    conftest.py), not through the cached dashboard query path: the bug
    needs data spanning years the small, single-month fixture doesn't
    have, and mutating the shared fixture's committed content here would
    leak into every other test in this module.
    """

    _BOUNDS_SQL = """
        SELECT date_trunc('month', MIN(production_date))::date AS first_month
        FROM core.daily_production
        WHERE npd_well_bore_code = %s
    """
    # The formula this replaces - kept here only to demonstrate the bug
    # this fix closes, not used anywhere in application code any more.
    _OLD_BUGGY_BOUNDS_SQL = """
        SELECT make_date(MIN(EXTRACT(YEAR FROM production_date)::int),
                          MIN(EXTRACT(MONTH FROM production_date)::int), 1) AS first_month
        FROM core.daily_production
        WHERE npd_well_bore_code = %s
    """

    def test_september_start_with_a_later_january_on_record(self, admin_write_conn, loaded_fixture):
        with admin_write_conn.cursor() as cur:
            # True earliest date: 2007-09-01. A later January (2008-01-15)
            # is what makes MIN(month)=1 independent of MIN(year)=2007 -
            # the exact trigger condition for the old formula's bug.
            cur.execute(
                "INSERT INTO core.daily_production (npd_well_bore_code, production_date, "
                "well_type, flow_kind, on_stream_hrs, bore_oil_vol, bore_gas_vol, bore_wat_vol) "
                "VALUES (%s, '2007-09-01', 'OP', 'production', 24, 50, 1000, 5), "
                "       (%s, '2008-01-15', 'OP', 'production', 24, 55, 1100, 6)",
                (FIXTURE_WELL_A_CODE, FIXTURE_WELL_A_CODE),
            )
            cur.execute(self._OLD_BUGGY_BOUNDS_SQL, (FIXTURE_WELL_A_CODE,))
            old_buggy_result = cur.fetchone()[0]
            cur.execute(self._BOUNDS_SQL, (FIXTURE_WELL_A_CODE,))
            corrected_result = cur.fetchone()[0]

        import datetime
        assert old_buggy_result == datetime.date(2007, 1, 1), (
            "sanity check that this scenario actually reproduces the old bug"
        )
        assert corrected_result == datetime.date(2007, 9, 1)


class TestNormalizedProfilesCalendarSmoothing:
    def test_returns_calendar_based_columns(self):
        """Basic shape check through the real cached query path - the
        detailed calendar-vs-row-count behavior is verified independently
        below against the underlying window mechanism, since the fixture
        (10 consecutive days, no multi-week gap) can't itself distinguish
        a 30-ROW window from a 30-CALENDAR-DAY one."""
        profiles = q.normalized_profiles([FIXTURE_WELL_A_CODE])
        assert "pct_of_peak_smoothed_30d" in profiles.columns
        assert "window_observations" in profiles.columns
        assert not profiles.empty

    def test_window_mechanism_excludes_readings_beyond_30_calendar_days(self, admin_conn):
        """Finding 9 of the 2026-09-09 security review: a 30-ROW average
        (pandas .rolling(30)) silently stretches across however many
        calendar days those 30 observations happen to span when dates are
        missing/irregular - the review measured this at up to 52 real
        calendar days for a nominal "30-day" average. Proves the
        replacement mechanism (SQL RANGE BETWEEN INTERVAL '29 days'
        PRECEDING) directly, independent of any specific well's data:
        a reading 59 days after two earlier ones must NOT be averaged
        with them, and must report a window of exactly 1 observation
        (itself only), not 3."""
        with admin_conn.cursor() as cur:
            cur.execute("""
                WITH v(d, val) AS (VALUES
                    (DATE '2020-01-01', 100.0),
                    (DATE '2020-01-02', 100.0),
                    (DATE '2020-03-01', 999.0)
                )
                SELECT
                    d,
                    AVG(val) OVER (
                        ORDER BY d RANGE BETWEEN INTERVAL '29 days' PRECEDING AND CURRENT ROW
                    ) AS smoothed,
                    COUNT(val) OVER (
                        ORDER BY d RANGE BETWEEN INTERVAL '29 days' PRECEDING AND CURRENT ROW
                    ) AS window_observations
                FROM v ORDER BY d
            """)
            rows = cur.fetchall()
        last_date, last_smoothed, last_n = rows[-1]
        assert last_date == pd.Timestamp("2020-03-01").date()
        assert last_n == 1
        assert float(last_smoothed) == pytest.approx(999.0)


class TestFieldLifetimeSummary:
    def test_total_oil_matches_well_a_alone(self):
        """Well B never produces oil, so the field total should equal
        well A's total exactly."""
        summary = q.field_lifetime_summary()
        assert float(summary["total_oil"]) == pytest.approx(888.0)

    def test_peak_oil_rate_is_well_as_peak_day(self):
        summary = q.field_lifetime_summary()
        assert float(summary["peak_oil_rate"]) == pytest.approx(120.0)
        assert summary["peak_date"] == pd.Timestamp("2020-01-10").date()
