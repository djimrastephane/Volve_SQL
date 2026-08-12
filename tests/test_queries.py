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

    def test_well_b_shutdown_and_restart_no_oil_values(self):
        episodes = q.well_downtime_episodes(FIXTURE_WELL_B_CODE)
        assert len(episodes) == 1
        ep = episodes.iloc[0]
        assert ep["shutdown_date"] == pd.Timestamp("2020-01-05")
        assert ep["restart_date"] == pd.Timestamp("2020-01-06")
        assert ep["oil_before"] is None or pd.isna(ep["oil_before"])
        assert ep["oil_after"] is None or pd.isna(ep["oil_after"])


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
