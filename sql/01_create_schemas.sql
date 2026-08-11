-- =============================================================================
-- 01_create_schemas.sql
--
-- Defines the database architecture for the Volve SQL project:
--
--     raw        Unmodified load of Equinor's source data (Excel -> Postgres).
--                Column names and types stay close to the source. Nothing here
--                is cleaned, deduplicated, or reinterpreted.
--
--     core        Properly typed, constrained, relational tables built from
--                raw: the wellbore dimension, the daily production fact table,
--                and the monthly reference table. Section 24 of
--                notebooks/02_data_quality.ipynb is the evidence base for
--                every table/key/constraint decision made here.
--
--     analytics   SQL views and derived objects for analysis. Nothing is
--                stored here that cannot be regenerated from core.
--
-- A reduced raw -> core -> analytics architecture was chosen over adding a
-- separate staging layer: this is a ~15,634-row single-source portfolio
-- project, not a multi-source pipeline, so staging would add structure
-- without adding value.
--
-- Idempotent: safe to re-run. Creates schemas only if they do not already
-- exist; does not drop or alter anything.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;

COMMENT ON SCHEMA raw IS
    'Unmodified load of Equinor Volve source data. No cleaning, typing, or key enforcement beyond what the source itself provides.';

COMMENT ON SCHEMA core IS
    'Typed, constrained relational tables: wellbore dimension, daily production fact table, monthly reference table. Built from raw per notebooks/02_data_quality.ipynb Section 24.';

COMMENT ON SCHEMA analytics IS
    'Views and derived analytical objects built on core. Contains no data that cannot be regenerated from core.';

-- Verification: confirm all three schemas now exist.
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name IN ('raw', 'core', 'analytics')
ORDER BY schema_name;