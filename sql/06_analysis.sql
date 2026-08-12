-- =============================================================================
-- 06_analysis.sql
--
-- Engineering questions about the Volve field, answered directly in SQL.
-- This is the payoff of everything before it: 01-04 established that the
-- data can be trusted, 05 made it easy to query - this file finally asks
-- what the data says.
--
-- Auditability rule: if a query excludes rows for a documented reason
-- (most often DQ-003's blank measurement state), that exclusion is stated
-- in a comment next to the filter, not left implicit. A reader should
-- never have to guess why a row count looks smaller than expected.
--
-- Read-only. No INSERT/UPDATE/DELETE anywhere in this file. Nothing here
-- is promoted into a view - a calculation only belongs in
-- sql/05_views.sql once it is reused, not the first time it is written.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- A1. Which wellbores produced the most cumulative oil?
--
-- NULLS LAST is not cosmetic: PostgreSQL sorts NULL first in a DESC
-- ORDER BY by default, which would rank 15/9-F-4 (a pure injector - total_oil
-- is NULL, not 0, because it never produces) as the #1 oil producer. This
-- surfaced during the reproducibility review of this file, not a design
-- decision made in advance - worth keeping explicit for that reason.
-- -----------------------------------------------------------------------------
SELECT
    wellbore_name,
    total_oil,
    RANK() OVER (ORDER BY total_oil DESC NULLS LAST) AS oil_rank
FROM analytics.vw_well_lifetime_summary
ORDER BY oil_rank;


-- -----------------------------------------------------------------------------
-- A2. Which wellbores produced the most cumulative gas and water?
--
-- Same NULLS LAST reasoning as A1 - every wellbore here has a non-NULL
-- total_gas/total_water so it makes no visible difference today, but
-- leaving it implicit would silently reintroduce A1's bug the moment a
-- wellbore with no gas or water history is added.
--
-- gas_rank uses RANK() (tied values would share a rank and leave a gap,
-- e.g. two wells tied for 2nd would both show 2, next shows 4). water_rank
-- uses DENSE_RANK() instead (tied values share a rank with no gap - next
-- shows 3), shown side by side deliberately since no tie exists in this
-- data to force the difference visibly - the distinction is in the
-- definition, not today's output.
-- -----------------------------------------------------------------------------
SELECT
    wellbore_name,
    total_gas,
    RANK() OVER (ORDER BY total_gas DESC NULLS LAST)         AS gas_rank,
    total_water,
    DENSE_RANK() OVER (ORDER BY total_water DESC NULLS LAST) AS water_rank
FROM analytics.vw_well_lifetime_summary
ORDER BY gas_rank;


-- -----------------------------------------------------------------------------
-- A3. When did each wellbore first record positive oil production?
--
-- Deliberately not the same question as "when was this wellbore's first
-- record" (see vw_well_lifetime_summary.first_record_date) - a wellbore's
-- earliest row can be a DQ-001/DQ-003 blank-state record with no
-- production at all. A LEFT JOIN keeps every wellbore in the result,
-- including 15/9-F-4, which never produces oil (pure injector) and
-- correctly returns NULL rather than being dropped.
-- -----------------------------------------------------------------------------
SELECT
    w.npd_well_bore_name AS wellbore_name,
    MIN(dp.production_date) AS first_positive_oil_date
FROM core.wellbore w
LEFT JOIN core.daily_production dp
    ON dp.npd_well_bore_code = w.npd_well_bore_code
    AND dp.bore_oil_vol > 0
GROUP BY w.npd_well_bore_name
ORDER BY first_positive_oil_date NULLS LAST;


-- -----------------------------------------------------------------------------
-- A4. When did each wellbore reach peak daily oil production?
--
-- Excludes rows where BORE_OIL_VOL IS NULL - not a data-quality exclusion,
-- just the contextual nullability confirmed in Section 11/12 (oil volume
-- is not applicable on injection-mode rows). A wellbore with no non-NULL
-- oil row at all (a pure injector) simply has no partition here and is
-- absent from the result, which is correct, not a bug.
-- -----------------------------------------------------------------------------
WITH ranked_oil AS (
    SELECT
        npd_well_bore_code,
        production_date,
        bore_oil_vol,
        ROW_NUMBER() OVER (
            PARTITION BY npd_well_bore_code ORDER BY bore_oil_vol DESC
        ) AS rn
    FROM core.daily_production
    WHERE bore_oil_vol IS NOT NULL
)
SELECT
    w.npd_well_bore_name AS wellbore_name,
    r.production_date    AS peak_oil_date,
    r.bore_oil_vol        AS peak_oil_volume
FROM ranked_oil r
JOIN core.wellbore w ON w.npd_well_bore_code = r.npd_well_bore_code
WHERE r.rn = 1
ORDER BY peak_oil_volume DESC;


-- -----------------------------------------------------------------------------
-- A5. How did production compare with peak at 30, 90, and 365 days after peak?
--
-- Deliberately not called "decline rate": this is a point-in-time
-- comparison against each well's own peak day, not decline-curve analysis.
-- A single shutdown day landing exactly on a checkpoint reads as a 100%
-- change even though it may reflect one day of downtime, not reservoir
-- deterioration - pct_decline_* columns keep their name for brevity, but
-- "% below peak at this checkpoint" is the accurate reading, not "decline
-- rate since peak."
--
-- Compares peak daily oil volume (from A4's method) against production
-- exactly 30/90/365 days later. A direct date match, not a "nearest
-- available record within N days" search - simpler and fully auditable,
-- at the cost of returning NULL where that exact calendar date has no
-- recorded row (e.g. inside a reporting gap).
-- Excludes rows where BORE_OIL_VOL IS NULL for the same reason as A4.
-- -----------------------------------------------------------------------------
WITH ranked_oil AS (
    SELECT
        npd_well_bore_code,
        production_date,
        bore_oil_vol,
        ROW_NUMBER() OVER (
            PARTITION BY npd_well_bore_code ORDER BY bore_oil_vol DESC
        ) AS rn
    FROM core.daily_production
    WHERE bore_oil_vol IS NOT NULL
),
peak_only AS (
    SELECT npd_well_bore_code, production_date AS peak_date, bore_oil_vol AS peak_volume
    FROM ranked_oil
    WHERE rn = 1
)
SELECT
    w.npd_well_bore_name AS wellbore_name,
    p.peak_date,
    p.peak_volume,
    d30.bore_oil_vol  AS oil_30_days_after_peak,
    ROUND(100.0 * (p.peak_volume - d30.bore_oil_vol) / p.peak_volume, 1)  AS pct_decline_30_days,
    d90.bore_oil_vol  AS oil_90_days_after_peak,
    ROUND(100.0 * (p.peak_volume - d90.bore_oil_vol) / p.peak_volume, 1)  AS pct_decline_90_days,
    d365.bore_oil_vol AS oil_365_days_after_peak,
    ROUND(100.0 * (p.peak_volume - d365.bore_oil_vol) / p.peak_volume, 1) AS pct_decline_365_days
FROM peak_only p
JOIN core.wellbore w ON w.npd_well_bore_code = p.npd_well_bore_code
LEFT JOIN core.daily_production d30
    ON d30.npd_well_bore_code = p.npd_well_bore_code AND d30.production_date = p.peak_date + 30
LEFT JOIN core.daily_production d90
    ON d90.npd_well_bore_code = p.npd_well_bore_code AND d90.production_date = p.peak_date + 90
LEFT JOIN core.daily_production d365
    ON d365.npd_well_bore_code = p.npd_well_bore_code AND d365.production_date = p.peak_date + 365
ORDER BY p.peak_volume DESC;


-- -----------------------------------------------------------------------------
-- A6. How did water production evolve relative to oil production?
--
-- Field-wide water-to-oil ratio by month. Excludes months with no
-- recorded field oil volume (WHERE oil_volume IS NOT NULL) - these are
-- months with no producing wellbore active at all, not a data-quality
-- exclusion. NULLIF guards the same condition inside the ratio itself, in
-- case a month has recorded oil = 0 rather than NULL.
-- -----------------------------------------------------------------------------
SELECT
    month_start,
    oil_volume,
    water_volume,
    ROUND(water_volume / NULLIF(oil_volume, 0), 3) AS water_oil_ratio
FROM analytics.vw_field_monthly_summary
WHERE oil_volume IS NOT NULL
ORDER BY month_start;


-- -----------------------------------------------------------------------------
-- A7. How did water injection evolve through field life?
-- -----------------------------------------------------------------------------
SELECT
    month_start,
    water_injection_volume,
    SUM(water_injection_volume) OVER (ORDER BY month_start) AS cumulative_water_injection
FROM analytics.vw_field_monthly_summary
ORDER BY month_start;


-- -----------------------------------------------------------------------------
-- A8. Which months had the highest field oil production?
--
-- Excludes months with no recorded field oil volume, same as A6.
-- -----------------------------------------------------------------------------
SELECT
    month_start,
    oil_volume,
    RANK() OVER (ORDER BY oil_volume DESC) AS oil_rank
FROM analytics.vw_field_monthly_summary
WHERE oil_volume IS NOT NULL
ORDER BY oil_rank
LIMIT 10;


-- -----------------------------------------------------------------------------
-- A9. Which wells contributed most to field production by year?
--
-- Excludes rows where BORE_OIL_VOL IS NULL for the same reason as A4
-- (not applicable on injection-mode rows).
-- -----------------------------------------------------------------------------
WITH yearly_by_well AS (
    SELECT
        EXTRACT(YEAR FROM production_date)::int AS year,
        npd_well_bore_code,
        SUM(bore_oil_vol) AS oil_volume
    FROM core.daily_production
    WHERE bore_oil_vol IS NOT NULL
    GROUP BY year, npd_well_bore_code
),
yearly_totals AS (
    SELECT year, SUM(oil_volume) AS year_total_oil
    FROM yearly_by_well
    GROUP BY year
)
SELECT
    y.year,
    w.npd_well_bore_name AS wellbore_name,
    y.oil_volume,
    t.year_total_oil,
    ROUND(100.0 * y.oil_volume / NULLIF(t.year_total_oil, 0), 1) AS pct_of_year_total
FROM yearly_by_well y
JOIN yearly_totals t ON y.year = t.year
JOIN core.wellbore w ON w.npd_well_bore_code = y.npd_well_bore_code
ORDER BY y.year, pct_of_year_total DESC;


-- -----------------------------------------------------------------------------
-- A10. How many wells were active each month?
--
-- Already computed in analytics.vw_field_monthly_summary (active_wells) -
-- shown directly here as the payoff of having built that view, rather than
-- recomputing the same aggregation.
-- -----------------------------------------------------------------------------
SELECT month_start, active_wells
FROM analytics.vw_field_monthly_summary
ORDER BY month_start;


-- -----------------------------------------------------------------------------
-- A11. Which wells experienced the largest shutdown/restart patterns?
--
-- A "transition" is a change in active state (ON_STREAM_HRS > 0) between
-- one recorded day and the next for the same wellbore, using LAG() to look
-- at the previous recorded row. A gap in reporting (missing calendar days)
-- is not a transition here - only consecutive recorded rows are compared,
-- so this measures how often a well's state flips between two records it
-- actually has, not real calendar-time shutdown duration.
--
-- Excludes rows with NULL ON_STREAM_HRS because they represent blank
-- measurement states documented under DQ-003 - there is no defined
-- active/inactive state to compare for those rows.
-- -----------------------------------------------------------------------------
WITH daily_state AS (
    SELECT
        npd_well_bore_code,
        production_date,
        (on_stream_hrs > 0) AS is_active,
        LAG(on_stream_hrs > 0) OVER (
            PARTITION BY npd_well_bore_code ORDER BY production_date
        ) AS previous_is_active
    FROM core.daily_production
    WHERE on_stream_hrs IS NOT NULL
),
transitions AS (
    SELECT
        npd_well_bore_code,
        CASE
            WHEN previous_is_active AND NOT is_active THEN 'shutdown'
            WHEN NOT previous_is_active AND is_active THEN 'restart'
        END AS transition_type
    FROM daily_state
    WHERE previous_is_active IS NOT NULL
      AND is_active IS DISTINCT FROM previous_is_active
)
SELECT
    w.npd_well_bore_name AS wellbore_name,
    count(*) FILTER (WHERE transition_type = 'shutdown') AS shutdowns,
    count(*) FILTER (WHERE transition_type = 'restart')  AS restarts,
    count(*) AS total_transitions
FROM transitions t
JOIN core.wellbore w ON w.npd_well_bore_code = t.npd_well_bore_code
GROUP BY w.npd_well_bore_name
ORDER BY total_transitions DESC;


-- -----------------------------------------------------------------------------
-- A11 extended. Reconstructing downtime episodes and their production impact
--
-- A11 counts transitions (state flips between consecutive records) but
-- doesn't say how long a well stayed down or what happened to production
-- around it. This groups consecutive same-state days into episodes using
-- the "gaps and islands" pattern: LAG() flags each day where the state
-- differs from the previous recorded day, a running SUM() of those flags
-- assigns every day a group id, and GROUP BY that id collapses each run of
-- same-state days into a single episode row.
--
-- Only inactive episodes with an *observed* preceding active day count as
-- a shutdown - a well's very first recorded day can already be inactive
-- with no prior state to compare against (a data-window edge, not an
-- observed shutdown), and excluding it is what makes the episode count
-- match A11's transition count exactly (verified against all 7 wells).
-- Restart date is the following episode's start; a well still inactive at
-- the end of its recorded history has a shutdown_date but no restart_date
-- (censored - still down when the data ends, not a zero-length outage).
--
-- LEAD() must be computed over ALL episodes, before filtering to inactive
-- ones - computing it after the WHERE filter (an earlier version of this
-- query did) makes LEAD() skip the intervening active episode entirely and
-- pair each shutdown with the START OF THE NEXT SHUTDOWN instead of its
-- own restart, silently inflating offline_days for any well with active
-- runs longer than one episode between shutdowns.
--
-- Oil before/after use the same exact-calendar-date checkpoint methodology
-- as A5: the day immediately before shutdown_date, and the restart_date
-- itself, no interpolation. Recovery % inherits A5's statistical trap - a
-- near-zero oil_before baseline turns a small absolute change into a
-- triple-digit swing (e.g. 0.55 Sm³/d before vs. 50.3 Sm³/d after reads as
-- +9145%). Always read oil_before/oil_after alongside the percentage, not
-- instead of it. Injection wells have no oil measurement at all
-- (bore_oil_vol is NULL, not zero) - these rows resolve as blank, correctly
-- reflecting that "oil recovery" doesn't apply to a water injector.
-- -----------------------------------------------------------------------------
WITH daily_state AS (
    SELECT
        npd_well_bore_code,
        production_date,
        (on_stream_hrs > 0) AS is_active,
        bore_oil_vol
    FROM core.daily_production
    WHERE on_stream_hrs IS NOT NULL
),
flagged AS (
    SELECT
        *,
        LAG(is_active) OVER (
            PARTITION BY npd_well_bore_code ORDER BY production_date
        ) AS previous_is_active
    FROM daily_state
),
episode_marks AS (
    SELECT
        *,
        CASE
            WHEN previous_is_active IS NULL THEN 1
            WHEN is_active IS DISTINCT FROM previous_is_active THEN 1
            ELSE 0
        END AS starts_new_episode
    FROM flagged
),
episode_ids AS (
    SELECT
        *,
        SUM(starts_new_episode) OVER (
            PARTITION BY npd_well_bore_code ORDER BY production_date
        ) AS episode_id
    FROM episode_marks
),
episodes AS (
    SELECT
        npd_well_bore_code,
        episode_id,
        is_active,
        bool_or(previous_is_active IS NULL) AS is_first_episode,
        MIN(production_date) AS episode_start,
        MAX(production_date) AS episode_end
    FROM episode_ids
    GROUP BY npd_well_bore_code, episode_id, is_active
),
episodes_seq AS (
    SELECT
        *,
        LEAD(episode_start) OVER (
            PARTITION BY npd_well_bore_code ORDER BY episode_id
        ) AS next_episode_start
    FROM episodes
),
shutdowns AS (
    SELECT
        npd_well_bore_code,
        episode_start AS shutdown_date,
        next_episode_start AS restart_date
    FROM episodes_seq
    WHERE is_active = false
      AND NOT is_first_episode
)
SELECT
    w.npd_well_bore_name AS wellbore_name,
    s.shutdown_date,
    s.restart_date,
    (s.restart_date - s.shutdown_date) AS offline_days,
    b.bore_oil_vol AS oil_before,
    a.bore_oil_vol AS oil_after,
    ROUND(100.0 * a.bore_oil_vol / NULLIF(b.bore_oil_vol, 0), 1) AS recovery_pct
FROM shutdowns s
JOIN core.wellbore w ON w.npd_well_bore_code = s.npd_well_bore_code
LEFT JOIN core.daily_production b
    ON b.npd_well_bore_code = s.npd_well_bore_code
   AND b.production_date = s.shutdown_date - 1
LEFT JOIN core.daily_production a
    ON a.npd_well_bore_code = s.npd_well_bore_code
   AND a.production_date = s.restart_date
ORDER BY w.npd_well_bore_name, s.shutdown_date;


-- -----------------------------------------------------------------------------
-- A12. How did field production change before and after new wells entered
--      service?
--
-- "Entered service" = a wellbore's first day of positive oil production or
-- positive water injection - not its first recorded row (see A3). Rolling
-- 3-month field-oil averages are computed once, over every month, using a
-- window frame (ROWS BETWEEN), then looked up for each wellbore's entry
-- month - not recomputed per wellbore via a correlated subquery.
-- -----------------------------------------------------------------------------
WITH field_monthly AS (
    SELECT
        month_start,
        oil_volume,
        AVG(oil_volume) OVER (
            ORDER BY month_start
            ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING
        ) AS trailing_3mo_avg_oil,
        AVG(oil_volume) OVER (
            ORDER BY month_start
            ROWS BETWEEN 1 FOLLOWING AND 3 FOLLOWING
        ) AS following_3mo_avg_oil
    FROM analytics.vw_field_monthly_summary
),
wellbore_entry AS (
    SELECT
        npd_well_bore_code,
        make_date(
            EXTRACT(YEAR FROM MIN(production_date))::int,
            EXTRACT(MONTH FROM MIN(production_date))::int,
            1
        ) AS entry_month_start
    FROM core.daily_production
    WHERE bore_oil_vol > 0 OR bore_wi_vol > 0
    GROUP BY npd_well_bore_code
)
SELECT
    w.npd_well_bore_name AS wellbore_name,
    e.entry_month_start,
    fm.trailing_3mo_avg_oil  AS avg_field_oil_3mo_before,
    fm.oil_volume             AS field_oil_entry_month,
    fm.following_3mo_avg_oil AS avg_field_oil_3mo_after
FROM wellbore_entry e
JOIN core.wellbore w ON w.npd_well_bore_code = e.npd_well_bore_code
JOIN field_monthly fm ON fm.month_start = e.entry_month_start
ORDER BY e.entry_month_start;
