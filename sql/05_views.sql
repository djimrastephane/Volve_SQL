-- =============================================================================
-- 05_views.sql
--
-- Purpose shifts here: 01-04 established that the source can be trusted.
-- This file makes the trusted data easy to query.
--
--     raw
--      |  source preservation
--      v
--     core
--      |  relational truth
--      v
--     analytics views
--      |-- vw_daily_well_performance     (daily fact + wellbore, joined once)
--      |-- vw_monthly_well_performance   (daily -> SQL aggregation, analytical)
--      |-- vw_well_lifetime_summary      (one row per wellbore)
--      |-- vw_field_monthly_summary      (one row per calendar month, field-wide)
--      +-- vw_data_quality_review        (identifiable DQ-flagged records)
--              |
--              v
--     06_analysis.sql
--
-- Deliberately NOT a KPI factory. No water cut, GOR, decline rates, rolling
-- averages, cumulative-production percentages, rankings, or anomaly scores
-- here - those belong in 06_analysis.sql first. A calculation only gets
-- promoted into a view once an actual analysis in 06 needs it more than
-- once. Same discipline applies to indexes: none are added here either.
--
-- vw_monthly_well_performance is deliberately NOT a wrapper around
-- core.monthly_reference. It is calculated independently, from
-- core.daily_production, so it stays comparable against
-- core.monthly_reference rather than becoming indistinguishable from it:
--
--     core.daily_production -> SQL aggregation -> vw_monthly_well_performance
--                                                          |
--                                                     compared against
--                                                          |
--                                                          v
--                                                core.monthly_reference
--
-- Idempotent: CREATE OR REPLACE VIEW throughout.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- analytics.vw_daily_well_performance
--
-- The main analyst-facing daily dataset: core.daily_production joined to
-- core.wellbore once, so later queries do not repeatedly write the same
-- join. Stays close to the fact table - every daily_production column is
-- present, plus wellbore_name, year/month, and is_active.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.vw_daily_well_performance AS
SELECT
    dp.production_date,
    EXTRACT(YEAR FROM dp.production_date)::int  AS year,
    EXTRACT(MONTH FROM dp.production_date)::int AS month,
    dp.npd_well_bore_code,
    w.npd_well_bore_name                        AS wellbore_name,
    dp.well_type,
    dp.flow_kind,
    dp.on_stream_hrs,
    dp.bore_oil_vol,
    dp.bore_gas_vol,
    dp.bore_wat_vol,
    dp.bore_wi_vol,
    dp.avg_downhole_pressure,
    dp.avg_downhole_temperature,
    dp.avg_dp_tubing,
    dp.avg_annulus_press,
    dp.avg_choke_size_p,
    dp.avg_choke_uom,
    dp.avg_whp_p,
    dp.avg_wht_p,
    dp.dp_choke_size,
    (dp.on_stream_hrs > 0)                      AS is_active
FROM core.daily_production dp
JOIN core.wellbore w ON dp.npd_well_bore_code = w.npd_well_bore_code;

COMMENT ON VIEW analytics.vw_daily_well_performance IS
    'Daily fact table joined to the wellbore dimension. One row per (npd_well_bore_code, production_date), matching core.daily_production''s grain exactly. is_active is NULL when on_stream_hrs is NULL (DQ-003) - not coerced to false.';


-- -----------------------------------------------------------------------------
-- analytics.vw_monthly_well_performance
--
-- Calculated from core.daily_production, not from core.monthly_reference -
-- see header. producing_days counts days with any positive production
-- volume; calendar_records is the raw count of daily rows contributing to
-- the month (useful for spotting partial-month coverage, e.g. a wellbore's
-- first or last month).
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.vw_monthly_well_performance AS
SELECT
    dp.npd_well_bore_code,
    w.npd_well_bore_name AS wellbore_name,
    EXTRACT(YEAR FROM dp.production_date)::int  AS year,
    EXTRACT(MONTH FROM dp.production_date)::int AS month,
    SUM(dp.on_stream_hrs)  AS on_stream_hours,
    SUM(dp.bore_oil_vol)   AS oil_volume,
    SUM(dp.bore_gas_vol)   AS gas_volume,
    SUM(dp.bore_wat_vol)   AS water_volume,
    SUM(dp.bore_wi_vol)    AS water_injection_volume,
    COUNT(*) FILTER (
        WHERE dp.bore_oil_vol > 0 OR dp.bore_gas_vol > 0 OR dp.bore_wat_vol > 0
    ) AS producing_days,
    COUNT(*) AS calendar_records
FROM core.daily_production dp
JOIN core.wellbore w ON dp.npd_well_bore_code = w.npd_well_bore_code
GROUP BY dp.npd_well_bore_code, w.npd_well_bore_name, year, month;

COMMENT ON VIEW analytics.vw_monthly_well_performance IS
    'Analytical monthly dataset, aggregated in SQL from core.daily_production - not a copy of core.monthly_reference. Compare the two directly (same grain: npd_well_bore_code + year + month) rather than treating this view as the reconciliation source of truth; core.monthly_reference remains the independent Equinor reference (see sql/04_quality_checks.sql, QC-014/015/016).';


-- -----------------------------------------------------------------------------
-- analytics.vw_well_lifetime_summary
--
-- One row per wellbore. first_record_date / last_record_date, not
-- first_production_date: these mark the recorded span, not necessarily
-- when positive production began - a wellbore's first record can be a
-- DQ-001/DQ-003 blank-state row.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.vw_well_lifetime_summary AS
SELECT
    dp.npd_well_bore_code,
    w.npd_well_bore_name AS wellbore_name,
    MIN(dp.production_date) AS first_record_date,
    MAX(dp.production_date) AS last_record_date,
    COUNT(*)                AS recorded_days,
    SUM(dp.on_stream_hrs)   AS total_on_stream_hours,
    SUM(dp.bore_oil_vol)    AS total_oil,
    SUM(dp.bore_gas_vol)    AS total_gas,
    SUM(dp.bore_wat_vol)    AS total_water,
    SUM(dp.bore_wi_vol)     AS total_water_injection,
    MAX(dp.bore_oil_vol)    AS peak_daily_oil,
    MAX(dp.bore_gas_vol)    AS peak_daily_gas,
    MAX(dp.bore_wat_vol)    AS peak_daily_water,
    COUNT(*) FILTER (
        WHERE dp.bore_oil_vol > 0 OR dp.bore_gas_vol > 0 OR dp.bore_wat_vol > 0
    ) AS number_of_production_days,
    COUNT(*) FILTER (WHERE dp.bore_wi_vol > 0) AS number_of_injection_days
FROM core.daily_production dp
JOIN core.wellbore w ON dp.npd_well_bore_code = w.npd_well_bore_code
GROUP BY dp.npd_well_bore_code, w.npd_well_bore_name;

COMMENT ON VIEW analytics.vw_well_lifetime_summary IS
    'One row per wellbore, full recorded history. first_record_date/last_record_date mark the recorded span (a wellbore can change WELL_TYPE/FLOW_KIND within it - Section 12) - not a claim about when production or injection specifically began.';


-- -----------------------------------------------------------------------------
-- analytics.vw_field_monthly_summary
--
-- One row per calendar month, all wells combined. active_wells counts
-- wellbores with at least one on-stream day that month - not merely
-- wellbores with a recorded row that month (DQ-001/DQ-003 rows exist with
-- no activity at all).
-- -----------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.vw_field_monthly_summary AS
SELECT
    EXTRACT(YEAR FROM dp.production_date)::int  AS year,
    EXTRACT(MONTH FROM dp.production_date)::int AS month,
    MAKE_DATE(
        EXTRACT(YEAR FROM dp.production_date)::int,
        EXTRACT(MONTH FROM dp.production_date)::int,
        1
    ) AS month_start,
    COUNT(DISTINCT dp.npd_well_bore_code) FILTER (WHERE dp.on_stream_hrs > 0) AS active_wells,
    SUM(dp.bore_oil_vol) AS oil_volume,
    SUM(dp.bore_gas_vol) AS gas_volume,
    SUM(dp.bore_wat_vol) AS water_volume,
    SUM(dp.bore_wi_vol)  AS water_injection_volume,
    SUM(dp.on_stream_hrs) AS on_stream_hours
FROM core.daily_production dp
GROUP BY year, month, month_start;

COMMENT ON VIEW analytics.vw_field_monthly_summary IS
    'One row per calendar month, aggregated across all 7 wellbores - the Volve field evolution view. active_wells requires on_stream_hrs > 0, not merely a recorded row that month.';


-- -----------------------------------------------------------------------------
-- analytics.vw_data_quality_review
--
-- Different purpose from the rest of this file: not a query convenience,
-- a caution list. Does not reproduce sql/04_quality_checks.sql - exposes
-- the identifiable records themselves, for an analyst who queries
-- core.daily_production (or the views above) directly and should know
-- which specific rows carry a documented exception.
--
-- Covers DQ-001, DQ-003, DQ-004, DQ-005, DQ-006 - each corresponds to
-- identifiable rows. DQ-002 (the shared 12-day gap) is deliberately not
-- represented here: a missing reporting interval has no row to attach a
-- flag to.
--
-- A row can carry more than one flag (DQ-001 and DQ-003 overlap on 244 of
-- 285 rows - a pre-field-life record is usually also a blank-measurement
-- record, but they document different concerns and are not collapsed into
-- one). This view does not enforce one-row-per-production-record
-- uniqueness - an analyst querying a specific (npd_well_bore_code,
-- production_date) here may legitimately see more than one row back.
-- =============================================================================

CREATE OR REPLACE VIEW analytics.vw_data_quality_review AS
SELECT
    npd_well_bore_code,
    production_date,
    'DQ-001' AS dq_issue,
    'Pre-field-life record (production_date before 2008-01-01) - notebooks/02_data_quality.ipynb Section 9' AS review_reason
FROM core.daily_production
WHERE production_date < DATE '2008-01-01'

UNION ALL

SELECT
    npd_well_bore_code,
    production_date,
    'DQ-003',
    'NULL on-stream hours / blank measurement record - Section 10.2, Section 14.7'
FROM core.daily_production
WHERE on_stream_hrs IS NULL

UNION ALL

SELECT
    npd_well_bore_code,
    production_date,
    'DQ-004',
    'ON_STREAM_HRS > 24 - possible daylight-saving effect, not confirmed - Section 10.7'
FROM core.daily_production
WHERE on_stream_hrs > 24

UNION ALL

SELECT
    npd_well_bore_code,
    production_date,
    'DQ-005',
    'Negative produced water, or unexplained positive production recorded with zero on-stream hours - Section 13'
FROM core.daily_production
WHERE bore_wat_vol < 0
   OR (npd_well_bore_code = 7078 AND production_date = DATE '2015-01-17')

UNION ALL

SELECT
    npd_well_bore_code,
    production_date,
    'DQ-006',
    'Substantial water injection volume recorded with zero on-stream hours and no pressure/temperature/choke readings - Section 14'
FROM core.daily_production
WHERE npd_well_bore_code IN (5693, 5769)
  AND on_stream_hrs = 0
  AND bore_wi_vol > 100

ORDER BY production_date, npd_well_bore_code;

COMMENT ON VIEW analytics.vw_data_quality_review IS
    'Row-level caution list for identifiable DQ-flagged records (DQ-001, DQ-003, DQ-004, DQ-005, DQ-006). A row may carry multiple flags (DQ-001/DQ-003 overlap on 244/285 rows) - not deduplicated. DQ-002 is not represented: a missing reporting interval has no row to attach a flag to.';
