"""
bench_nlsql.py

Text-to-SQL evaluation harness for app/nlsql.py's model choice. Do not treat
OLLAMA_MODEL's default as settled without evidence - this benchmark is that
evidence, built the same way the rest of this project makes decisions:
against real output, not assumption (see sql/03_create_indexes.sql for the
same discipline applied to an indexing decision).

12 questions, one per engineering question in sql/06_analysis.sql (A1-A12),
run against every candidate model with an identical prompt (schema card +
few-shot examples + system rules from app/nlsql.py - the model is the only
variable). Ground truth for each question is computed live from
analytics.* at the start of the run, not hardcoded, so the benchmark stays
correct if the data or views ever change.

Grading, per attempt:
  valid_sql             parsed as a single SELECT/WITH against analytics only
  correct_view          referenced at least one of the view(s) this question
                         can reasonably be answered from
  executes              ran against PostgreSQL without error
  hallucinated_columns  failed specifically with psycopg2.errors.UndefinedColumn
                         (a distinct, attributable failure mode from other errors)
  correct_result        the returned data actually answers the question -
                         checked against live ground truth, not against
                         whether the SQL text resembles the reference query
  respects_dq           for ranking questions: did NOT fall into the
                         NULLS-sort-first trap (sql/06_analysis.sql A1/A2) by
                         ranking a NULL (e.g. a pure injector's total_oil) as
                         the top result
  latency_s             wall-clock seconds for the Ollama call

Run: python app/bench_nlsql.py [model ...]
Defaults to qwen2.5-coder:14b, qwen3:14b, qwen3:8b if no models are given.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg2
import psycopg2.errors

import nlsql
from db import get_connection, run_query

DEFAULT_MODELS = ["qwen2.5-coder:14b", "qwen3:14b", "qwen3:8b"]


# ---------------------------------------------------------------------------
# Ground truth, computed live - never hardcoded.
# ---------------------------------------------------------------------------

def compute_ground_truth() -> dict:
    gt = {}

    gt["top_oil_well"] = run_query("""
        SELECT wellbore_name FROM analytics.vw_well_lifetime_summary
        ORDER BY total_oil DESC NULLS LAST LIMIT 1
    """).iloc[0]["wellbore_name"]

    gt["top_gas_well"] = run_query("""
        SELECT wellbore_name FROM analytics.vw_well_lifetime_summary
        ORDER BY total_gas DESC NULLS LAST LIMIT 1
    """).iloc[0]["wellbore_name"]

    gt["null_oil_well"] = run_query("""
        SELECT wellbore_name FROM analytics.vw_well_lifetime_summary
        WHERE total_oil IS NULL
    """).iloc[0]["wellbore_name"]  # the NULLS-first trap well (pure injector)

    gt["earliest_oil_well"] = run_query("""
        SELECT wellbore_name, MIN(production_date) AS d
        FROM analytics.vw_daily_well_performance
        WHERE bore_oil_vol > 0
        GROUP BY wellbore_name ORDER BY d ASC LIMIT 1
    """).iloc[0]["wellbore_name"]

    gt["peak_oil_well"] = run_query("""
        SELECT wellbore_name FROM analytics.vw_well_lifetime_summary
        ORDER BY peak_daily_oil DESC NULLS LAST LIMIT 1
    """).iloc[0]["wellbore_name"]

    decline_df = run_query("""
        WITH ranked_oil AS (
            SELECT npd_well_bore_code, wellbore_name, production_date, bore_oil_vol,
                ROW_NUMBER() OVER (PARTITION BY npd_well_bore_code ORDER BY bore_oil_vol DESC) AS rn
            FROM analytics.vw_daily_well_performance WHERE bore_oil_vol IS NOT NULL
        ), peak_only AS (
            SELECT npd_well_bore_code, wellbore_name, production_date AS peak_date,
                bore_oil_vol AS peak_volume FROM ranked_oil WHERE rn = 1
        )
        SELECT p.wellbore_name,
            ROUND(100.0 * (p.peak_volume - d90.bore_oil_vol) / p.peak_volume, 1) AS pct_decline_90d
        FROM peak_only p
        LEFT JOIN analytics.vw_daily_well_performance d90
            ON d90.npd_well_bore_code = p.npd_well_bore_code AND d90.production_date = p.peak_date + 90
        ORDER BY pct_decline_90d DESC NULLS LAST LIMIT 1
    """)
    gt["largest_decline_well"] = decline_df.iloc[0]["wellbore_name"]

    gt["highest_water_ratio_direction_increasing"] = True  # known field trend, see A6

    gt["highest_oil_month"] = str(run_query("""
        SELECT month_start FROM analytics.vw_field_monthly_summary
        WHERE oil_volume IS NOT NULL ORDER BY oil_volume DESC LIMIT 1
    """).iloc[0]["month_start"])

    gt["total_water_injection"] = float(run_query("""
        SELECT SUM(total_water_injection) AS v FROM analytics.vw_well_lifetime_summary
    """).iloc[0]["v"])

    gt["max_active_wells"] = int(run_query("""
        SELECT MAX(active_wells) AS v FROM analytics.vw_field_monthly_summary
    """).iloc[0]["v"])

    transitions_df = run_query("""
        WITH daily_state AS (
            SELECT npd_well_bore_code, wellbore_name, production_date, is_active,
                LAG(is_active) OVER (PARTITION BY npd_well_bore_code ORDER BY production_date) AS prev_active
            FROM analytics.vw_daily_well_performance WHERE on_stream_hrs IS NOT NULL
        )
        SELECT wellbore_name, count(*) AS transitions
        FROM daily_state
        WHERE prev_active IS NOT NULL AND is_active IS DISTINCT FROM prev_active
        GROUP BY wellbore_name ORDER BY transitions DESC LIMIT 1
    """)
    gt["most_transitions_well"] = transitions_df.iloc[0]["wellbore_name"]

    dq004_df = run_query("""
        SELECT w.wellbore_name, count(*) AS n
        FROM analytics.vw_data_quality_review d
        JOIN analytics.vw_well_lifetime_summary w ON w.npd_well_bore_code = d.npd_well_bore_code
        WHERE d.dq_issue = 'DQ-004'
        GROUP BY w.wellbore_name ORDER BY n DESC LIMIT 1
    """)
    gt["top_dq004_well"] = dq004_df.iloc[0]["wellbore_name"]

    return gt


# ---------------------------------------------------------------------------
# Result-level checkers. Each takes (df, gt) and returns (bool, note).
# They check what the data says, not whether the SQL resembles a reference
# query - two different queries can produce the same correct answer.
# ---------------------------------------------------------------------------

def _find_name_col(df):
    for c in df.columns:
        if "well" in c.lower() and "name" in c.lower():
            return c
    return None


def check_top_entity(expected_key):
    """
    Trusts the query's own ORDER BY: row 0 is whatever the query claims is
    the answer (that is the whole point of asking "which well X the most" -
    a correct query orders its own result). Earlier version tried to guess
    which numeric column to re-rank by when more than one row came back,
    and picked the *first* numeric column rather than the one actually
    named in the question - on A5 that grabbed peak_volume instead of
    pct_decline_90_days and produced a false negative. Re-ranking here
    would just reintroduce the same class of bug for a different question.
    """
    def _check(df, gt):
        name_col = _find_name_col(df)
        if name_col is None or df.empty:
            return False, "no well-name column / empty result"
        actual = df.iloc[0][name_col]
        expected = gt[expected_key]
        return (actual == expected), f"expected {expected!r}, got {actual!r} (row 0 of {len(df)})"
    return _check


def check_no_null_trap(df, gt):
    """The classic bug: NULL sorts first in DESC, so a pure injector's NULL
    total_oil can get ranked #1 unless the query guards against it. Trusts
    row 0, same reasoning as check_top_entity."""
    name_col = _find_name_col(df)
    if name_col is None or df.empty:
        return None, "not applicable - no ranking to check"
    top_name = df.iloc[0][name_col]
    trap_well = gt["null_oil_well"]
    return (top_name != trap_well), f"row 0: {top_name!r}"


def _find_col(df, *, prefer_kind=None, name_contains=()):
    """Prefer a column whose name matches one of name_contains; only fall
    back to a positional/dtype guess if no name match exists. Guessing
    positionally first is what caused the A5/A8 false negatives below."""
    lowered = {c: c.lower() for c in df.columns}
    for token in name_contains:
        match = next((c for c, lc in lowered.items() if token in lc), None)
        if match:
            return match
    if prefer_kind:
        return next((c for c in df.columns if df[c].dtype.kind == prefer_kind), None)
    return None


def check_month(df, gt):
    if df.empty:
        return False, "empty result"
    date_col = _find_col(df, prefer_kind="M", name_contains=("month_start", "date"))
    if date_col is not None:
        actual = str(df.iloc[0][date_col])
        return (actual[:10] == gt["highest_oil_month"][:10]), f"expected {gt['highest_oil_month']}, got {actual}"
    month_num_col = _find_col(df, name_contains=("month",))
    if month_num_col is None:
        return False, "no date or month column found"
    actual_month = int(df.iloc[0][month_num_col])
    expected_month = int(gt["highest_oil_month"][5:7])
    return (actual_month == expected_month), f"expected month {expected_month}, got {actual_month} (no date column returned)"


def check_total_water_injection(df, gt):
    if df.empty:
        return False, "empty result"
    col = _find_col(df, name_contains=("water_injection", "wi_vol", "injection"))
    if col is None:
        numeric_cols = [c for c in df.columns if df[c].dtype.kind in "fi"]
        if not numeric_cols:
            return False, "no numeric column / empty result"
        col = numeric_cols[0]
    total = df[col].sum() if len(df) > 1 else df.iloc[0][col]
    expected = gt["total_water_injection"]
    ok = abs(total - expected) / expected < 0.02
    return ok, f"expected ~{expected:,.0f}, got {total:,.0f} (column: {col})"


def check_max_active_wells(df, gt):
    if df.empty:
        return False, "empty result"
    col = _find_col(df, name_contains=("active_well", "active"))
    if col is None:
        numeric_cols = [c for c in df.columns if df[c].dtype.kind in "fi"]
        if not numeric_cols:
            return False, "no numeric column / empty result"
        col = numeric_cols[-1]
    actual_max = df[col].max()
    return (int(actual_max) == gt["max_active_wells"]), f"expected {gt['max_active_wells']}, got {actual_max} (column: {col})"


EVAL_SET = [
    dict(
        id="A1", question="Which well produced the most oil over its recorded history?",
        concepts=["analytics view", "SUM / lifetime aggregation", "ORDER BY", "LIMIT"],
        check=check_top_entity("top_oil_well"), dq_check=check_no_null_trap,
        allowed_views={"analytics.vw_well_lifetime_summary"},
    ),
    dict(
        id="A2", question="How do the wells rank by cumulative oil production?",
        concepts=["aggregation", "RANK", "wellbore grouping"],
        check=check_top_entity("top_oil_well"), dq_check=check_no_null_trap,
        allowed_views={"analytics.vw_well_lifetime_summary"},
    ),
    dict(
        id="A2g", question="Which well produced the most gas over its recorded history?",
        concepts=["analytics view", "SUM / lifetime aggregation", "ORDER BY", "LIMIT"],
        check=check_top_entity("top_gas_well"), dq_check=check_no_null_trap,
        allowed_views={"analytics.vw_well_lifetime_summary"},
    ),
    dict(
        id="A3", question="Which well started producing oil earliest?",
        concepts=["MIN", "filter on positive volume", "GROUP BY"],
        check=check_top_entity("earliest_oil_well"), dq_check=None,
        allowed_views={"analytics.vw_daily_well_performance"},
    ),
    dict(
        id="A4", question="Which well reached the highest peak daily oil production?",
        concepts=["MAX / window ranking", "per-well peak"],
        check=check_top_entity("peak_oil_well"), dq_check=check_no_null_trap,
        allowed_views={"analytics.vw_well_lifetime_summary", "analytics.vw_daily_well_performance"},
    ),
    dict(
        id="A5", question="Which wells had the largest production decline?",
        concepts=["window function", "self-comparison to peak", "PARTITION BY"],
        check=check_top_entity("largest_decline_well"), dq_check=None,
        allowed_views={"analytics.vw_daily_well_performance"},
    ),
    dict(
        id="A8", question="Which month had the highest field-wide oil production?",
        concepts=["aggregation", "ORDER BY", "LIMIT"],
        check=check_month, dq_check=None,
        allowed_views={"analytics.vw_field_monthly_summary"},
    ),
    dict(
        id="A7", question="What was the total cumulative water injection volume for the field?",
        concepts=["SUM", "field-wide aggregation"],
        check=check_total_water_injection, dq_check=None,
        allowed_views={"analytics.vw_well_lifetime_summary", "analytics.vw_field_monthly_summary"},
    ),
    dict(
        id="A10", question="What is the largest number of wells that were ever active in the same month?",
        concepts=["MAX", "field-wide time series"],
        check=check_max_active_wells, dq_check=None,
        allowed_views={"analytics.vw_field_monthly_summary"},
    ),
    dict(
        id="A11", question="Which wells experienced shutdown and restart events?",
        concepts=["ON_STREAM_HRS", "LAG", "CASE", "temporal ordering"],
        check=check_top_entity("most_transitions_well"), dq_check=None,
        allowed_views={"analytics.vw_daily_well_performance"},
    ),
    dict(
        id="DQ4", question="Which well has the most on-stream-hours-over-24 data quality exceptions (DQ-004)?",
        concepts=["analytics.vw_data_quality_review", "filter on dq_issue", "GROUP BY", "JOIN for well name"],
        check=check_top_entity("top_dq004_well"), dq_check=None,
        allowed_views={"analytics.vw_data_quality_review"},
    ),
    dict(
        id="A12", question="Show the production history of 15/9-F-1 C.",
        concepts=["filter on wellbore_name", "ORDER BY production_date", "no aggregation needed"],
        check=None, dq_check=None,  # graded on execution + row count only, no single "top" answer
        allowed_views={"analytics.vw_daily_well_performance", "analytics.vw_monthly_well_performance"},
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_one(model: str, item: dict, gt: dict) -> dict:
    result = dict(
        id=item["id"], model=model, question=item["question"],
        valid_sql=False, correct_view=None, executes=False,
        hallucinated_columns=False, correct_result=None, respects_dq=None,
        latency_s=None, sql=None, note="",
    )
    t0 = time.time()
    try:
        sql = nlsql.generate_sql(item["question"], model=model)
        result["latency_s"] = round(time.time() - t0, 1)
        result["sql"] = sql
        result["valid_sql"] = True
    except nlsql.NLSQLError as exc:
        result["latency_s"] = round(time.time() - t0, 1)
        result["note"] = f"generation/validation failed: {exc}"
        return result

    used_views = set(nlsql.source_views(sql))
    result["correct_view"] = bool(used_views & item["allowed_views"])

    try:
        df = run_query(sql)
        result["executes"] = True
    except psycopg2.errors.UndefinedColumn as exc:
        result["hallucinated_columns"] = True
        result["note"] = f"UndefinedColumn: {exc}"
        return result
    except Exception as exc:
        result["note"] = f"execution error: {exc}"
        return result

    if item["check"] is not None:
        ok, note = item["check"](df, gt)
        result["correct_result"] = ok
        result["note"] = note
    else:
        result["correct_result"] = len(df) > 0
        result["note"] = f"{len(df)} rows returned"

    if item["dq_check"] is not None:
        ok, note = item["dq_check"](df, gt)
        result["respects_dq"] = ok
        if not ok:
            result["note"] += f" | DQ check: {note}"

    return result


def summarize(results: list[dict], models: list[str]) -> str:
    lines = []
    metrics = [
        ("Valid PostgreSQL SQL", "valid_sql"),
        ("Correct tables/views", "correct_view"),
        ("Executes successfully", "executes"),
        ("Hallucinated columns", "hallucinated_columns"),
        ("Correct result", "correct_result"),
        ("Respects DQ rules (no NULL trap)", "respects_dq"),
    ]
    header = f"{'Metric':<34}" + "".join(f"{m:>20}" for m in models)
    lines.append(header)
    lines.append("-" * len(header))
    for label, key in metrics:
        row = f"{label:<34}"
        for model in models:
            attempts = [r for r in results if r["model"] == model]
            applicable = [r[key] for r in attempts if r[key] is not None]
            if not applicable:
                row += f"{'n/a':>20}"
                continue
            if key == "hallucinated_columns":
                score = f"{sum(applicable)}/{len(applicable)}"
            else:
                score = f"{sum(1 for v in applicable if v)}/{len(applicable)}"
            row += f"{score:>20}"
        lines.append(row)

    lat_row = f"{'Median latency (s)':<34}"
    for model in models:
        lats = [r["latency_s"] for r in results if r["model"] == model and r["latency_s"] is not None]
        lats.sort()
        med = lats[len(lats) // 2] if lats else float("nan")
        lat_row += f"{med:>20.1f}"
    lines.append(lat_row)

    return "\n".join(lines)


def main():
    models = sys.argv[1:] or DEFAULT_MODELS
    print(f"Models: {models}")
    print("Computing ground truth from analytics.* ...")
    gt = compute_ground_truth()
    for k, v in gt.items():
        print(f"  {k}: {v}")
    print()

    results = []
    for model in models:
        print(f"=== {model} ===")
        for item in EVAL_SET:
            r = run_one(model, item, gt)
            results.append(r)
            status = "OK" if r["correct_result"] in (True, None) and r["executes"] else "FAIL"
            print(f"  [{r['id']:<5}] {status:<4} "
                  f"valid={r['valid_sql']} exec={r['executes']} "
                  f"result={r['correct_result']} dq={r['respects_dq']} "
                  f"{r['latency_s']}s  -  {r['note'][:80]}")
        print()

    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(summarize(results, models))

    return results


if __name__ == "__main__":
    main()
