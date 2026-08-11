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
    """analytics.vw_well_lifetime_summary"""
    return run_query(f"""
        SELECT npd_well_bore_code, wellbore_name
        FROM {VIEW_LIFETIME}
        ORDER BY wellbore_name
    """)


def field_kpis() -> pd.DataFrame:
    """analytics.vw_well_lifetime_summary - field-wide cumulative totals"""
    return run_query(f"""
        SELECT
            SUM(total_oil)             AS total_oil,
            SUM(total_gas)             AS total_gas,
            SUM(total_water)           AS total_water,
            SUM(total_water_injection) AS total_water_injection
        FROM {VIEW_LIFETIME}
    """)


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


def well_daily(well_code: int) -> pd.DataFrame:
    """analytics.vw_daily_well_performance"""
    df = run_query(f"""
        SELECT production_date, bore_oil_vol, bore_gas_vol, bore_wat_vol,
               bore_wi_vol, on_stream_hrs, is_active
        FROM {VIEW_DAILY}
        WHERE npd_well_bore_code = %s
        ORDER BY production_date
    """, (well_code,))
    df["production_date"] = pd.to_datetime(df["production_date"])
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


def ranking() -> pd.DataFrame:
    """analytics.vw_well_lifetime_summary - A1, A2, field-wide rank"""
    return run_query(f"""
        SELECT
            wellbore_name,
            total_oil,
            RANK() OVER (ORDER BY total_oil DESC NULLS LAST)         AS oil_rank,
            total_gas,
            RANK() OVER (ORDER BY total_gas DESC NULLS LAST)         AS gas_rank,
            total_water,
            DENSE_RANK() OVER (ORDER BY total_water DESC NULLS LAST) AS water_rank
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
        return pd.DataFrame(columns=["wellbore_name", "year", "month", "water_oil_ratio"])
    return run_query(f"""
        SELECT
            wellbore_name, year, month, water_volume, oil_volume,
            ROUND(water_volume / NULLIF(oil_volume, 0), 3) AS water_oil_ratio
        FROM {VIEW_MONTHLY}
        WHERE npd_well_bore_code = ANY(%s)
        ORDER BY wellbore_name, year, month
    """, (well_codes,))


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
