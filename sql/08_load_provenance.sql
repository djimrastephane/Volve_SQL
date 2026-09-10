-- =============================================================================
-- 08_load_provenance.sql
--
-- Minimal load provenance: one row per COMMITTED load run (src/load_postgres.py
-- inserts it inside the same transaction as the load itself, right before
-- commit - a failed load rolls back everything, including this row, so
-- every row here really did become the live dataset, by construction, not
-- an attempt that was later reverted).
--
-- Added for two things the 2026-09-09 security review flagged as missing:
--   1. Provenance - which source file (and its content hash) produced the
--      data currently in core/analytics, and when.
--   2. A committed dataset revision the dashboard's cache keys can depend
--      on (app/db.py's get_dataset_revision()), so a successful reload
--      invalidates every cached dashboard query at once instead of
--      leaving some widgets showing the old dataset until their
--      individual TTL happens to expire.
--
-- analytics.vw_load_revision exists so the dashboard's volve_app role -
-- which has zero grant on core, full stop, everywhere else in this
-- project - never needs a core grant just to read a revision counter.
-- This is the one thing about a load that is not business data and safe
-- to expose the same way every other analytics.* view is: through a view,
-- not a direct core grant.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW, like
-- every other file in sql/. Apply after 05_views.sql, before
-- 07_app_role.sql (which grants SELECT on every analytics view, this one
-- included, via ALL TABLES IN SCHEMA analytics).
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.load_runs (
    load_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_path     TEXT NOT NULL,
    source_sha256   TEXT NOT NULL,
    daily_rows      INTEGER NOT NULL,
    monthly_rows    INTEGER NOT NULL,
    wellbore_count  INTEGER NOT NULL,
    status          TEXT NOT NULL
);

COMMENT ON TABLE core.load_runs IS
    'One row per committed load (src/load_postgres.py, inserted in the same transaction as the load, before commit) - provenance and the revision counter app/db.py caches against. A failed load never adds a row here (the whole transaction rolls back), so status is always the same value in practice today; kept as free text rather than a fixed CHECK so a future partial/staged load pipeline is not blocked by this table''s definition. Never truncated by src/load_postgres.py - only appended to, so this is also a load history, not just the latest state.';

CREATE OR REPLACE VIEW analytics.vw_load_revision AS
SELECT load_id, loaded_at, status
FROM core.load_runs
ORDER BY load_id DESC;

COMMENT ON VIEW analytics.vw_load_revision IS
    'Load provenance with source path/hash and row counts deliberately excluded (core.load_runs has them; this view is only what a read-only dashboard role needs to detect "the dataset changed since I last cached a query" - not a provenance audit trail).';
