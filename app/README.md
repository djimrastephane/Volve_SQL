# Volve Production Analytics dashboard

A Streamlit application for a production engineer, not a Power BI clone.
Reads `analytics.*` views only:

```
Excel -> Python ingestion -> raw -> core -> analytics -> Streamlit -> engineer
```

## Access model

The app connects as `volve_app` (`sql/07_app_role.sql`), a PostgreSQL role
with `SELECT` on `analytics` only - no grant on `core` or `raw` exists for
this role. That is enforced by PostgreSQL itself: even a bug in this app's
code cannot make it read `core`/`raw`, because the database connection
cannot see those schemas at all. The connection is additionally opened
read-only (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`) as a
second, independent guard.

## Run it

```bash
psql -d volve_analytics -f sql/07_app_role.sql   # once, creates the volve_app role
pip install -r requirements.txt
streamlit run app/app.py
```

Open http://localhost:8501.

## Pages

| Page | Views used | Engineering questions it answers |
|---|---|---|
| Field Overview | `vw_field_monthly_summary`, `vw_well_lifetime_summary` | A6, A7, A8, A10 |
| Well Performance | `vw_daily_well_performance`, `vw_well_lifetime_summary` | A4, A11 |
| Well Comparison | `vw_well_lifetime_summary`, `vw_daily_well_performance`, `vw_monthly_well_performance` | A1, A2, A3, A5, A6 (generalized) |
| Data Quality | `vw_data_quality_review` | DQ-001, DQ-003, DQ-004, DQ-005, DQ-006 |

Every A-question originally written against `core.daily_production` /
`core.wellbore` directly in `sql/06_analysis.sql` (A3, A4, A5, A9, A11, A12)
is rebuilt here against `analytics.vw_daily_well_performance` instead - the
dashboard has no other option, since `volve_app` cannot see `core`. That
this works at all is itself evidence the `analytics` layer carries enough
information for real engineering analysis, not just for audit queries.

## Ask the Data

A second page, `views/ask_the_data.py`, turns a free-text question into SQL
against `analytics.*` using a local LLM served by Ollama - no external API
call, no data or schema leaves this machine. The model's only job is
producing SQL; the displayed answer is always the literal query result,
never an LLM paraphrase of it, and the generated SQL is always shown
("View SQL").

Model choice (`OLLAMA_MODEL` in `nlsql.py`, default `qwen2.5-coder:14b`)
was picked from a benchmark, not by assumption - see `bench_nlsql.py` and
`bench_results.md`. The 5 models tested (3 Qwen variants, llama3, mistral)
were simply what happened to already be pulled on the machine this project
was built on - not a claim that they are the best 5 models available, or
that a different set wouldn't do better. `qwen2.5-coder:14b` won among
*those 5* on reliability (zero hallucinated columns across 3 Qwen variants
x 12 questions) and was ~14x faster than the next-best-scoring model, for a
statistically tied correctness rate.

The point of `bench_nlsql.py` is the method, not the specific winner: it
turns `sql/06_analysis.sql`'s 12 questions into a reusable text-to-SQL eval
with live-computed ground truth. Anyone with a different set of local (or
hosted) models available should benchmark their own candidates the same
way - `python app/bench_nlsql.py <model> [model ...]` - rather than assume
this project's result transfers to a different model lineup.

Setup: `ollama pull qwen2.5-coder:14b` (or set `OLLAMA_MODEL` to a model
you already have), then `ollama serve`.

## Status

Both the dashboard and Ask the Data are built and tested against the live
database - `queries.py` (dashboard) and `nlsql.py`/`bench_nlsql.py` (Ask
the Data) are each a single, auditable path from question to SQL to
result, matching the project's existing SQL style.
