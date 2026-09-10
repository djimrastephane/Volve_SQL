"""
test_bench_nlsql.py

app/bench_nlsql.py's checker functions - pure functions over a DataFrame
and a ground-truth dict, no database or Ollama connection needed.

TestCheckTopEntityValueRobustness reproduces a real crash found while
actually running the 2026-09-09-remediated benchmark against the real
dataset and all 5 previously-tested models: a value_hints token matched
a well-NAME column instead of a numeric one on one model's attempt,
and float(actual_value) raised ValueError, killing the entire benchmark
run instead of failing just that one attempt. Not a hypothetical -
this is the actual failure, reproduced minimally.
"""

from __future__ import annotations

import pandas as pd

import bench_nlsql as b


class TestCheckTopEntityValueRobustness:
    def test_degrades_to_name_only_when_hint_matches_a_non_numeric_column(self):
        """The real crash: value_hints=(..., "n") matched a column like
        "wellbore_name" (contains the letter "n") instead of a numeric
        count column, and float() on a well name raised ValueError."""
        df = pd.DataFrame({
            "wellbore_name": ["15/9-F-4"],
            "another_name_column": ["15/9-F-4"],
        })
        gt = {"most_transitions_well": "15/9-F-4", "most_transitions_value": 124}
        check = b.check_top_entity(
            "most_transitions_well", value_key="most_transitions_value", value_hints=("n",)
        )
        ok, note = check(df, gt)  # must not raise
        assert ok is True  # name still matches - degrades to name-only, doesn't fail the whole check
        assert "not numeric" in note or "not identified" in note

    def test_validates_a_correct_numeric_value(self):
        df = pd.DataFrame({"wellbore_name": ["15/9-F-12"], "total_oil": [4579609.55]})
        gt = {"top_oil_well": "15/9-F-12", "top_oil_value": 4579609.55}
        check = b.check_top_entity("top_oil_well", value_key="top_oil_value", value_hints=("total_oil",))
        ok, note = check(df, gt)
        assert ok is True
        assert "value:" in note

    def test_rejects_a_wrong_numeric_value_with_the_right_name(self):
        """The exact gap finding 16 flagged: a query with the right well
        name but a wrong number must not pass."""
        df = pd.DataFrame({"wellbore_name": ["15/9-F-12"], "total_oil": [999.0]})
        gt = {"top_oil_well": "15/9-F-12", "top_oil_value": 4579609.55}
        check = b.check_top_entity("top_oil_well", value_key="top_oil_value", value_hints=("total_oil",))
        ok, note = check(df, gt)
        assert ok is False


class TestCheckProductionHistory:
    def test_rejects_wrong_row_count(self):
        gt = {"history_row_count": 3056, "history_min_date": "2008-02-12", "history_max_date": "2016-09-17"}
        df = pd.DataFrame({"production_date": pd.date_range("2008-02-12", periods=10)})
        ok, note = b.check_production_history(df, gt)
        assert ok is False
        assert "3056" in note

    def test_accepts_matching_row_count_and_date_range(self):
        dates = pd.date_range("2008-02-12", "2016-09-17", freq="D")
        gt = {
            "history_row_count": len(dates),
            "history_min_date": str(dates.min().date()),
            "history_max_date": str(dates.max().date()),
        }
        df = pd.DataFrame({"production_date": dates})
        ok, note = b.check_production_history(df, gt)
        assert ok is True
