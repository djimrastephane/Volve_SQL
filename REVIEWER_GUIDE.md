# Reviewer Guide

One page: what this project demonstrates, how to run it in under 5 minutes
without any licensed data or local LLM, and which files are worth your time
if you're skimming. The full story is in [`README.md`](README.md) — this is
the fast path into it, not a replacement for it.

## What this proves

- **SQL beyond CRUD.** `sql/06_analysis.sql` reconstructs well downtime
  episodes with a "gaps and islands" window-function pattern (`LAG()` +
  running `SUM()` as an episode ID), ranks/normalizes production with
  `DISTINCT ON`, `ROW_NUMBER() OVER (PARTITION BY ...)`, and exact-date
  peak-vs-checkpoint comparisons — not just `SELECT`/`JOIN`/`GROUP BY`.
- **Data quality as a first-class discipline, not a notebook aside.**
  `notebooks/02_data_quality.ipynb`'s findings become executable, re-runnable
  checks in `sql/04_quality_checks.sql` (27 checks: structural + the
  documented DQ-001…DQ-006 exception populations), and the schema's `CHECK`
  constraints (`sql/02_create_tables.sql`) encode a deliberate `0 ≠ NULL`
  distinction throughout — e.g. `CHECK (bore_oil_vol >= 0)` passes `NULL`
  (unmeasured) but rejects negative values, and `bore_wat_vol` deliberately
  carries no non-negativity constraint at all (DQ-005: negative values are a
  real, documented source anomaly, not a defect to hide).
- **Security-mindedness, not just a working feature.** The dashboard connects
  as a least-privilege role (`sql/07_app_role.sql`) with zero grant on
  `core`/`raw`, enforced by PostgreSQL itself, not application code. "Ask the
  Data" (free-text → SQL via a local LLM) validates every generated statement
  with a real SQL parser (`app/nlsql.py`, `sqlglot`) — exact view allowlist,
  a full-tree walk that catches even a write hidden inside a CTE, all before
  that same role/grant boundary would catch it anyway. Defense in depth,
  each layer independent of the others.
- **Reproducibility.** `.github/workflows/ci.yml` runs on every push: SQL
  lint, a schema-only dry run, a synthetic-data end-to-end load through the
  real loader, and 76 pytest tests — genuinely green (verified against a
  from-scratch, password-auth-required Postgres cluster, not just a
  developer machine that already had convenient defaults sitting around).
- **Product judgment on the dashboard.** Five pages, each answering a
  different question (Field Overview: what happened to the field; Well
  Performance: what happened to this well; Well Comparison: how do wells
  compare; Data Quality: can I trust this; Ask the Data: what isn't covered
  above — see the page/views/questions table in
  [`app/README.md`](app/README.md#pages)) — not a single page with every
  chart bolted on. Missing data is never silently coerced to zero or hidden;
  every page distinguishes "0" (a real reading) from "None"/"n/a" (nothing
  recorded), including in the click-to-navigate tables and the free-text
  Q&A results.

## How to run (no license, no LLM — under 5 minutes)

No local PostgreSQL install? One extra step first:

```bash
make docker-up                        # PostgreSQL 17 in Docker (docker-compose.yml)
export PGHOST=localhost PGUSER=postgres
```

Then, either way:

```bash
make setup         # venv + deps + schema
make load-fixture   # loads a tiny synthetic 2-well workbook, not the real data
make check          # sqlfluff + pytest (76 tests)
make app            # http://localhost:8501
```

`make help` lists every target. The real Volve workbook is licensed and not
redistributed (`data/README.md`) — `make load` uses it if you have it placed
at `data/raw/Volve production data.xlsx`; `make load-fixture` is the no-data
alternative that still exercises the full loader → schema → dashboard path.
"Ask the Data" (the NL-to-SQL page) additionally needs a local
[Ollama](https://ollama.com) server; every other page works without it.

## Best files to inspect

If you only read three files, make them the first three below.

| File | Why |
|---|---|
| [`sql/06_analysis.sql`](sql/06_analysis.sql) | The window-function/CTE work — episode reconstruction, rankings, decline analysis. |
| [`sql/02_create_tables.sql`](sql/02_create_tables.sql) | Constraint design with rationale in the comments, not just column types. |
| [`app/nlsql.py`](app/nlsql.py) | The NL-to-SQL safety layers — parser-based validation, exact allowlisting. |
| [`sql/04_quality_checks.sql`](sql/04_quality_checks.sql) | Notebook findings turned into re-runnable, numbered checks. |
| [`app/queries.py`](app/queries.py) | Every dashboard query in one place, each documented against the SQL question it answers. |
| [`app/views/field_overview.py`](app/views/field_overview.py) | A representative dashboard page — see how missing/NULL data is surfaced, not hidden. |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | What's actually verified on every push, and why each job exists. |
| [`tests/conftest.py`](tests/conftest.py) | How the test suite handles "no database available" gracefully. |
| [`Makefile`](Makefile) | The one-command path documented above, in full. |
| [`notebooks/02_data_quality.ipynb`](notebooks/02_data_quality.ipynb) | Where the DQ-001…DQ-006 findings above actually come from. |

For the full narrative — business framing, architecture diagram, the
complete data model, every engineering question and finding, and the
documented limitations — see [`README.md`](README.md).
