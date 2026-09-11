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
  1. Must parse (as PostgreSQL) into exactly one statement whose root is a
     SELECT, contain no write/DDL node anywhere in the tree - not just at
     the root, since PostgreSQL allows a data-modifying CTE
     (`WITH x AS (DELETE FROM ... RETURNING *) SELECT * FROM x`) whose
     outer shape is a SELECT - reference only the exact 5 analytics views
     in ALLOWED_VIEWS (resolved per SQL scope, not by a flat "any CTE name
     anywhere" walk - see _table_refs()), call only functions on the
     explicit allowlist (see _validate_functions()), and contain no
     SELECT INTO / FOR UPDATE / FOR SHARE clause. See _validate_sql().
     This is a narrow allowlisted grammar, not a growing blocklist: an
     unrecognized function, construct, or reference is refused by
     default, not enumerated by name.
  2. Executed on app/db.py's run_generated_query(), a connection isolated
     from the one dashboard pages use for their own fixed queries (see
     that function's docstring) - as volve_app (sql/07_app_role.sql),
     which has no grant on core or raw regardless of what the SQL says,
     opened read-only, capped with a short statement_timeout, and
     discarded (not returned to a shared cache) after every call so a
     session-level change or cancellation from one request can never
     reach another.
  3. Result rows and bytes are capped during fetch, before full
     materialization (see run_generated_query()'s row/byte budget).
None of these layers depends on the other two being correct.
"""

from __future__ import annotations

import logging
import os
import re

import requests
import sqlglot
import sqlglot.errors
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

from db import run_generated_query

# Structured, not indiscriminate: logs the model, latency, and a rejection
# CATEGORY (e.g. "not on the allowed function list") - never the user's
# free-text question, and never the generated SQL text itself (that stays
# in the UI's own "View SQL" transparency feature for the user who asked
# it; logging it server-side by default would be a second copy of
# potentially sensitive generated content sitting in ops logs for no
# operational benefit this category-level signal doesn't already give).
logger = logging.getLogger("volve.nlsql")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:14b")

# Finding 4/14 of the 2026-09-09 security review: neither the question
# nor the model's raw output had an explicit size bound, and the model
# response envelope was read (resp.json()["response"]) with no guard for
# a non-JSON body, a missing/renamed key, or a non-string value - any of
# which crashed the page instead of failing as an ordinary NLSQLError.
MAX_QUESTION_CHARS = int(os.environ.get("VOLVE_ASK_MAX_QUESTION_CHARS", "1000"))
MAX_MODEL_OUTPUT_CHARS = int(os.environ.get("VOLVE_ASK_MAX_OUTPUT_CHARS", "20000"))

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
  well_type and flow_kind are per-day, NOT a fixed attribute of a well - 2 of
  this field's 7 wells show both 'OP' and 'WI' on different days (one
  briefly, one for a real 144-day early period before it became an
  injector). Neither column exists on any other view. A question about a
  well's overall type ("which wells are producers/injectors") needs each
  well's DOMINANT type (the value with the most days for that well), not
  DISTINCT well_type per well - see the few-shot example below.

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
        "Which wells are producers and which are injectors?",
        "WITH type_counts AS ("
        "  SELECT npd_well_bore_code, wellbore_name, well_type, count(*) AS n "
        "  FROM analytics.vw_daily_well_performance "
        "  GROUP BY npd_well_bore_code, wellbore_name, well_type"
        ") "
        "SELECT DISTINCT ON (npd_well_bore_code) wellbore_name, well_type AS dominant_well_type "
        "FROM type_counts "
        "ORDER BY npd_well_bore_code, n DESC",
    ),
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

# Exact allowlist, not a "reject known-bad schemas" blocklist - anything
# not literally one of these 5 views is refused, including views/tables
# added to any other schema in the future. Kept in sync with SCHEMA_CARD
# by hand (5 entries, low churn) rather than derived from it, since
# SCHEMA_CARD is prose meant for the LLM, not a machine-readable source.
ALLOWED_VIEWS = {
    "analytics.vw_daily_well_performance",
    "analytics.vw_monthly_well_performance",
    "analytics.vw_well_lifetime_summary",
    "analytics.vw_field_monthly_summary",
    "analytics.vw_data_quality_review",
}

# Anything in this tuple, found ANYWHERE in the parsed tree (not just at
# the root), gets the statement refused - covers the data-modifying-CTE
# case above, and exp.Command is sqlglot's fallback node for statement
# types it doesn't have a dedicated parser for (VACUUM, CALL, ...) - "not
# a construct this validator understands" refuses closed, the same as
# "not a SELECT" does, rather than assuming an unrecognized statement is
# probably harmless.
_WRITE_OR_UNKNOWN_NODES = (
    exp.DML, exp.DDL, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Command, exp.Execute, exp.Cache, exp.Set,
)

# Explicit function allowlist, not a blocklist of known-bad names -
# anything not listed here is refused by default, including any future
# PostgreSQL function this project has never heard of. sqlglot gives most
# built-in functions their own node class (checked here by type()); a
# handful of functions this schema legitimately needs (MAKE_DATE) have no
# dedicated class in sqlglot and parse as exp.Anonymous instead, so those
# are allowed by literal lowercased name via _ALLOWED_ANONYMOUS_FUNCTIONS -
# every OTHER exp.Anonymous call (set_config, pg_advisory_lock,
# query_to_xml, pg_sleep, dblink, ... - anything sqlglot doesn't recognize
# as a specific built-in) is refused. This is exactly the "session
# configuration changes, advisory locks, SQL embedded in a string" bypass
# class from the security review: none of those functions appear here, so
# none of them validate, regardless of what string argument they're given.
_ALLOWED_FUNCTIONS = (
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Round, exp.Abs,
    exp.Extract, exp.TimestampTrunc, exp.Coalesce, exp.Nullif,
    exp.RowNumber, exp.Rank, exp.DenseRank, exp.Lag, exp.Lead,
)
_ALLOWED_ANONYMOUS_FUNCTIONS = {"make_date"}


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
    # Strips only a single well-formed trailing semicolon (the model
    # ending its one statement normally) - deliberately does NOT truncate
    # at the first ";" the way this used to, since that would silently
    # discard a second statement instead of letting _validate_sql's
    # statement-count check catch and report it.
    text = text.rstrip(";").strip()
    return text


def _table_refs(tree: exp.Expression) -> set[str]:
    """Every schema-qualified table/view this statement actually reads
    from, resolved per SQL SCOPE via sqlglot's own resolver
    (sqlglot.optimizer.scope.build_scope) rather than a flat "collect
    every CTE alias anywhere in the tree" walk. The flat version cannot
    tell a name that is genuinely in scope (an enclosing CTE, a sibling
    CTE defined earlier in the same WITH list, the query's own alias)
    apart from a same-named CTE that only exists in an unrelated,
    non-enclosing scope - e.g. one nested inside a DIFFERENT CTE
    (`WITH x AS (WITH pg_roles AS (...) SELECT 1) SELECT * FROM pg_roles`,
    where the outer pg_roles reference is not the inner CTE - PostgreSQL
    itself resolves it as the real pg_catalog table). build_scope's
    per-scope `sources` mapping already makes exactly this distinction: a
    source is either a Scope (CTE/subquery/derived table actually in
    scope here) or a Table (an unresolved name that PostgreSQL would look
    up as a real relation) - only the latter is a reference this
    function reports. A forward reference to a CTE defined later in the
    same WITH list - illegal in standard (non-RECURSIVE) SQL - resolves
    the same way: as an unresolved Table, which is exactly right, since
    real PostgreSQL would refuse it as an undefined relation too, not
    silently treat it as the same-named CTE.

    Shared by _validate_sql() (checked against ALLOWED_VIEWS) and
    source_views() (just displayed to the user) so both agree on what
    "referenced" means.
    """
    try:
        root = build_scope(tree)
    except Exception:
        # A tree build_scope can't analyze (e.g. a construct outside what
        # its optimizer models) is not proof of safety - refuse closed by
        # reporting it as an unresolvable reference, same as "doesn't
        # parse" is treated elsewhere in this module.
        return {"(unresolvable - refused)"}
    if root is None:
        return set()
    refs = set()
    for scope in root.traverse():
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                qualified = f"{source.db}.{source.name}" if source.db else source.name
                if qualified:
                    refs.add(qualified)
    return refs


def _validate_functions(tree: exp.Expression) -> None:
    for func in tree.find_all(exp.Func):
        if isinstance(func, exp.Connector):
            # sqlglot models AND/OR (exp.And/exp.Or) as exp.Func subclasses
            # too, since they're technically n-ary callables in its type
            # hierarchy - they are boolean connectives, not a callable
            # server-side function name, so they are not part of what this
            # allowlist is restricting.
            continue
        if isinstance(func, exp.Anonymous):
            name = func.this if isinstance(func.this, str) else str(func.this)
            if name.lower() in _ALLOWED_ANONYMOUS_FUNCTIONS:
                continue
            raise NLSQLError(
                f'Generated statement calls "{name}(...)", which is not on the '
                f"allowed function list - refused to run it."
            )
        if not isinstance(func, _ALLOWED_FUNCTIONS):
            raise NLSQLError(
                f'Generated statement calls "{func.sql_name()}(...)", which is not on the '
                f"allowed function list - refused to run it."
            )


def _validate_sql(sql: str) -> None:
    if not sql:
        raise NLSQLError("The model returned an empty query.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        raise NLSQLError(f"Generated statement does not parse as valid SQL: {exc}") from exc

    if len(statements) == 0:
        raise NLSQLError("The model returned an empty query.")
    if len(statements) > 1:
        raise NLSQLError(
            f"Expected exactly one SQL statement, the model produced {len(statements)} - refused to run it."
        )
    tree = statements[0]

    if not isinstance(tree, exp.Select):
        raise NLSQLError(
            "Generated statement is not a SELECT/WITH query - refused to run it."
        )

    if list(tree.find_all(_WRITE_OR_UNKNOWN_NODES)):
        raise NLSQLError(
            "Generated statement contains a write/DDL operation (possibly nested "
            "inside a CTE) - refused to run it."
        )

    # SELECT INTO creates a table - a write disguised as a SELECT, and not
    # caught by _WRITE_OR_UNKNOWN_NODES since sqlglot models it as a plain
    # exp.Select with an `into` clause, not a DML/DDL node. FOR UPDATE/FOR
    # SHARE take row locks this read-only role has no business holding -
    # the connection is already opened readonly (app/db.py), so PostgreSQL
    # would refuse these too, but rejecting them here means that guarantee
    # doesn't depend on the connection setting being correct.
    if tree.args.get("into") is not None:
        raise NLSQLError(
            "Generated statement uses SELECT INTO, which creates a table - refused to run it."
        )
    if tree.args.get("locks"):
        raise NLSQLError(
            "Generated statement requests a row lock (FOR UPDATE/FOR SHARE) - refused to run it."
        )

    for qualified in _table_refs(tree):
        if qualified not in ALLOWED_VIEWS:
            raise NLSQLError(
                f'Generated statement references "{qualified}", which is not one of the '
                f"allowed analytics views - refused to run it."
            )

    _validate_functions(tree)


_ERROR_CATEGORIES = (
    ("empty", "Question is empty"),
    ("question_too_long", "over the"),
    ("ollama_unreachable", "Could not reach Ollama"),
    ("malformed_response_envelope", "not valid JSON"),
    ("malformed_response_envelope", '"response" field'),
    ("output_too_long", "character limit"),
    ("unparseable_sql", "does not parse as valid SQL"),
    ("empty_generated_query", "returned an empty query"),
    ("multiple_statements", "exactly one SQL statement"),
    ("not_select", "not a SELECT/WITH query"),
    ("write_or_ddl", "write/DDL operation"),
    ("select_into", "SELECT INTO"),
    ("row_lock", "row lock"),
    ("disallowed_reference", "not one of the allowed analytics views"),
    ("disallowed_function", "not on the allowed function list"),
    ("execution_failed", "Query failed:"),
)


def _categorize_error(message: str) -> str:
    """Coarse, fixed category derived from the (also fixed, English)
    exception message - not the message itself, so logs stay useful for
    triage ("how often is category X happening") without needing every
    raise site in this module to also thread a category code through
    NLSQLError's constructor."""
    for category, needle in _ERROR_CATEGORIES:
        if needle in message:
            return category
    return "other"


def generate_sql(question: str, model: str = OLLAMA_MODEL, timeout: int = 90) -> str:
    """model is overridable so app/bench_nlsql.py can compare candidates with
    an identical prompt/schema/few-shot set - the only variable being tested."""
    import time
    t0 = time.monotonic()
    try:
        sql = _generate_sql_inner(question, model, timeout)
    except NLSQLError as exc:
        # Category only (the class of failure), never the question or
        # generated SQL text - see this module's logger comment.
        logger.info(
            "generate_sql model=%s outcome=rejected category=%s duration_ms=%d",
            model, _categorize_error(str(exc)), int((time.monotonic() - t0) * 1000),
        )
        raise
    logger.info(
        "generate_sql model=%s outcome=validated duration_ms=%d",
        model, int((time.monotonic() - t0) * 1000),
    )
    return sql


def _generate_sql_inner(question: str, model: str, timeout: int) -> str:
    if not question or not question.strip():
        raise NLSQLError("Question is empty.")
    if len(question) > MAX_QUESTION_CHARS:
        raise NLSQLError(
            f"Question is {len(question):,} characters, over the {MAX_QUESTION_CHARS:,}-character limit."
        )

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

    # The response envelope itself is untrusted input, same as the SQL it
    # carries: a non-JSON body, a missing/renamed "response" key, or a
    # non-string value must fail as an ordinary NLSQLError, not an
    # unhandled exception the calling page has to know to catch.
    try:
        payload = resp.json()
    except ValueError as exc:
        raise NLSQLError(f"Ollama returned a response that was not valid JSON: {exc}") from exc

    raw_response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(raw_response, str):
        raise NLSQLError(
            "Ollama's response envelope did not contain a text \"response\" field "
            f"(got {type(raw_response).__name__})."
        )
    if len(raw_response) > MAX_MODEL_OUTPUT_CHARS:
        raise NLSQLError(
            f"Model output is {len(raw_response):,} characters, over the "
            f"{MAX_MODEL_OUTPUT_CHARS:,}-character limit - refused."
        )

    sql = _clean_sql(raw_response)
    try:
        _validate_sql(sql)
    except NLSQLError as exc:
        exc.sql = sql
        raise
    return sql


def source_views(sql: str) -> list[str]:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError:
        return []
    if len(statements) != 1:
        return []
    return sorted(_table_refs(statements[0]))


def ask(question: str):
    """
    Returns (sql, dataframe). Raises NLSQLError if generation, validation, or
    execution fails - exc.sql carries the generated statement whenever one
    was produced, even on failure, so the caller can always show what was
    tried, not just that it failed.
    """
    sql = generate_sql(question)
    try:
        df = run_generated_query(sql)
    except Exception as exc:
        raise NLSQLError(f"Query failed: {exc}", sql=sql) from exc
    return sql, df
