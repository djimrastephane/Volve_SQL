-- =============================================================================
-- 04_quality_checks.sql
--
-- Re-expresses the notebook's important findings as SQL checks against the
-- loaded database - a second, independent layer of validation:
--
--     Python / notebook  ->  source understanding
--     PostgreSQL         ->  independent SQL verification
--
-- This script fixes nothing. It only answers two questions:
--   1. Does the database still satisfy the structural rules confirmed in
--      notebooks/02_data_quality.ipynb (grain, keys, FK coverage,
--      identifier consistency, reconciliation)?
--   2. Does the database still contain the known DQ-001..DQ-006 exception
--      populations documented from the source - unchanged, not silently
--      dropped or altered by a later change to the loader?
--
-- Three statuses, matching the notebook's PASS/FAIL/REVIEW convention:
--   PASS    deterministic expectation satisfied
--   FAIL    database/source integrity rule violated, OR a documented DQ
--           population no longer matches its recorded size (drift - the
--           loader or source changed something worth investigating)
--   REVIEW  a known, possibly-legitimate industrial exception, confirmed
--           still present exactly as documented
--
-- Not every notebook check is ported - only what matters for database
-- integrity, repeatability, or a known DQ exception. Sections 15-20 of the
-- notebook (pressure/temperature/choke relationship reviews, outlier
-- summaries) are not re-tested here; they produced no FAIL-worthy or
-- DQ-tracked findings of their own.
--
-- Read-only. No INSERT/UPDATE/DELETE anywhere in this file.
-- =============================================================================

DROP TABLE IF EXISTS pg_temp.quality_check_results;

CREATE TEMP TABLE quality_check_results AS

-- -----------------------------------------------------------------------------
-- 1. Row-count checks
-- -----------------------------------------------------------------------------
WITH qc_001 AS (
    SELECT 1 AS sort_order, 'QC-001' AS check_id, 'Row counts' AS category,
        'Raw daily row count' AS check_name,
        '15634' AS expected, count(*)::text AS actual,
        CASE WHEN count(*) = 15634 THEN 'PASS' ELSE 'FAIL' END AS status
    FROM raw.daily_production_source
),
qc_002 AS (
    SELECT 2, 'QC-002', 'Row counts',
        'Core daily row count',
        '15634', count(*)::text,
        CASE WHEN count(*) = 15634 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
),
qc_003 AS (
    SELECT 3, 'QC-003', 'Row counts',
        'Core wellbore count',
        '7', count(*)::text,
        CASE WHEN count(*) = 7 THEN 'PASS' ELSE 'FAIL' END
    FROM core.wellbore
),
qc_004 AS (
    SELECT 4, 'QC-004', 'Row counts',
        'Raw monthly row count',
        '527', count(*)::text,
        CASE WHEN count(*) = 527 THEN 'PASS' ELSE 'FAIL' END
    FROM raw.monthly_production_source
),
qc_005 AS (
    SELECT 5, 'QC-005', 'Row counts',
        'Core monthly reference row count',
        '526', count(*)::text,
        CASE WHEN count(*) = 526 THEN 'PASS' ELSE 'FAIL' END
    FROM core.monthly_reference
),

-- -----------------------------------------------------------------------------
-- 2. Grain uniqueness (daily and monthly)
-- -----------------------------------------------------------------------------
qc_006 AS (
    SELECT 6, 'QC-006', 'Grain uniqueness',
        'Daily duplicate (npd_well_bore_code, production_date) combinations',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT npd_well_bore_code, production_date
        FROM core.daily_production
        GROUP BY npd_well_bore_code, production_date
        HAVING count(*) > 1
    ) dup
),
qc_007 AS (
    SELECT 7, 'QC-007', 'Grain uniqueness',
        'Monthly duplicate (npd_well_bore_code, reference_year, reference_month) combinations',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT npd_well_bore_code, reference_year, reference_month
        FROM core.monthly_reference
        GROUP BY npd_well_bore_code, reference_year, reference_month
        HAVING count(*) > 1
    ) dup
),

-- -----------------------------------------------------------------------------
-- 3. Wellbore FK coverage
-- -----------------------------------------------------------------------------
qc_008 AS (
    SELECT 8, 'QC-008', 'Wellbore FK coverage',
        'Daily rows without a matching core.wellbore row',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production dp
    LEFT JOIN core.wellbore w ON dp.npd_well_bore_code = w.npd_well_bore_code
    WHERE w.npd_well_bore_code IS NULL
),
qc_009 AS (
    SELECT 9, 'QC-009', 'Wellbore FK coverage',
        'Monthly rows without a matching core.wellbore row',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.monthly_reference mr
    LEFT JOIN core.wellbore w ON mr.npd_well_bore_code = w.npd_well_bore_code
    WHERE w.npd_well_bore_code IS NULL
),

-- -----------------------------------------------------------------------------
-- 4. Identifier consistency
--
-- Tested against RAW, deliberately, not core. Core's PK/UNIQUE constraints
-- already make these true by construction (they could not fail without the
-- load itself having failed) - testing raw is the check that is actually
-- informative, since raw carries no such constraint.
-- -----------------------------------------------------------------------------
qc_010 AS (
    SELECT 10, 'QC-010', 'Identifier consistency',
        'npd_well_bore_code values mapping to >1 npd_well_bore_name (raw)',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT npd_well_bore_code
        FROM raw.daily_production_source
        WHERE npd_well_bore_code IS NOT NULL AND npd_well_bore_name IS NOT NULL
        GROUP BY npd_well_bore_code
        HAVING count(DISTINCT npd_well_bore_name) > 1
    ) violations
),
qc_011 AS (
    SELECT 11, 'QC-011', 'Identifier consistency',
        'npd_well_bore_name values mapping to >1 npd_well_bore_code (raw)',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT npd_well_bore_name
        FROM raw.daily_production_source
        WHERE npd_well_bore_code IS NOT NULL AND npd_well_bore_name IS NOT NULL
        GROUP BY npd_well_bore_name
        HAVING count(DISTINCT npd_well_bore_code) > 1
    ) violations
),
qc_012 AS (
    SELECT 12, 'QC-012', 'Identifier consistency',
        'Wellbore codes in daily raw but not monthly raw',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT DISTINCT npd_well_bore_code FROM raw.daily_production_source WHERE npd_well_bore_code IS NOT NULL
        EXCEPT
        SELECT DISTINCT npdcode FROM raw.monthly_production_source WHERE npdcode IS NOT NULL
    ) missing
),
qc_013 AS (
    SELECT 13, 'QC-013', 'Identifier consistency',
        'Wellbore codes in monthly raw but not daily raw',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM (
        SELECT DISTINCT npdcode FROM raw.monthly_production_source WHERE npdcode IS NOT NULL
        EXCEPT
        SELECT DISTINCT npd_well_bore_code FROM raw.daily_production_source WHERE npd_well_bore_code IS NOT NULL
    ) missing
),

-- -----------------------------------------------------------------------------
-- 5. Daily/monthly reconciliation
--
-- Re-expresses notebook Section 21, including its tolerance: the source
-- monthly WI (water injection) figures differ from SUM(daily bore_wi_vol)
-- by about 1e-10 on values in the hundred-thousands - confirmed genuine
-- source-level floating-point noise, not a loader defect (Equinor's own
-- monthly rollup was evidently computed independently of a plain sum of
-- the daily figures). PostgreSQL NUMERIC is exact, so this noise is
-- visible here in a way it was not through pandas' looser float
-- comparisons - which is exactly why the same 1e-6 tolerance from the
-- notebook is applied below rather than strict equality. Both sides are
-- COALESCE'd to 0 before comparing, which also absorbs the one legitimate
-- exception the notebook found: monthly leaves a metric NULL (not 0)
-- where it does not apply.
-- -----------------------------------------------------------------------------
daily_monthly_agg AS (
    SELECT
        npd_well_bore_code,
        extract(YEAR FROM production_date)::int AS reference_year,
        extract(MONTH FROM production_date)::int AS reference_month,
        sum(bore_oil_vol) AS oil_vol_sum,
        sum(bore_gas_vol) AS gas_vol_sum,
        sum(bore_wat_vol) AS water_vol_sum,
        sum(bore_wi_vol) AS water_injection_vol_sum
    FROM core.daily_production
    GROUP BY npd_well_bore_code, reference_year, reference_month
),
reconciliation_joined AS (
    SELECT
        d.npd_well_bore_code AS d_code, m.npd_well_bore_code AS m_code,
        d.oil_vol_sum, m.oil_vol,
        d.gas_vol_sum, m.gas_vol,
        d.water_vol_sum, m.water_vol,
        d.water_injection_vol_sum, m.water_injection_vol
    FROM daily_monthly_agg d
    FULL JOIN core.monthly_reference m
        ON d.npd_well_bore_code = m.npd_well_bore_code
        AND d.reference_year = m.reference_year
        AND d.reference_month = m.reference_month
),
qc_014 AS (
    SELECT 14, 'QC-014', 'Daily/monthly reconciliation',
        'Wellbore/year/month groups in daily aggregation but not monthly reference',
        '0', count(*) FILTER (WHERE m_code IS NULL)::text,
        CASE WHEN count(*) FILTER (WHERE m_code IS NULL) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM reconciliation_joined
),
qc_015 AS (
    SELECT 15, 'QC-015', 'Daily/monthly reconciliation',
        'Wellbore/year/month groups in monthly reference but not daily aggregation',
        '0', count(*) FILTER (WHERE d_code IS NULL)::text,
        CASE WHEN count(*) FILTER (WHERE d_code IS NULL) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM reconciliation_joined
),
qc_016 AS (
    SELECT 16, 'QC-016', 'Daily/monthly reconciliation',
        'Genuine value mismatches (oil/gas/water/water-injection sums, tolerance 0.000001 per notebook Section 21)',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM reconciliation_joined
    WHERE abs(coalesce(oil_vol_sum, 0) - coalesce(oil_vol, 0)) > 0.000001
       OR abs(coalesce(gas_vol_sum, 0) - coalesce(gas_vol, 0)) > 0.000001
       OR abs(coalesce(water_vol_sum, 0) - coalesce(water_vol, 0)) > 0.000001
       OR abs(coalesce(water_injection_vol_sum, 0) - coalesce(water_injection_vol, 0)) > 0.000001
),

-- -----------------------------------------------------------------------------
-- 6. Known DQ issue checks
--
-- Recreates each documented exception population. Status is REVIEW when
-- the live count matches the documented count exactly (the known
-- industrial exception is confirmed present, unchanged) and FAIL when it
-- does not (the population drifted - the loader or source changed
-- something that needs investigating, which is the entire point of this
-- section: catching that automatically rather than relying on someone
-- re-reading the notebook).
-- -----------------------------------------------------------------------------
dq_001 AS (
    SELECT 17, 'DQ-001', 'Known DQ issues',
        'Pre-field-life records (production_date < 2008-01-01)',
        '244', count(*)::text,
        CASE WHEN count(*) = 244 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE production_date < DATE '2008-01-01'
),
dq_002 AS (
    SELECT 18, 'DQ-002', 'Known DQ issues',
        'Rows in the documented shared gap window (wellbores 5351/5599, 2012-01-02 to 2012-01-14 exclusive)',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE npd_well_bore_code IN (5351, 5599)
      AND production_date > DATE '2012-01-02'
      AND production_date < DATE '2012-01-14'
),
dq_003 AS (
    SELECT 19, 'DQ-003', 'Known DQ issues',
        'NULL on_stream_hrs population',
        '285', count(*)::text,
        CASE WHEN count(*) = 285 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE on_stream_hrs IS NULL
),
dq_004 AS (
    SELECT 20, 'DQ-004', 'Known DQ issues',
        'Rows with on_stream_hrs > 24',
        '20', count(*)::text,
        CASE WHEN count(*) = 20 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE on_stream_hrs > 24
),
dq_005 AS (
    SELECT 21, 'DQ-005', 'Known DQ issues',
        'Negative produced water + unexplained zero-hours/positive-production row',
        '5', count(*)::text,
        CASE WHEN count(*) = 5 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE bore_wat_vol < 0
       OR (npd_well_bore_code = 7078 AND production_date = DATE '2015-01-17')
),
dq_006 AS (
    SELECT 22, 'DQ-006', 'Known DQ issues',
        'Large zero-hours/positive-injection inconsistencies (BORE_WI_VOL > 100)',
        '2', count(*)::text,
        CASE WHEN count(*) = 2 THEN 'REVIEW' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE npd_well_bore_code IN (5693, 5769)
      AND on_stream_hrs = 0
      AND bore_wi_vol > 100
),

-- -----------------------------------------------------------------------------
-- 7. Constraint-sensitive checks
--
-- These conditions are already enforced by CHECK constraints in
-- 02_create_tables.sql and can never fail while those constraints exist -
-- that is exactly why re-testing them independently here is useful: this
-- catches a constraint that was dropped or bypassed (e.g. via a direct
-- superuser write), which the constraint itself obviously cannot catch.
-- -----------------------------------------------------------------------------
qc_017 AS (
    SELECT 23, 'QC-017', 'Constraint-sensitive',
        'Negative oil/gas/water-injection volumes (bore_wat_vol excluded - DQ-005)',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE bore_oil_vol < 0 OR bore_gas_vol < 0 OR bore_wi_vol < 0
),
qc_018 AS (
    SELECT 24, 'QC-018', 'Constraint-sensitive',
        'Negative on_stream_hrs / pressure / temperature / choke values',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE on_stream_hrs < 0 OR avg_downhole_pressure < 0 OR avg_downhole_temperature < 0
       OR avg_dp_tubing < 0 OR avg_annulus_press < 0 OR avg_choke_size_p < 0
       OR avg_whp_p < 0 OR avg_wht_p < 0 OR dp_choke_size < 0
),
qc_019 AS (
    SELECT 25, 'QC-019', 'Constraint-sensitive',
        'well_type values outside {OP, WI}',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE well_type NOT IN ('OP', 'WI')
),
qc_020 AS (
    SELECT 26, 'QC-020', 'Constraint-sensitive',
        'flow_kind values outside {production, injection}',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE flow_kind NOT IN ('production', 'injection')
),
qc_021 AS (
    SELECT 27, 'QC-021', 'Constraint-sensitive',
        'avg_choke_uom values outside {NULL, ''%''}',
        '0', count(*)::text,
        CASE WHEN count(*) = 0 THEN 'PASS' ELSE 'FAIL' END
    FROM core.daily_production
    WHERE avg_choke_uom IS NOT NULL AND avg_choke_uom != '%'
)

SELECT * FROM qc_001
UNION ALL SELECT * FROM qc_002
UNION ALL SELECT * FROM qc_003
UNION ALL SELECT * FROM qc_004
UNION ALL SELECT * FROM qc_005
UNION ALL SELECT * FROM qc_006
UNION ALL SELECT * FROM qc_007
UNION ALL SELECT * FROM qc_008
UNION ALL SELECT * FROM qc_009
UNION ALL SELECT * FROM qc_010
UNION ALL SELECT * FROM qc_011
UNION ALL SELECT * FROM qc_012
UNION ALL SELECT * FROM qc_013
UNION ALL SELECT * FROM qc_014
UNION ALL SELECT * FROM qc_015
UNION ALL SELECT * FROM qc_016
UNION ALL SELECT * FROM dq_001
UNION ALL SELECT * FROM dq_002
UNION ALL SELECT * FROM dq_003
UNION ALL SELECT * FROM dq_004
UNION ALL SELECT * FROM dq_005
UNION ALL SELECT * FROM dq_006
UNION ALL SELECT * FROM qc_017
UNION ALL SELECT * FROM qc_018
UNION ALL SELECT * FROM qc_019
UNION ALL SELECT * FROM qc_020
UNION ALL SELECT * FROM qc_021;


-- =============================================================================
-- 8. Final quality summary
-- =============================================================================

-- Detailed results, in the order the sections above were defined.
SELECT check_id, category, check_name, expected, actual, status
FROM quality_check_results
ORDER BY sort_order;

-- One-row rollup: the single result set a dashboard or CI pipeline would
-- actually look at.
SELECT
    count(*) AS total_checks,
    count(*) FILTER (WHERE status = 'PASS') AS pass_count,
    count(*) FILTER (WHERE status = 'REVIEW') AS review_count,
    count(*) FILTER (WHERE status = 'FAIL') AS fail_count,
    CASE
        WHEN count(*) FILTER (WHERE status = 'FAIL') > 0 THEN 'FAIL'
        WHEN count(*) FILTER (WHERE status = 'REVIEW') > 0 THEN 'PASS WITH REVIEW'
        ELSE 'PASS'
    END AS overall_status
FROM quality_check_results;
