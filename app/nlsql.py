"""
nlsql.py

"Ask the Data": turns a free-text question into a single read-only SQL
statement against analytics.* views, using a local LLM served by Ollama -
no external API call, no data or schema information leaves this machine.

Design principle: the LLM's only job is producing SQL. The answer shown to
the user is always the literal result of executing that SQL against
PostgreSQL, never an LLM paraphrase of it - and the generated SQL is always
shown to the user ("View SQL"), so nothing here is a black box.

Defense in depth before any generated SQL is executed:
  1. Must parse as a single SELECT/WITH statement (no multi-statement, no
     DDL/DML keywords, no reference to core/raw/pg_catalog/information_schema).
  2. Executed on a connection as volve_app (sql/07_app_role.sql), which has
     no grant on core or raw regardless of what the SQL says.
  3. That connection is opened read-only and capped with a 10s
     statement_timeout (app/db.py).
None of these three layers depends on the other two being correct.
"""

from __future__ import annotations

import os
import re

import requests

from db import run_query

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")

SCHEMA_CARD = """\
analytics.vw_daily_well_performance
  one row per (npd_well_bore_code, production_date)
  columns: production_date (date), year (int), month (int),
    npd_well_bore_code (int), wellbore_name (text), well_type (text: 'OP'/'WI'),
    flow_kind (text: 'production'/'injection'), on_stream_hrs (numeric),
    bore_oil_vol (numeric, Sm3/day), bore_gas_vol (numeric, Sm3/day),
    bore_wat_vol (numeric, Sm3/day), bore_wi_vol (numeric, water injection Sm3/day),
    avg_downhole_pressure, avg_downhole_temperature, avg_dp_tubing,
    avg_annulus_press, avg_choke_size_p, avg_whp_p, avg_wht_p, dp_choke_size,
    is_active (boolean, true when on_stream_hrs > 0, NULL when on_stream_hrs IS NULL)

analytics.vw_monthly_well_performance
  one row per (npd_well_bore_code, year, month)
  columns: npd_well_bore_code (int), wellbore_name (text), year (int), month (int),
    on_stream_hours (numeric), oil_volume (numeric, Sm3/month),
    gas_volume (numeric), water_volume (numeric), water_injection_volume (numeric),
    producing_days (int), calendar_records (int)

analytics.vw_well_lifetime_summary
  one row per wellbore (7 rows total)
  columns: npd_well_bore_code (int), wellbore_name (text),
    first_record_date (date), last_record_date (date), recorded_days (int),
    total_on_stream_hours (numeric), total_oil (numeric, cumulative Sm3 - NULL
    for a well that never produced oil, e.g. a pure injector, not zero),
    total_gas (numeric), total_water (numeric), total_water_injection (numeric),
    peak_daily_oil (numeric), peak_daily_gas (numeric), peak_daily_water (numeric),
    number_of_production_days (int), number_of_injection_days (int)

analytics.vw_field_monthly_summary
  one row per calendar month, all wells combined
  columns: year (int), month (int), month_start (date, first of month),
    active_wells (int, wells with on_stream_hrs > 0 that month),
    oil_volume (numeric), gas_volume (numeric), water_volume (numeric),
    water_injection_volume (numeric), on_stream_hours (numeric)

analytics.vw_data_quality_review
  row-level data-quality caution list - a wellbore/date can appear more than once
  columns: npd_well_bore_code (int), production_date (date),
    dq_issue (text: 'DQ-001', 'DQ-003', 'DQ-004', 'DQ-005', 'DQ-006'),
    review_reason (text)
"""

FEW_SHOT = [
    (
        "Which well produced the most oil?",
        "SELECT wellbore_name, total_oil FROM analytics.vw_well_lifetime_summary "
        "ORDER BY total_oil DESC NULLS LAST LIMIT 1",
    ),
    (
        "Which well produced the most oil in 2014?",
        "SELECT wellbore_name, SUM(oil_volume) AS oil_2014 "
        "FROM analytics.vw_monthly_well_performance WHERE year = 2014 "
        "GROUP BY wellbore_name ORDER BY oil_2014 DESC NULLS LAST LIMIT 1",
    ),
    (
        "Show the production history of 15/9-F-1 C.",
        "SELECT production_date, bore_oil_vol, bore_gas_vol, bore_wat_vol "
        "FROM analytics.vw_daily_well_performance WHERE wellbore_name = '15/9-F-1 C' "
        "ORDER BY production_date",
    ),
    (
        "How many wells were active in 2010?",
        "SELECT month_start, active_wells FROM analytics.vw_field_monthly_summary "
        "WHERE EXTRACT(YEAR FROM month_start) = 2010 ORDER BY month_start",
    ),
    (
        "Which wells have the most DQ-004 exceptions?",
        "SELECT w.wellbore_name, count(*) AS record_count "
        "FROM analytics.vw_data_quality_review d "
        "JOIN analytics.vw_well_lifetime_summary w "
        "  ON w.npd_well_bore_code = d.npd_well_bore_code "
        "WHERE d.dq_issue = 'DQ-004' "
        "GROUP BY w.wellbore_name ORDER BY record_count DESC",
    ),
    (
        "Which wells had the largest production decline?",
        "WITH ranked_oil AS ("
        "  SELECT npd_well_bore_code, wellbore_name, production_date, bore_oil_vol, "
        "    ROW_NUMBER() OVER (PARTITION BY npd_well_bore_code ORDER BY bore_oil_vol DESC) AS rn "
        "  FROM analytics.vw_daily_well_performance WHERE bore_oil_vol IS NOT NULL"
        "), peak_only AS ("
        "  SELECT npd_well_bore_code, wellbore_name, production_date AS peak_date, "
        "    bore_oil_vol AS peak_volume FROM ranked_oil WHERE rn = 1"
        ") "
        "SELECT p.wellbore_name, p.peak_volume, d90.bore_oil_vol AS oil_90_days_after_peak, "
        "  ROUND(100.0 * (p.peak_volume - d90.bore_oil_vol) / p.peak_volume, 1) AS pct_decline_90_days "
        "FROM peak_only p "
        "LEFT JOIN analytics.vw_daily_well_performance d90 "
        "  ON d90.npd_well_bore_code = p.npd_well_bore_code AND d90.production_date = p.peak_date + 90 "
        "ORDER BY pct_decline_90_days DESC NULLS LAST",
    ),
]

SYSTEM_PROMPT = f"""You are a PostgreSQL query generator for an oil-field production
database. You may ONLY read from these 5 views, all in the analytics schema:

{SCHEMA_CARD}

Rules:
- Output exactly one PostgreSQL SELECT (or WITH ... SELECT) statement, nothing else.
- No markdown, no code fences, no explanation, no trailing semicolon.
- Only reference the analytics schema views listed above. Never reference core, raw,
  or any other schema or table.
- Never use INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, COPY, or
  any statement other than a single read-only SELECT.
- NULL means "not applicable" (e.g. a pure injector's total_oil), not zero - do not
  COALESCE it to zero unless the question explicitly asks for that.
- Use NULLS LAST when ranking with ORDER BY ... DESC, since PostgreSQL sorts NULL
  first by default and that silently misranks NULL rows as "highest".
"""

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|CALL|"
    r"EXECUTE|VACUUM|MERGE)\b",
    re.IGNORECASE,
)
_NON_ANALYTICS_SCHEMA = re.compile(
    r"\b(core|raw|pg_catalog|information_schema|pg_[a-z_]+)\s*\.", re.IGNORECASE
)


class NLSQLError(Exception):
    """
    Raised when the model can't be reached, its output fails validation, or
    the generated SQL fails to execute. sql carries the generated statement
    whenever one was actually produced, even though the call failed - without
    it, "View SQL (rejected)" had nothing to show for any failure, silently
    contradicting the page's own transparency claim.
    """

    def __init__(self, message: str, sql: str | None = None):
        super().__init__(message)
        self.sql = sql


def _build_prompt(question: str) -> str:
    examples = "\n\n".join(f"Q: {ex_q}\nSQL: {ex_sql}" for ex_q, ex_sql in FEW_SHOT)
    return f"{examples}\n\nQ: {question}\nSQL:"


def _clean_sql(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(sql)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()
    # Keep only the first statement, in case the model added more than one.
    text = text.split(";")[0].strip()
    return text


def _validate_sql(sql: str) -> None:
    if not sql:
        raise NLSQLError("The model returned an empty query.")
    if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE):
        raise NLSQLError(
            "Generated statement is not a SELECT/WITH query - refused to run it."
        )
    if _FORBIDDEN.search(sql):
        raise NLSQLError(
            "Generated statement contains a write/DDL keyword - refused to run it."
        )
    if _NON_ANALYTICS_SCHEMA.search(sql):
        raise NLSQLError(
            "Generated statement references a schema outside analytics - refused to run it."
        )


def generate_sql(question: str, model: str = OLLAMA_MODEL, timeout: int = 90) -> str:
    """model is overridable so app/bench_nlsql.py can compare candidates with
    an identical prompt/schema/few-shot set - the only variable being tested."""
    prompt = _build_prompt(question)
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": model,
                "system": SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise NLSQLError(
            f"Could not reach Ollama at {OLLAMA_HOST} (model {model}). "
            f"Is `ollama serve` running? ({exc})"
        ) from exc

    sql = _clean_sql(resp.json()["response"])
    try:
        _validate_sql(sql)
    except NLSQLError as exc:
        exc.sql = sql
        raise
    return sql


def source_views(sql: str) -> list[str]:
    return sorted(set(re.findall(r"analytics\.\w+", sql, re.IGNORECASE)))


def ask(question: str):
    """
    Returns (sql, dataframe). Raises NLSQLError if generation, validation, or
    execution fails - exc.sql carries the generated statement whenever one
    was produced, even on failure, so the caller can always show what was
    tried, not just that it failed.
    """
    sql = generate_sql(question)
    try:
        df = run_query(sql)
    except Exception as exc:
        raise NLSQLError(f"Query failed: {exc}", sql=sql) from exc
    return sql, df
