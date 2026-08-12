"""
test_nlsql.py

app/nlsql.py's SQL validation and cleaning - pure functions, no database
or Ollama connection needed. This is exactly the layer-1 defense
described in nlsql.py's own docstring ("Defense in depth before any
generated SQL is executed"): these tests pin down what it does and does
not catch, on its own, before the real enforcement (the volve_app role
having no grant on core/raw) ever comes into play.
"""

from __future__ import annotations

import pytest

import nlsql


class TestValidateSql:
    def test_accepts_plain_select(self):
        nlsql._validate_sql("SELECT wellbore_name FROM analytics.vw_well_lifetime_summary")

    def test_accepts_with_select(self):
        nlsql._validate_sql(
            "WITH x AS (SELECT 1) SELECT * FROM x"
        )

    def test_accepts_lowercase_select(self):
        nlsql._validate_sql("select 1")

    def test_rejects_empty_string(self):
        with pytest.raises(nlsql.NLSQLError, match="empty query"):
            nlsql._validate_sql("")

    @pytest.mark.parametrize("sql", [
        "INSERT INTO analytics.vw_daily_well_performance VALUES (1)",
        "UPDATE analytics.vw_daily_well_performance SET bore_oil_vol = 0",
        "DELETE FROM analytics.vw_daily_well_performance",
        "DROP TABLE core.daily_production",
        "SELECT 1; DROP TABLE core.daily_production",
    ])
    def test_rejects_write_and_ddl_statements(self, sql):
        with pytest.raises(nlsql.NLSQLError):
            nlsql._validate_sql(sql)

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
        with pytest.raises(nlsql.NLSQLError, match="outside analytics"):
            nlsql._validate_sql(sql)

    def test_accepts_analytics_schema_reference(self):
        nlsql._validate_sql("SELECT * FROM analytics.vw_daily_well_performance")

    def test_known_limitation_cte_named_raw_is_rejected(self):
        """Documents a real false positive, not a fix for it: _NON_ANALYTICS_SCHEMA
        matches the literal text "raw." anywhere in the statement, including a CTE
        alias that merely happens to be named "raw" and is never a reference to the
        raw schema. The real enforcement layer (volve_app has no grant on raw/core
        regardless of what the SQL says) makes this a false-positive-only risk, not
        a security gap - but it's worth pinning down explicitly rather than leaving
        it as an unverified assumption.
        """
        sql = "WITH raw AS (SELECT 1 AS x) SELECT raw.x FROM raw"
        with pytest.raises(nlsql.NLSQLError, match="outside analytics"):
            nlsql._validate_sql(sql)


class TestCleanSql:
    def test_strips_markdown_code_fence(self):
        raw = "```sql\nSELECT 1\n```"
        assert nlsql._clean_sql(raw) == "SELECT 1"

    def test_strips_bare_code_fence(self):
        raw = "```\nSELECT 1\n```"
        assert nlsql._clean_sql(raw) == "SELECT 1"

    def test_keeps_only_first_statement(self):
        raw = "SELECT 1; SELECT 2"
        assert nlsql._clean_sql(raw) == "SELECT 1"

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


class TestNLSQLError:
    def test_carries_sql_when_provided(self):
        exc = nlsql.NLSQLError("bad query", sql="SELECT 1")
        assert exc.sql == "SELECT 1"

    def test_sql_defaults_to_none(self):
        exc = nlsql.NLSQLError("bad query")
        assert exc.sql is None
