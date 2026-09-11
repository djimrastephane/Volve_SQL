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


class _FakeResponse:
    """Minimal stand-in for requests.Response - only what generate_sql
    actually touches (raise_for_status, json)."""

    def __init__(self, json_result=None, json_error=None):
        self._json_result = json_result
        self._json_error = json_error

    def raise_for_status(self):
        pass

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_result


class TestGenerateSqlInputOutputGuards:
    """Finding 4/14 of the 2026-09-09 security review: neither the
    question nor the model's raw output had an explicit size bound, and
    the response envelope (resp.json()["response"]) was read with no
    guard for a malformed body - any of which crashed the page instead of
    raising the application's own NLSQLError. No real Ollama server is
    contacted here - requests.post is monkeypatched to return a
    _FakeResponse, isolating these guards from the network call itself.
    """

    def test_rejects_empty_question(self):
        with pytest.raises(nlsql.NLSQLError, match="empty"):
            nlsql.generate_sql("   ")

    def test_rejects_question_over_the_length_limit(self):
        with pytest.raises(nlsql.NLSQLError, match="character limit"):
            nlsql.generate_sql("x" * (nlsql.MAX_QUESTION_CHARS + 1))

    def test_rejects_non_json_response_body(self, monkeypatch):
        monkeypatch.setattr(
            nlsql.requests, "post",
            lambda *a, **k: _FakeResponse(json_error=ValueError("not json")),
        )
        with pytest.raises(nlsql.NLSQLError, match="not valid JSON"):
            nlsql.generate_sql("Which well produced the most oil?")

    def test_rejects_response_envelope_missing_response_key(self, monkeypatch):
        monkeypatch.setattr(
            nlsql.requests, "post",
            lambda *a, **k: _FakeResponse(json_result={"unexpected": "shape"}),
        )
        with pytest.raises(nlsql.NLSQLError, match='"response" field'):
            nlsql.generate_sql("Which well produced the most oil?")

    def test_rejects_response_value_that_is_not_a_string(self, monkeypatch):
        monkeypatch.setattr(
            nlsql.requests, "post",
            lambda *a, **k: _FakeResponse(json_result={"response": {"nested": "object"}}),
        )
        with pytest.raises(nlsql.NLSQLError, match='"response" field'):
            nlsql.generate_sql("Which well produced the most oil?")

    def test_rejects_oversized_model_output(self, monkeypatch):
        huge = "SELECT 1 " + ("x" * nlsql.MAX_MODEL_OUTPUT_CHARS)
        monkeypatch.setattr(
            nlsql.requests, "post",
            lambda *a, **k: _FakeResponse(json_result={"response": huge}),
        )
        with pytest.raises(nlsql.NLSQLError, match="character limit"):
            nlsql.generate_sql("Which well produced the most oil?")

    def test_accepts_a_well_formed_response(self, monkeypatch):
        monkeypatch.setattr(
            nlsql.requests, "post",
            lambda *a, **k: _FakeResponse(
                json_result={"response": "SELECT wellbore_name FROM analytics.vw_well_lifetime_summary"}
            ),
        )
        sql = nlsql.generate_sql("Which well produced the most oil?")
        assert sql == "SELECT wellbore_name FROM analytics.vw_well_lifetime_summary"


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


class TestCteScopeAdversarial:
    """Finding 3 of the 2026-09-09 security review: the old CTE-name
    collector walked the whole tree for any exp.CTE, so an inner CTE's
    name shadowed an outer, unrelated reference to a real relation with
    the same name - accepted below by the validator this replaces. Each
    case here was independently confirmed against the *new*, scope-aware
    _table_refs() (sqlglot.optimizer.scope.build_scope), not just
    against what the old flat walk would have done.
    """

    def test_inner_cte_does_not_shadow_an_outer_reference_to_the_same_name(self):
        """The review's own example: an inner WITH's pg_roles CTE is not
        visible to the outer SELECT - PostgreSQL resolves the outer
        reference as the real pg_catalog table, and so must this
        validator, refusing it as an unresolved non-analytics reference."""
        sql = "WITH x AS (WITH pg_roles AS (SELECT 1) SELECT 1) SELECT rolname FROM pg_roles"
        with pytest.raises(nlsql.NLSQLError, match="not one of the allowed analytics views"):
            nlsql._validate_sql(sql)

    def test_sibling_cte_reference_is_in_scope(self):
        """A CTE may reference an earlier sibling in the same WITH list -
        ordinary, legal SQL that must keep validating."""
        sql = "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT x FROM b"
        nlsql._validate_sql(sql)

    def test_forward_reference_to_a_later_sibling_cte_is_rejected(self):
        """A CTE referencing a sibling defined LATER in the same WITH list
        is not legal PostgreSQL (outside RECURSIVE) - real PostgreSQL
        resolves that name as an external relation lookup and fails if
        one doesn't exist. The validator must reject it the same way, not
        treat it as an in-scope CTE reference just because a same-named
        CTE happens to exist elsewhere in the statement."""
        sql = "WITH a AS (SELECT x FROM b), b AS (SELECT 1 AS x) SELECT * FROM a"
        with pytest.raises(nlsql.NLSQLError, match="not one of the allowed analytics views"):
            nlsql._validate_sql(sql)

    def test_recursive_cte_referencing_only_itself_validates(self):
        """A RECURSIVE CTE legitimately references its own name inside its
        own body - must not be mistaken for an external reference."""
        sql = "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT n FROM r"
        nlsql._validate_sql(sql)

    def test_nested_cte_visible_to_its_own_enclosing_query(self):
        """Contrast with test_inner_cte_does_not_shadow_an_outer_reference:
        a nested CTE IS in scope for the query that actually encloses it."""
        sql = "WITH x AS (WITH y AS (SELECT 1 AS n) SELECT n FROM y) SELECT n FROM x"
        nlsql._validate_sql(sql)


class TestFunctionAllowlist:
    """Finding 2 of the 2026-09-09 security review: the old validator
    placed no restriction on which functions a generated statement could
    call. Each bypass example quoted in that finding is reproduced here,
    confirmed rejected by the new allowlist-based check
    (_validate_functions), not a blocklist of these specific names."""

    @pytest.mark.parametrize("sql", [
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT set_config('default_transaction_read_only', 'off', false)",
        "SELECT pg_advisory_lock(12345)",
        "SELECT query_to_xml('SELECT * FROM pg_catalog.pg_roles', true, false, '')",
    ])
    def test_rejects_the_reviews_accepted_bypass_examples(self, sql):
        with pytest.raises(nlsql.NLSQLError, match="not on the allowed function list"):
            nlsql._validate_sql(sql)

    def test_rejects_repeat_huge_single_row_payload(self):
        """A single-row result can still be huge even with a row cap -
        this function is refused outright, not relied on to be caught by
        a later byte budget."""
        with pytest.raises(nlsql.NLSQLError, match="not on the allowed function list"):
            nlsql._validate_sql("SELECT repeat('x', 100000000)")

    def test_rejects_unknown_anonymous_function(self):
        with pytest.raises(nlsql.NLSQLError, match="not on the allowed function list"):
            nlsql._validate_sql("SELECT pg_sleep(5)")

    def test_accepts_make_date_the_one_allowlisted_anonymous_function(self):
        nlsql._validate_sql("SELECT make_date(2020, 1, 1)")

    def test_accepts_date_trunc(self):
        nlsql._validate_sql(
            "SELECT date_trunc('month', production_date) FROM analytics.vw_daily_well_performance"
        )

    def test_accepts_and_or_boolean_connectives(self):
        """AND/OR are modeled as exp.Func subclasses by sqlglot (they are
        technically n-ary callables in its type hierarchy) but are not a
        callable server-side function name - must not be caught by the
        function allowlist."""
        nlsql._validate_sql(
            "SELECT * FROM analytics.vw_daily_well_performance "
            "WHERE bore_oil_vol > 0 AND (on_stream_hrs > 0 OR on_stream_hrs IS NULL)"
        )

    def test_rejects_select_into(self):
        with pytest.raises(nlsql.NLSQLError, match="SELECT INTO"):
            nlsql._validate_sql("SELECT * INTO evil FROM analytics.vw_daily_well_performance")

    def test_rejects_for_update(self):
        with pytest.raises(nlsql.NLSQLError, match="row lock"):
            nlsql._validate_sql("SELECT * FROM analytics.vw_daily_well_performance FOR UPDATE")


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
