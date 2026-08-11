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

## Status

Dashboard: done, tested against the live database (see `queries.py` - each
function is a single, auditable SQL statement, matching the project's
existing SQL style).

"Ask the Data" (natural-language query interface): not yet built - deferred
pending a decision on the NL-to-SQL approach.
