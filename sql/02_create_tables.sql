-- =============================================================================
-- 02_create_tables.sql
--
-- Table definitions only. No data loading, no views, no quality queries, no
-- analytical SQL. Every design choice below is traceable to a specific
-- section of notebooks/02_data_quality.ipynb.
--
-- Naming convention: source column names are kept, lowercased, with spaces
-- removed (e.g. "On Stream" -> on_stream). Renaming to friendlier business
-- names (DATEPRD -> production_date) happens only in core, never in raw -
-- that renaming is itself a structural decision, and raw's job is to not
-- make structural decisions.
--
-- Numeric type: NUMERIC (exact decimal), not DOUBLE PRECISION/FLOAT, for
-- every measurement column in both raw and core. The source values arrived
-- as Excel/pandas float64, but NUMERIC avoids introducing additional binary
-- rounding on top of whatever the source already contains, and is the
-- conventional PostgreSQL choice for stored measurement data that will be
-- summed and compared (as Section 21's reconciliation does). No fixed
-- precision/scale is set - that would encode a precision assumption this
-- project has not validated (Section 24.8, open question).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS throughout. Re-running this file
-- will not drop or alter a table that already has data in it.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- raw.daily_production_source
--
-- Mirrors the "Daily Production Data" worksheet. Every column that pandas
-- inferred cleanly (Sections 2-3 of the notebook: no contamination found on
-- this sheet) keeps a matching SQL type. No NOT NULL, no CHECK, no UNIQUE
-- beyond the technical id - raw does not enforce business rules.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw.daily_production_source (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY,
    dateprd                     DATE,
    well_bore_code              TEXT,
    npd_well_bore_code          INTEGER,
    npd_well_bore_name          TEXT,
    npd_field_code              INTEGER,
    npd_field_name              TEXT,
    npd_facility_code           INTEGER,
    npd_facility_name           TEXT,
    on_stream_hrs               NUMERIC,
    avg_downhole_pressure       NUMERIC,
    avg_downhole_temperature    NUMERIC,
    avg_dp_tubing               NUMERIC,
    avg_annulus_press           NUMERIC,
    avg_choke_size_p            NUMERIC,
    avg_choke_uom               TEXT,
    avg_whp_p                   NUMERIC,
    avg_wht_p                   NUMERIC,
    dp_choke_size               NUMERIC,
    bore_oil_vol                NUMERIC,
    bore_gas_vol                NUMERIC,
    bore_wat_vol                NUMERIC,
    bore_wi_vol                 NUMERIC,
    flow_kind                   TEXT,
    well_type                   TEXT,
    CONSTRAINT pk_raw_daily_production_source PRIMARY KEY (id)
);

COMMENT ON TABLE raw.daily_production_source IS
    'Unmodified load of the "Daily Production Data" worksheet. One row per source row, including any that would later be excluded or flagged in core. Technical id only - not a business key.';

COMMENT ON COLUMN raw.daily_production_source.id IS
    'Technical row identifier assigned at load time. Not present in the source; raw data is not assumed unique on any business column.';

COMMENT ON COLUMN raw.daily_production_source.npd_well_bore_code IS
    'Same identifier system as raw.monthly_production_source.npdcode - confirmed in notebooks/02_data_quality.ipynb Section 7 (all 7 wellbores match, both directions).';


-- -----------------------------------------------------------------------------
-- raw.monthly_production_source
--
-- Mirrors the "Monthly Production Data" worksheet - deliberately, including
-- its known contamination. Section 5 found one stray non-data row (a units
-- header: "hrs", "Sm3", ...) with every key column NULL, which forces pandas
-- to infer On Stream/Oil/Gas/Water/GI/WI as text rather than numeric. This
-- table preserves that as TEXT rather than silently coercing it - coercion
-- (and excluding the stray row) is a core-layer decision, made explicitly in
-- load_postgres.py, not hidden inside a raw table definition.
--
-- npdcode/year/month are still INTEGER: pandas inferred those as float64
-- only because the stray row leaves them NULL (not because of text
-- contamination), so no information is lost by typing them as nullable
-- integers here.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw.monthly_production_source (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY,
    wellbore_name               TEXT,
    npdcode                     INTEGER,
    year                        INTEGER,
    month                       INTEGER,
    on_stream                   TEXT,
    oil                         TEXT,
    gas                         TEXT,
    water                       TEXT,
    gi                          TEXT,
    wi                          TEXT,
    CONSTRAINT pk_raw_monthly_production_source PRIMARY KEY (id)
);

COMMENT ON TABLE raw.monthly_production_source IS
    'Unmodified load of the "Monthly Production Data" worksheet, including the stray units-header row identified in Section 5. 527 rows expected, not 526.';

COMMENT ON COLUMN raw.monthly_production_source.on_stream IS
    'TEXT, not NUMERIC: the stray non-data row (Section 5) holds the literal string "hrs" here, which would fail numeric parsing at load time. Cast to NUMERIC in core.monthly_reference, after excluding that row.';

COMMENT ON COLUMN raw.monthly_production_source.oil IS
    'TEXT, not NUMERIC: the stray non-data row (Section 5) holds the literal string "Sm3" here. Cast to NUMERIC in core.monthly_reference, after excluding that row.';

COMMENT ON COLUMN raw.monthly_production_source.gas IS
    'TEXT, not NUMERIC: same stray-row contamination as oil. Cast to NUMERIC in core.monthly_reference, after excluding that row.';

COMMENT ON COLUMN raw.monthly_production_source.water IS
    'TEXT, not NUMERIC: same stray-row contamination as oil. Cast to NUMERIC in core.monthly_reference, after excluding that row.';

COMMENT ON COLUMN raw.monthly_production_source.gi IS
    'TEXT, not NUMERIC: same stray-row contamination as oil. Also almost entirely NULL among real rows (Section 5) - gas injection is essentially unused in this dataset.';

COMMENT ON COLUMN raw.monthly_production_source.wi IS
    'TEXT, not NUMERIC: same stray-row contamination as oil. Cast to NUMERIC in core.monthly_reference, after excluding that row.';


-- -----------------------------------------------------------------------------
-- core.wellbore
--
-- Wellbore dimension. Field and facility attributes are folded in rather
-- than split into separate dimension tables: Section 8 confirmed exactly one
-- field and one facility for the entire dataset, with zero wellbores ever
-- associated with more than one of either. This is a Volve-v1 decision
-- (Section 24.6), not a template rule - revisit the moment cardinality
-- exceeds 1 in a future dataset.
--
-- WELL_TYPE and FLOW_KIND are deliberately absent from this table - Section
-- 12 confirmed both are temporal (2 of 7 wellbores show real WELL_TYPE
-- transitions), so they belong on core.daily_production, not here.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.wellbore (
    npd_well_bore_code          INTEGER NOT NULL,
    npd_well_bore_name          TEXT NOT NULL,
    well_bore_code              TEXT NOT NULL,
    npd_field_code               INTEGER NOT NULL,
    npd_field_name               TEXT NOT NULL,
    npd_facility_code            INTEGER NOT NULL,
    npd_facility_name            TEXT NOT NULL,
    CONSTRAINT pk_wellbore PRIMARY KEY (npd_well_bore_code),
    CONSTRAINT uq_wellbore_name UNIQUE (npd_well_bore_name),
    CONSTRAINT uq_wellbore_well_bore_code UNIQUE (well_bore_code)
);

COMMENT ON TABLE core.wellbore IS
    'Wellbore dimension for Volve. Field/facility attributes folded in (Section 8: cardinality 1 for both, confirmed stable per wellbore) rather than modelled as separate dimensions - a v1 decision documented in Section 24.6, not a template default.';

COMMENT ON COLUMN core.wellbore.well_bore_code IS
    'Secondary wellbore identifier in a different format from npd_well_bore_code (e.g. "NO 15/9-F-1 C" vs 7405). Confirmed 1:1 with npd_well_bore_code in both directions (Section 6); not used as the primary key because npd_well_bore_code is numeric and matches the monthly source directly (Section 7).';

COMMENT ON COLUMN core.wellbore.npd_field_code IS
    'Folded in from the daily source rather than a separate field dimension - see table comment. Single value (3420717 / VOLVE) for the entire dataset (Section 8).';

COMMENT ON COLUMN core.wellbore.npd_facility_code IS
    'Folded in from the daily source rather than a separate facility dimension - see table comment. Single value (369304 / MÆRSK INSPIRER) for the entire dataset (Section 8).';


-- -----------------------------------------------------------------------------
-- core.daily_production
--
-- Grain: one row per wellbore per calendar date. Confirmed unique - Section
-- 4 (CONFIRMED verdict) and reconfirmed independently in Section 19 (0
-- duplicate rows, 0 duplicate keys). Production and injection stay in one
-- table (Section 24.7): the 18 WELL_TYPE=WI/FLOW_KIND=production transition
-- rows have no clean split point between the two, and every measurement
-- column is already correctly nullable/contextual rather than needing two
-- narrower tables to avoid sparse columns (Sections 11, 15-17).
--
-- Primary key choice, made deliberately rather than defaulting to a
-- surrogate integer id: (npd_well_bore_code, production_date) is the
-- validated natural grain of this table. A surrogate id would add a layer
-- of indirection with no evidenced benefit here - there is no child table
-- that would need to reference an individual daily_production row by a
-- narrower key, and the composite key is exactly the fact this notebook
-- spent Section 4 confirming. If a future requirement needs a narrower FK
-- target, that is the point to reconsider, not before.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.daily_production (
    npd_well_bore_code          INTEGER NOT NULL,
    production_date             DATE NOT NULL,
    well_type                   TEXT NOT NULL,
    flow_kind                   TEXT NOT NULL,
    on_stream_hrs                NUMERIC,
    avg_downhole_pressure       NUMERIC,
    avg_downhole_temperature    NUMERIC,
    avg_dp_tubing                NUMERIC,
    avg_annulus_press            NUMERIC,
    avg_choke_size_p             NUMERIC,
    avg_choke_uom                TEXT,
    avg_whp_p                    NUMERIC,
    avg_wht_p                    NUMERIC,
    dp_choke_size                NUMERIC,
    bore_oil_vol                 NUMERIC,
    bore_gas_vol                 NUMERIC,
    bore_wat_vol                 NUMERIC,
    bore_wi_vol                  NUMERIC,

    CONSTRAINT pk_daily_production
        PRIMARY KEY (npd_well_bore_code, production_date),

    CONSTRAINT fk_daily_production_wellbore
        FOREIGN KEY (npd_well_bore_code)
        REFERENCES core.wellbore (npd_well_bore_code),

    -- Exactly 2 categories, always, across all 15,634 rows (Section 12.1).
    CONSTRAINT chk_daily_production_well_type
        CHECK (well_type IN ('OP', 'WI')),
    CONSTRAINT chk_daily_production_flow_kind
        CHECK (flow_kind IN ('production', 'injection')),

    -- Non-negativity: enforced only where zero violations were observed
    -- across the full 15,634-row history (Section 24.5). CHECK constraints
    -- pass on NULL in PostgreSQL, so these do not force any column NOT NULL.
    CONSTRAINT chk_daily_production_on_stream_hrs
        CHECK (on_stream_hrs >= 0),                    -- Section 10.1: 0 violations. No upper bound: DQ-004 unresolved.
    CONSTRAINT chk_daily_production_downhole_pressure
        CHECK (avg_downhole_pressure >= 0),             -- Section 15.1: 0 violations
    CONSTRAINT chk_daily_production_downhole_temperature
        CHECK (avg_downhole_temperature >= 0),          -- Section 16.1: 0 violations
    CONSTRAINT chk_daily_production_dp_tubing
        CHECK (avg_dp_tubing >= 0),                     -- Section 15.1: 0 violations
    CONSTRAINT chk_daily_production_annulus_press
        CHECK (avg_annulus_press >= 0),                 -- Section 15.1: 0 violations
    CONSTRAINT chk_daily_production_choke_size_p
        CHECK (avg_choke_size_p >= 0),                  -- Section 17.1: 0 violations. No upper bound: % definition unconfirmed.
    CONSTRAINT chk_daily_production_choke_uom
        CHECK (avg_choke_uom IS NULL OR avg_choke_uom = '%'),  -- Section 12.8: only value ever observed
    CONSTRAINT chk_daily_production_whp_p
        CHECK (avg_whp_p >= 0),                         -- Section 15.1: 0 violations
    CONSTRAINT chk_daily_production_wht_p
        CHECK (avg_wht_p >= 0),                         -- Section 16.1: 0 violations
    CONSTRAINT chk_daily_production_dp_choke_size
        CHECK (dp_choke_size >= 0),                     -- Section 15.1: 0 violations
    CONSTRAINT chk_daily_production_oil_vol
        CHECK (bore_oil_vol >= 0),                      -- Section 13.1: 0 violations
    CONSTRAINT chk_daily_production_gas_vol
        CHECK (bore_gas_vol >= 0),                      -- Section 13.1: 0 violations
    CONSTRAINT chk_daily_production_wi_vol
        CHECK (bore_wi_vol >= 0)                        -- Section 14.1: 0 violations
    -- bore_wat_vol: deliberately NO non-negativity constraint. DQ-005 found
    -- 4 confirmed real negative values (Section 13.1) - a hard constraint
    -- here would reject genuine source rows.
);

COMMENT ON TABLE core.daily_production IS
    'Primary analytical fact table. Grain: one row per (npd_well_bore_code, production_date), confirmed unique in Sections 4 and 19. Production and injection share this table by design (Section 24.7).';

COMMENT ON COLUMN core.daily_production.well_type IS
    'Temporal, not static (Section 12): a wellbore can change WELL_TYPE over its history (2 of 7 do). Do not assume one value per wellbore - that is exactly why this column lives here and not on core.wellbore.';

COMMENT ON COLUMN core.daily_production.flow_kind IS
    'Temporal, not static (Section 12): 1 of 7 wellbores shows a real FLOW_KIND transition. See well_type comment.';

COMMENT ON COLUMN core.daily_production.bore_wat_vol IS
    'No non-negativity constraint - DQ-001 through DQ-006 issue register (Section 23): DQ-005 covers 4 confirmed real negative values plus 1 unexplained zero-hours/positive-production row.';


-- -----------------------------------------------------------------------------
-- core.monthly_reference
--
-- Retained for reconciliation against core.daily_production (Section 21:
-- SUM() of daily by wellbore/year/month matches this table with 0 genuine
-- mismatches across 526 wellbore-months) - not a second fact source for
-- analysis.
--
-- Monthly key handled carefully, per Section 5's finding: the stray
-- non-data row in the source has every key column NULL. Unlike raw, core
-- does not carry that row at all - it is excluded during the raw -> core
-- load (load_postgres.py), which is what makes NOT NULL and a real PRIMARY
-- KEY correct and safe here, in contrast to the nullable integer columns
-- used for the same fields in raw.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.monthly_reference (
    npd_well_bore_code           INTEGER NOT NULL,
    reference_year                INTEGER NOT NULL,
    reference_month                INTEGER NOT NULL,
    on_stream_hrs                 NUMERIC,
    oil_vol                       NUMERIC,
    gas_vol                       NUMERIC,
    water_vol                     NUMERIC,
    gas_injection_vol             NUMERIC,
    water_injection_vol           NUMERIC,

    CONSTRAINT pk_monthly_reference
        PRIMARY KEY (npd_well_bore_code, reference_year, reference_month),

    CONSTRAINT fk_monthly_reference_wellbore
        FOREIGN KEY (npd_well_bore_code)
        REFERENCES core.wellbore (npd_well_bore_code),

    -- General calendar-domain constraint (any month must be 1-12) - not a
    -- rule specific to this dataset, unlike the CHECK constraints on
    -- core.daily_production, which are all evidenced against the data itself.
    CONSTRAINT chk_monthly_reference_month_range
        CHECK (reference_month BETWEEN 1 AND 12)

    -- No non-negativity constraints on the volume columns: Section 21 only
    -- validated that these values match SUM(core.daily_production...); it
    -- did not independently re-run basic-validity checks (negative/zero/
    -- range) against the monthly source's own values. Adding a constraint
    -- here would apply a daily-layer finding to a table it wasn't tested on.
);

COMMENT ON TABLE core.monthly_reference IS
    'Equinor-supplied monthly production, retained for reconciliation against core.daily_production (Section 21: 100% match, 0 genuine mismatches, 526 wellbore-months). Not the primary analytical fact source (Section 24.1). The stray non-data row found in raw.monthly_production_source (Section 5) is excluded here by the load process, not represented by a nullable row.';

COMMENT ON COLUMN core.monthly_reference.gas_injection_vol IS
    'Corresponds to source column GI. Essentially unused in this dataset - almost entirely NULL among real rows (Section 5 profiling).';


-- Verification: confirm all 5 tables now exist with the expected column counts.
SELECT table_schema, table_name, count(*) AS column_count
FROM information_schema.columns
WHERE table_schema IN ('raw', 'core')
GROUP BY table_schema, table_name
ORDER BY table_schema, table_name;
