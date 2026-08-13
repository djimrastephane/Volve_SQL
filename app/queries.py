"""
queries.py

Every SQL statement the dashboard runs, in one place - all against
analytics.* views only. Each function names the view(s) it reads and, where
applicable, the sql/06_analysis.sql question (A1-A12) it mirrors. Several
A-questions were originally written against core.daily_production /
core.wellbore directly; here they are rebuilt against
analytics.vw_daily_well_performance (the same join, already made once) since
the dashboard's volve_app role cannot see core at all - proof that the
analytics views carry enough information on their own for engineering
analysis, not just for auditing.
"""

from __future__ import annotations

import pandas as pd

from db import run_query

VIEW_DAILY = "analytics.vw_daily_well_performance"
VIEW_MONTHLY = "analytics.vw_monthly_well_performance"
VIEW_LIFETIME = "analytics.vw_well_lifetime_summary"
VIEW_FIELD_MONTHLY = "analytics.vw_field_monthly_summary"
VIEW_DOWNTIME = "analytics.vw_downtime_episodes"
VIEW_DQ = "analytics.vw_data_quality_review"

DQ_EXPLANATIONS = {
    "DQ-001": "Pre-field-life record (production_date before 2008-01-01).",
    "DQ-002": "12-day shared reporting gap (wellbores 5351/5599, 2012-01-02 "
              "to 2012-01-14) - a missing interval, not a flagged row, so it "
              "has no record in this view.",
    "DQ-003": "NULL on-stream hours - a distinct state from on_stream_hrs = 0 "
              "(blank measurement record, not a zero reading).",
    "DQ-004": "on_stream_hrs > 24 - plausibly a daylight-saving artifact, not "
              "confirmed. No upper-bound constraint was added for this reason.",
    "DQ-005": "Negative produced water, or unexplained positive production "
              "recorded with zero on-stream hours.",
    "DQ-006": "Substantial water injection volume recorded with zero "
              "on-stream hours and no supporting pressure/temperature/choke "
              "readings.",
}


def list_wells() -> pd.DataFrame:
    """
    analytics.vw_well_lifetime_summary + vw_daily_well_performance.
    well_type is the *dominant* type across a well's recorded days, not a
    fixed attribute - 15/9-F-5 has 144 OP days among 3,162 WI days (an
    early transition period), so "dominant" is a majority vote, not a
    guarantee every row agrees.
    """
    df = run_query(f"""
        WITH type_counts AS (
            SELECT npd_well_bore_code, well_type, count(*) AS n
            FROM {VIEW_DAILY}
            GROUP BY npd_well_bore_code, well_type
        ),
        dominant_type AS (
            SELECT DISTINCT ON (npd_well_bore_code) npd_well_bore_code, well_type
            FROM type_counts
            ORDER BY npd_well_bore_code, n DESC
        )
        SELECT l.npd_well_bore_code, l.wellbore_name, dt.well_type
        FROM {VIEW_LIFETIME} l
        JOIN dominant_type dt ON dt.npd_well_bore_code = l.npd_well_bore_code
        ORDER BY l.wellbore_name
    """)
    df["well_type_label"] = df["well_type"].map({"OP": "Producer", "WI": "Injector"}).fillna(df["well_type"])
    return df


def field_lifetime_summary() -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance + vw_well_lifetime_summary -
    field-wide (all 7 wellbores) top-line KPIs: cumulative oil/gas/water/
    water-injection, the field's peak DAILY oil rate (summed across all
    wells reporting that day) and the date it happened, and the recorded
    field life span. Peak rate is a daily figure, not monthly - "peak field
    oil rate" reads as a rate, and daily is the finest grain this project
    otherwise reports rates at (matches peak_daily_oil on
    vw_well_lifetime_summary).
    """
    totals = run_query(f"""
        SELECT
            SUM(total_oil) AS total_oil, SUM(total_gas) AS total_gas,
            SUM(total_water) AS total_water, SUM(total_water_injection) AS total_water_injection,
            MIN(first_record_date) AS first_record_date, MAX(last_record_date) AS last_record_date
        FROM {VIEW_LIFETIME}
    """)
    peak = run_query(f"""
        SELECT production_date AS peak_date, SUM(bore_oil_vol) AS peak_oil_rate
        FROM {VIEW_DAILY}
        WHERE bore_oil_vol IS NOT NULL
        GROUP BY production_date
        ORDER BY peak_oil_rate DESC
        LIMIT 1
    """)
    row = pd.concat([totals.iloc[0], peak.iloc[0]])
    row["field_life_years"] = (row["last_record_date"] - row["first_record_date"]).days / 365.25
    return row


def field_availability_monthly() -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance - field-wide monthly availability:
    on-stream hours as a % of possible hours across (well, day) rows with a
    known state that month. Days with no on-stream-hours reading are
    excluded from the denominator, not counted as inactive - same
    methodology as well_availability(), generalized to the whole field so
    it can sit alongside the Active wells chart. A well not yet drilled (or
    already decommissioned) contributes no rows at all that month, so it
    doesn't drag this down the way a fixed "7 wells x 24h" denominator
    would.
    """
    df = run_query(f"""
        SELECT
            make_date(
                EXTRACT(YEAR FROM production_date)::int,
                EXTRACT(MONTH FROM production_date)::int, 1
            ) AS month_start,
            SUM(on_stream_hrs) AS total_hours,
            COUNT(*) AS known_hours_days
        FROM {VIEW_DAILY}
        WHERE on_stream_hrs IS NOT NULL
        GROUP BY month_start
        ORDER BY month_start
    """)
    df["month_start"] = pd.to_datetime(df["month_start"])
    df["availability_pct"] = 100 * df["total_hours"] / (df["known_hours_days"] * 24)
    return df


def well_summary() -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance + vw_well_lifetime_summary +
    list_wells() - one row per well: first oil day, peak daily oil and the
    date it happened, cumulative oil, that well's % of the field's total
    oil (A1/A2 generalized), and availability (well_availability(),
    computed set-based here for all 7 wells in one query rather than one
    round trip per well). Feeds both the Well Contribution chart and the
    Well Summary table on Field Overview.
    """
    first_oil = run_query(f"""
        SELECT npd_well_bore_code, MIN(production_date) AS first_oil_date
        FROM {VIEW_DAILY} WHERE bore_oil_vol > 0
        GROUP BY npd_well_bore_code
    """)
    peak = run_query(f"""
        SELECT DISTINCT ON (npd_well_bore_code)
            npd_well_bore_code, production_date AS peak_date, bore_oil_vol AS peak_oil
        FROM {VIEW_DAILY}
        WHERE bore_oil_vol IS NOT NULL
        ORDER BY npd_well_bore_code, bore_oil_vol DESC
    """)
    availability = run_query(f"""
        SELECT npd_well_bore_code,
            ROUND(100.0 * SUM(on_stream_hrs) / (COUNT(*) * 24), 1) AS availability_pct
        FROM {VIEW_DAILY}
        WHERE on_stream_hrs IS NOT NULL
        GROUP BY npd_well_bore_code
    """)
    lifetime = run_query(f"SELECT npd_well_bore_code, wellbore_name, total_oil FROM {VIEW_LIFETIME}")

    df = lifetime.merge(first_oil, on="npd_well_bore_code", how="left")
    df = df.merge(peak, on="npd_well_bore_code", how="left")
    df = df.merge(availability, on="npd_well_bore_code", how="left")
    df["first_oil_date"] = pd.to_datetime(df["first_oil_date"])
    df["peak_date"] = pd.to_datetime(df["peak_date"])
    df["oil_pct_of_field"] = 100 * df["total_oil"] / df["total_oil"].sum()
    return df.sort_values("total_oil", ascending=False, na_position="last")


def field_monthly() -> pd.DataFrame:
    """analytics.vw_field_monthly_summary - A6, A7, A8, A10"""
    df = run_query(f"""
        SELECT month_start, active_wells, oil_volume, gas_volume,
               water_volume, water_injection_volume, on_stream_hours
        FROM {VIEW_FIELD_MONTHLY}
        ORDER BY month_start
    """)
    df["month_start"] = pd.to_datetime(df["month_start"])
    return df


def top_field_months(limit: int = 10) -> pd.DataFrame:
    """analytics.vw_field_monthly_summary - A8"""
    df = run_query(f"""
        SELECT month_start, oil_volume,
               RANK() OVER (ORDER BY oil_volume DESC) AS oil_rank
        FROM {VIEW_FIELD_MONTHLY}
        WHERE oil_volume IS NOT NULL
        ORDER BY oil_rank
        LIMIT %s
    """, (limit,))
    df["month_start"] = pd.to_datetime(df["month_start"])
    return df


def active_wells_by_type() -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance + list_wells() - A10 generalized by
    well type. Type is each well's fixed dominant type (from list_wells()),
    not its day-level well_type value, so a well doesn't switch categories
    between chart points - 15/9-F-5's 144-day early OP period doesn't make
    it a producer for one segment of this chart, it's an injector
    throughout, same as everywhere else in this app.

    Explicitly zero-fills months where a type has no active wells (e.g.
    Feb-Mar 2008, before any injector first comes online) rather than
    omitting the row - a naive groupby drops those months entirely, which
    makes a line chart interpolate straight across a real dip to zero
    instead of showing it.
    """
    bounds = run_query(f"""
        SELECT
            make_date(MIN(EXTRACT(YEAR FROM production_date)::int),
                       MIN(EXTRACT(MONTH FROM production_date)::int), 1) AS first_month,
            MAX(production_date) AS last_date
        FROM {VIEW_DAILY}
    """)
    daily_active = run_query(f"""
        SELECT DISTINCT
            make_date(
                EXTRACT(YEAR FROM production_date)::int,
                EXTRACT(MONTH FROM production_date)::int, 1
            ) AS month_start,
            npd_well_bore_code
        FROM {VIEW_DAILY}
        WHERE on_stream_hrs > 0
    """)
    wells = list_wells()
    merged = daily_active.merge(wells[["npd_well_bore_code", "well_type_label"]], on="npd_well_bore_code")
    counts = (
        merged.groupby(["month_start", "well_type_label"])
        .size()
        .reset_index(name="active_wells")
        .rename(columns={"well_type_label": "well_type"})
    )

    full_months = pd.date_range(bounds["first_month"].iloc[0], bounds["last_date"].iloc[0], freq="MS")
    grid = pd.MultiIndex.from_product(
        [full_months, wells["well_type_label"].unique()], names=["month_start", "well_type"]
    ).to_frame(index=False)
    counts["month_start"] = pd.to_datetime(counts["month_start"])
    result = grid.merge(counts, on=["month_start", "well_type"], how="left")
    result["active_wells"] = result["active_wells"].fillna(0).astype(int)
    return result


def well_daily(well_code: int) -> pd.DataFrame:
    """analytics.vw_daily_well_performance"""
    df = run_query(f"""
        SELECT production_date, bore_oil_vol, bore_gas_vol, bore_wat_vol,
               bore_wi_vol, on_stream_hrs, is_active,
               avg_downhole_pressure, avg_choke_size_p
        FROM {VIEW_DAILY}
        WHERE npd_well_bore_code = %s
        ORDER BY production_date
    """, (well_code,))
    df["production_date"] = pd.to_datetime(df["production_date"])
    return df


def well_snapshot(well_code: int) -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance - single-row "as of last record"
    snapshot: exact-date, no smoothing, same methodology as A5/the episode
    checkpoints. latest_oil_rate is the well's most recently recorded day,
    which is 0 for a well that ended shut-in (not a decline to zero) -
    always paired with latest_record_date and latest_is_active so that
    distinction is visible, not implied.
    """
    df = run_query(f"""
        WITH latest AS (
            SELECT production_date, bore_oil_vol, is_active
            FROM {VIEW_DAILY}
            WHERE npd_well_bore_code = %s AND on_stream_hrs IS NOT NULL
            ORDER BY production_date DESC
            LIMIT 1
        ),
        first_oil AS (
            SELECT MIN(production_date) AS first_oil_date
            FROM {VIEW_DAILY}
            WHERE npd_well_bore_code = %s AND bore_oil_vol > 0
        )
        SELECT
            latest.production_date AS latest_record_date,
            latest.bore_oil_vol AS latest_oil_rate,
            latest.is_active AS latest_is_active,
            first_oil.first_oil_date
        FROM latest, first_oil
    """, (well_code, well_code))
    df["latest_record_date"] = pd.to_datetime(df["latest_record_date"])
    df["first_oil_date"] = pd.to_datetime(df["first_oil_date"])
    return df


def well_monthly(well_code: int) -> pd.DataFrame:
    """analytics.vw_monthly_well_performance"""
    return run_query(f"""
        SELECT year, month, oil_volume, gas_volume, water_volume,
               water_injection_volume, on_stream_hours, producing_days,
               calendar_records
        FROM {VIEW_MONTHLY}
        WHERE npd_well_bore_code = %s
        ORDER BY year, month
    """, (well_code,))


def well_lifetime(well_code: int) -> pd.DataFrame:
    """analytics.vw_well_lifetime_summary"""
    return run_query(f"""
        SELECT *
        FROM {VIEW_LIFETIME}
        WHERE npd_well_bore_code = %s
    """, (well_code,))


def well_transitions(well_code: int) -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance - single-well version of A11.
    A "transition" is a change in is_active between one recorded day and
    the next - not real calendar-time shutdown duration (same definition
    as A11 in sql/06_analysis.sql).
    """
    df = run_query(f"""
        WITH daily_state AS (
            SELECT
                production_date,
                is_active,
                LAG(is_active) OVER (ORDER BY production_date) AS previous_is_active
            FROM {VIEW_DAILY}
            WHERE npd_well_bore_code = %s AND on_stream_hrs IS NOT NULL
        )
        SELECT
            production_date,
            CASE
                WHEN previous_is_active AND NOT is_active THEN 'shutdown'
                WHEN NOT previous_is_active AND is_active THEN 'restart'
            END AS transition_type
        FROM daily_state
        WHERE previous_is_active IS NOT NULL
          AND is_active IS DISTINCT FROM previous_is_active
        ORDER BY production_date
    """, (well_code,))
    df["production_date"] = pd.to_datetime(df["production_date"])
    return df


def well_downtime_episodes(well_code: int) -> pd.DataFrame:
    """
    analytics.vw_downtime_episodes, filtered to one well - extends A11 (see
    sql/06_analysis.sql, "A11 extended") from counting transitions to full
    downtime episodes. The "gaps and islands" reconstruction itself lives in
    that view (sql/05_views.sql), computed once for all wells, rather than
    duplicated here - this used to be a hand-copy of the same CTE chain,
    parameterized to one well; see the view's own comment for the technique,
    the censoring behaviour of restart_date, and the exact-calendar-date
    checkpoint methodology recovery_pct inherits from A5.
    """
    df = run_query(f"""
        SELECT shutdown_date, restart_date, offline_days, oil_before, oil_after, recovery_pct
        FROM {VIEW_DOWNTIME}
        WHERE npd_well_bore_code = %s
        ORDER BY shutdown_date
    """, (well_code,))
    df["shutdown_date"] = pd.to_datetime(df["shutdown_date"])
    df["restart_date"] = pd.to_datetime(df["restart_date"])
    return df


def well_availability(well_code: int) -> float | None:
    """
    analytics.vw_daily_well_performance - on-stream hours as a % of possible
    hours across days with a known state. Days with NULL on_stream_hrs
    (DQ-003) are excluded from the denominator, not counted as inactive -
    an unknown state is not the same claim as an observed zero.
    """
    df = run_query(f"""
        SELECT
            SUM(on_stream_hrs) AS total_hours,
            COUNT(*) AS known_hours_days
        FROM {VIEW_DAILY}
        WHERE npd_well_bore_code = %s AND on_stream_hrs IS NOT NULL
    """, (well_code,))
    row = df.iloc[0]
    if row["known_hours_days"] == 0:
        return None
    return float(row["total_hours"]) / (float(row["known_hours_days"]) * 24) * 100


def ranking() -> pd.DataFrame:
    """
    analytics.vw_well_lifetime_summary - A1, A2, field-wide rank.
    Also ranks water injection - oil/gas/produced-water rank are
    meaningless for this field's 2 water injectors (always None/last), and
    without an injection rank they had no comparable metric on this page
    at all.
    """
    return run_query(f"""
        SELECT
            wellbore_name,
            total_oil,
            RANK() OVER (ORDER BY total_oil DESC NULLS LAST)         AS oil_rank,
            total_gas,
            RANK() OVER (ORDER BY total_gas DESC NULLS LAST)         AS gas_rank,
            total_water,
            DENSE_RANK() OVER (ORDER BY total_water DESC NULLS LAST) AS water_rank,
            total_water_injection,
            RANK() OVER (ORDER BY total_water_injection DESC NULLS LAST) AS injection_rank
        FROM {VIEW_LIFETIME}
        ORDER BY oil_rank
    """)


def normalized_profiles(well_codes: list[int]) -> pd.DataFrame:
    """
    analytics.vw_daily_well_performance + vw_well_lifetime_summary.
    Oil production indexed to days since each well's first positive oil
    day (A3) and expressed as % of that well's peak daily oil (A4) - lets
    wells that started producing on different calendar dates be compared
    on the same decline-shape axis.
    """
    if not well_codes:
        return pd.DataFrame(columns=["wellbore_name", "days_since_first_oil", "pct_of_peak"])
    df = run_query(f"""
        WITH first_oil AS (
            SELECT npd_well_bore_code, MIN(production_date) AS first_oil_date
            FROM {VIEW_DAILY}
            WHERE bore_oil_vol > 0 AND npd_well_bore_code = ANY(%s)
            GROUP BY npd_well_bore_code
        )
        SELECT
            dp.wellbore_name,
            dp.production_date,
            (dp.production_date - fo.first_oil_date) AS days_since_first_oil,
            dp.bore_oil_vol,
            ROUND(100.0 * dp.bore_oil_vol / NULLIF(ls.peak_daily_oil, 0), 1) AS pct_of_peak
        FROM {VIEW_DAILY} dp
        JOIN first_oil fo ON fo.npd_well_bore_code = dp.npd_well_bore_code
        JOIN {VIEW_LIFETIME} ls ON ls.npd_well_bore_code = dp.npd_well_bore_code
        WHERE dp.npd_well_bore_code = ANY(%s)
          AND dp.production_date >= fo.first_oil_date
          AND dp.bore_oil_vol IS NOT NULL
        ORDER BY dp.wellbore_name, days_since_first_oil
    """, (well_codes, well_codes))
    return df


def monthly_production_multi(well_codes: list[int]) -> pd.DataFrame:
    """
    analytics.vw_monthly_well_performance - actual (not normalized) monthly
    production per well, on real calendar time, for superposing selected
    wells on one chart. Complements normalized_profiles(), which indexes to
    days-since-first-oil instead - this shows raw magnitude and calendar
    timing side by side (e.g. whether one well was declining while another
    was still ramping up), which a normalized view deliberately discards.
    """
    if not well_codes:
        return pd.DataFrame(columns=["wellbore_name", "month_start", "oil_volume", "gas_volume", "water_volume"])
    df = run_query(f"""
        SELECT
            wellbore_name,
            make_date(year, month, 1) AS month_start,
            oil_volume, gas_volume, water_volume, water_injection_volume
        FROM {VIEW_MONTHLY}
        WHERE npd_well_bore_code = ANY(%s)
        ORDER BY wellbore_name, month_start
    """, (well_codes,))
    df["month_start"] = pd.to_datetime(df["month_start"])
    return df


def water_trends(well_codes: list[int]) -> pd.DataFrame:
    """analytics.vw_monthly_well_performance - per-well water-oil ratio, A6 generalized"""
    if not well_codes:
        return pd.DataFrame(columns=["wellbore_name", "month_start", "water_oil_ratio"])
    df = run_query(f"""
        SELECT
            wellbore_name,
            make_date(year, month, 1) AS month_start,
            water_volume, oil_volume,
            ROUND(water_volume / NULLIF(oil_volume, 0), 3) AS water_oil_ratio
        FROM {VIEW_MONTHLY}
        WHERE npd_well_bore_code = ANY(%s)
        ORDER BY wellbore_name, month_start
    """, (well_codes,))
    df["month_start"] = pd.to_datetime(df["month_start"])
    return df


def decline(well_codes: list[int]) -> pd.DataFrame:
    """analytics.vw_daily_well_performance - A5, restricted to selected wells"""
    if not well_codes:
        return pd.DataFrame()
    return run_query(f"""
        WITH ranked_oil AS (
            SELECT
                npd_well_bore_code, wellbore_name, production_date, bore_oil_vol,
                ROW_NUMBER() OVER (
                    PARTITION BY npd_well_bore_code ORDER BY bore_oil_vol DESC
                ) AS rn
            FROM {VIEW_DAILY}
            WHERE bore_oil_vol IS NOT NULL AND npd_well_bore_code = ANY(%s)
        ),
        peak_only AS (
            SELECT npd_well_bore_code, wellbore_name,
                   production_date AS peak_date, bore_oil_vol AS peak_volume
            FROM ranked_oil
            WHERE rn = 1
        )
        SELECT
            p.wellbore_name,
            p.peak_date,
            p.peak_volume,
            d30.bore_oil_vol  AS oil_30_days_after_peak,
            ROUND(100.0 * (p.peak_volume - d30.bore_oil_vol) / p.peak_volume, 1)  AS pct_decline_30_days,
            d90.bore_oil_vol  AS oil_90_days_after_peak,
            ROUND(100.0 * (p.peak_volume - d90.bore_oil_vol) / p.peak_volume, 1)  AS pct_decline_90_days,
            d365.bore_oil_vol AS oil_365_days_after_peak,
            ROUND(100.0 * (p.peak_volume - d365.bore_oil_vol) / p.peak_volume, 1) AS pct_decline_365_days
        FROM peak_only p
        LEFT JOIN {VIEW_DAILY} d30
            ON d30.npd_well_bore_code = p.npd_well_bore_code AND d30.production_date = p.peak_date + 30
        LEFT JOIN {VIEW_DAILY} d90
            ON d90.npd_well_bore_code = p.npd_well_bore_code AND d90.production_date = p.peak_date + 90
        LEFT JOIN {VIEW_DAILY} d365
            ON d365.npd_well_bore_code = p.npd_well_bore_code AND d365.production_date = p.peak_date + 365
        ORDER BY p.peak_volume DESC
    """, (well_codes,))


def dq_summary() -> pd.DataFrame:
    """analytics.vw_data_quality_review"""
    return run_query(f"""
        SELECT
            dq_issue,
            count(*)                          AS record_count,
            count(DISTINCT npd_well_bore_code) AS affected_wells,
            min(production_date)               AS earliest_date,
            max(production_date)               AS latest_date
        FROM {VIEW_DQ}
        GROUP BY dq_issue
        ORDER BY dq_issue
    """)


def dq_detail(dq_issue: str) -> pd.DataFrame:
    """analytics.vw_data_quality_review + vw_well_lifetime_summary (name lookup)"""
    df = run_query(f"""
        SELECT
            w.wellbore_name,
            dqr.production_date,
            dqr.review_reason
        FROM {VIEW_DQ} dqr
        JOIN {VIEW_LIFETIME} w ON w.npd_well_bore_code = dqr.npd_well_bore_code
        WHERE dqr.dq_issue = %s
        ORDER BY dqr.production_date
    """, (dq_issue,))
    df["production_date"] = pd.to_datetime(df["production_date"])
    return df
