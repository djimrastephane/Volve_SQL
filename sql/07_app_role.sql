-- =============================================================================
-- 07_app_role.sql
--
-- Creates a dedicated, least-privilege PostgreSQL role for the Streamlit
-- dashboard (app/): CONNECT on the database, USAGE on analytics only, SELECT
-- on the analytics views only. No grant on raw or core is ever made to this
-- role - the app's "analytics-schema-only" access is a database-enforced
-- fact, not a convention the application code has to honour on its own.
--
-- Local Homebrew PostgreSQL here uses "trust" auth for local/loopback
-- connections (pg_hba.conf), so this role needs no password to be usable in
-- this project's environment. In any deployment with real network exposure,
-- this role should instead get a generated password and a scram-sha-256
-- pg_hba entry - out of scope for a local portfolio project, but worth
-- stating explicitly rather than silently relying on trust auth forever.
--
-- Idempotent: safe to re-run.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'volve_app') THEN
        CREATE ROLE volve_app LOGIN;
    END IF;
END
$$;

COMMENT ON ROLE volve_app IS
    'Least-privilege role for the Streamlit dashboard (app/). SELECT on analytics only - no access to core or raw.';

GRANT CONNECT ON DATABASE volve_analytics TO volve_app;

GRANT USAGE ON SCHEMA analytics TO volve_app;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO volve_app;

-- Any future analytics.vw_* view created by sql/05_views.sql keeps working
-- for this role without a manual re-grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO volve_app;

-- Explicitly NOT done, on purpose:
--   GRANT USAGE ON SCHEMA core TO volve_app;
--   GRANT USAGE ON SCHEMA raw TO volve_app;
-- No grant exists on core/raw for this role, and none of the two statements
-- above ever ran - core/raw remain inaccessible to volve_app by default,
-- which is what "connected directly to PostgreSQL, but only to the
-- analytics schema" means at the database layer, not just in app code.

-- Verification: confirm the role can see analytics.* and nothing in core/raw.
SELECT grantee, table_schema, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = 'volve_app'
ORDER BY table_schema, table_name;
