"""
test_nlsql.py

app/nlsql.py's SQL validation and cleaning - pure functions, no database
or Ollama connection needed. This is exactly the layer-1 defense
described in nlsql.py's own docstring ("Defense in depth before any
generated SQL is executed"): these tests pin down what it does and does
not catch, on its own, before the real enforcement (the volve_app role
having no grant on core/raw) ever comes into play.

_validate_sql() parses with sqlglot (dialect=postgres) rather than
matching keywords/schema names against the raw SQL text with regex. Two
concrete things that motivated the switch, both verified here: a regex
keyword blocklist can't see into a CTE (`WITH x AS (DELETE FROM ...
RETURNING *) SELECT * FROM x` is a real Postgres statement whose *outer*
shape is a harmless-looking SELECT), and a schema-prefix regex can't tell
a CTE that happens to be named "raw" from an actual reference to the raw
schema - false-flagging query patterns the few-shot examples themselves
use.
"""

from __future__ import annotations

import pytest

import nlsql


class TestValidateSql:
    def test_accepts_plain_select(self):
        nlsql._validate_sql("SELECT wellbore_name FROM analytics.vw_well_lifetime_summary")

    def test_accepts_with_select(self):
        nlsql._validate_sql("WITH x AS (SELECT 1) SELECT * FROM x")

    def test_accepts_lowercase_select(self):
        nlsql._validate_sql("select 1")

    def test_accepts_every_few_shot_example(self):
        """The examples the LLM is actually shown must themselves validate -
        multi-CTE chains, window functions, joins across two allowed views,
        an EXISTS subquery."""
        for _question, sql in nlsql.FEW_SHOT:
            nlsql._validate_sql(sql)

    def test_rejects_empty_string(self):
        with pytest.raises(nlsql.NLSQLError, match="empty query"):
            nlsql._validate_sql("")

    def test_rejects_unparseable_garbage(self):
        """New capability versus the old regex validator, which had no way
        to tell malformed SQL from valid SQL at all - it only pattern-matched
        keywords, so garbage input would have sailed through validation and
        failed later, more confusingly, at execution."""
        with pytest.raises(nlsql.NLSQLError, match="does not parse as valid SQL"):
            nlsql._validate_sql("this is not sql at all !!!")

    @pytest.mark.parametrize("sql", [
        "INSERT INTO analytics.vw_daily_well_performance VALUES (1)",
        "UPDATE analytics.vw_daily_well_performance SET bore_oil_vol = 0",
        "DELETE FROM analytics.vw_daily_well_performance",
        "DROP TABLE core.daily_production",
        "TRUNCATE TABLE core.daily_production",
        "GRANT SELECT ON analytics.vw_daily_well_performance TO PUBLIC",
        "VACUUM core.daily_production",
        "CALL some_proc()",
    ])
    def test_rejects_write_ddl_and_unrecognized_statements(self, sql):
        with pytest.raises(nlsql.NLSQLError, match="not a SELECT/WITH query"):
            nlsql._validate_sql(sql)

    def test_rejects_data_modifying_cte(self):
        """The case a root-type-only check (`isinstance(tree, exp.Select)`)
        would miss: the outer statement genuinely is a SELECT, but a CTE
        inside it performs a real DELETE with side effects - confirmed this
        is valid, parseable PostgreSQL before writing the check that catches
        it (find_all walks the whole tree, not just the root)."""
        sql = (
            "WITH deleted AS (DELETE FROM core.daily_production "
            "WHERE npd_well_bore_code = 1 RETURNING *) SELECT * FROM deleted"
        )
        with pytest.raises(nlsql.NLSQLError, match="write/DDL operation"):
            nlsql._validate_sql(sql)

    def test_rejects_multiple_statements_explicitly(self):
        """Explicit rejection, not the old silent truncate-to-first-statement -
        see _clean_sql's comment for why that changed."""
        with pytest.raises(nlsql.NLSQLError, match="exactly one SQL statement"):
            nlsql._validate_sql("SELECT 1; DROP TABLE core.daily_production")

    def test_rejects_statement_not_starting_with_select_or_with(self):
        with pytest.raises(nlsql.NLSQLError, match="not a SELECT/WITH query"):
            nlsql._validate_sql("EXPLAIN SELECT 1")

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM core.daily_production",
        "SELECT * FROM raw.daily_production_source",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.columns",
    ])
    def test_rejects_non_analytics_schema_references(self, sql):
        with pytest.raises(nlsql.NLSQLError, match="not one of the allowed analytics views"):
            nlsql._validate_sql(sql)

    def test_rejects_analytics_schema_object_not_in_exact_allowlist(self):
        """The old validator only checked the schema PREFIX (blocklist:
        reject core/raw/pg_catalog/...), so a made-up name that happens to
        live under analytics. would have sailed through - this is an exact
        allowlist (only these 5 names), a strictly stronger check the old
        approach structurally couldn't express."""
        with pytest.raises(nlsql.NLSQLError, match="not one of the allowed analytics views"):
            nlsql._validate_sql("SELECT * FROM analytics.vw_totally_made_up")

    def test_accepts_analytics_schema_reference(self):
        nlsql._validate_sql("SELECT * FROM analytics.vw_daily_well_performance")

    def test_accepts_join_across_two_allowed_views(self):
        nlsql._validate_sql(
            "SELECT a.wellbore_name FROM analytics.vw_daily_well_performance a "
            "JOIN analytics.vw_well_lifetime_summary b ON a.npd_well_bore_code = b.npd_well_bore_code"
        )

    def test_cte_named_raw_is_correctly_accepted(self):
        """The false positive the old regex validator had (see this file's
        module docstring): _NON_ANALYTICS_SCHEMA matched the literal text
        "raw." anywhere, including a CTE alias that merely happens to be
        named "raw" and never touches the raw schema. Parsing distinguishes
        a CTE reference from an external table reference structurally, so
        this now validates correctly instead of being wrongly rejected."""
        sql = "WITH raw AS (SELECT 1 AS x) SELECT raw.x FROM raw"
        nlsql._validate_sql(sql)

    def test_cte_shadowing_a_real_view_name_is_still_just_a_cte(self):
        """An unqualified reference to a CTE aliased with the same bare
        name as a real view never actually touches that view - only a
        schema-qualified analytics.vw_... reference does."""
        sql = "WITH vw_well_lifetime_summary AS (SELECT 1 AS x) SELECT x FROM vw_well_lifetime_summary"
        nlsql._validate_sql(sql)


class TestCleanSql:
    def test_strips_markdown_code_fence(self):
        raw = "```sql\nSELECT 1\n```"
        assert nlsql._clean_sql(raw) == "SELECT 1"

    def test_strips_bare_code_fence(self):
        raw = "```\nSELECT 1\n```"
        assert nlsql._clean_sql(raw) == "SELECT 1"

    def test_strips_single_trailing_semicolon(self):
        assert nlsql._clean_sql("SELECT 1;") == "SELECT 1"

    def test_preserves_multiple_statements_for_validator_to_reject(self):
        """Deliberately does NOT truncate at the first ";" the way this used
        to - that silently discarded a second statement the user never saw
        was ever there. _validate_sql's statement-count check is what
        catches and reports this now, so _clean_sql must leave it intact."""
        raw = "SELECT 1; SELECT 2"
        assert nlsql._clean_sql(raw) == "SELECT 1; SELECT 2"

    def test_strips_surrounding_whitespace(self):
        assert nlsql._clean_sql("  \n SELECT 1 \n  ") == "SELECT 1"


class TestSourceViews:
    def test_extracts_single_view(self):
        sql = "SELECT * FROM analytics.vw_daily_well_performance"
        assert nlsql.source_views(sql) == ["analytics.vw_daily_well_performance"]

    def test_extracts_and_dedupes_multiple_views(self):
        sql = (
            "SELECT * FROM analytics.vw_daily_well_performance a "
            "JOIN analytics.vw_well_lifetime_summary b ON true "
            "JOIN analytics.vw_daily_well_performance c ON true"
        )
        assert nlsql.source_views(sql) == [
            "analytics.vw_daily_well_performance",
            "analytics.vw_well_lifetime_summary",
        ]

    def test_no_views_returns_empty_list(self):
        assert nlsql.source_views("SELECT 1") == []

    def test_cte_only_reference_not_reported_as_a_source_view(self):
        sql = "WITH x AS (SELECT 1 AS n) SELECT n FROM x"
        assert nlsql.source_views(sql) == []

    def test_unparseable_sql_returns_empty_list_not_an_exception(self):
        """source_views() is a display helper, not a security check - it
        should degrade quietly rather than raise for input _validate_sql
        would already have rejected before this is ever called on it."""
        assert nlsql.source_views("not valid sql !!!") == []


class TestNLSQLError:
    def test_carries_sql_when_provided(self):
        exc = nlsql.NLSQLError("bad query", sql="SELECT 1")
        assert exc.sql == "SELECT 1"

    def test_sql_defaults_to_none(self):
        exc = nlsql.NLSQLError("bad query")
        assert exc.sql is None
