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
    analytics.vw_daily_well_performance - extends A11 (see sql/06_analysis.sql,
    "A11 extended") from counting transitions to reconstructing full downtime
    episodes: "gaps and islands" groups consecutive same-state days via LAG()
    + a running SUM() as a group id, GROUP BY collapses each run into one
    episode. Only inactive episodes with an observed preceding active day
    count as a shutdown (excludes the case where the well's first recorded
    day is already inactive) - this is what makes shutdown counts match
    well_transitions() exactly, verified against all 7 wells.

    restart_date is NaT for a well still inactive at the end of its recorded
    history (censored, not a zero-length outage). recovery_pct can swing to
    triple digits when oil_before is near zero - a real feature of the data,
    not an error; the app shows oil_before/oil_after alongside it rather
    than the percentage alone.
    """
    df = run_query(f"""
        WITH daily_state AS (
            SELECT production_date, is_active, bore_oil_vol
            FROM {VIEW_DAILY}
            WHERE npd_well_bore_code = %s AND on_stream_hrs IS NOT NULL
        ),
        flagged AS (
            SELECT *,
                LAG(is_active) OVER (ORDER BY production_date) AS previous_is_active
            FROM daily_state
        ),
        episode_marks AS (
            SELECT *,
                CASE
                    WHEN previous_is_active IS NULL THEN 1
                    WHEN is_active IS DISTINCT FROM previous_is_active THEN 1
                    ELSE 0
                END AS starts_new_episode
            FROM flagged
        ),
        episode_ids AS (
            SELECT *,
                SUM(starts_new_episode) OVER (ORDER BY production_date) AS episode_id
            FROM episode_marks
        ),
        episodes AS (
            SELECT episode_id, is_active,
                bool_or(previous_is_active IS NULL) AS is_first_episode,
                MIN(production_date) AS episode_start
            FROM episode_ids
            GROUP BY episode_id, is_active
        ),
        episodes_seq AS (
            SELECT *,
                LEAD(episode_start) OVER (ORDER BY episode_id) AS next_episode_start
            FROM episodes
        ),
        shutdowns AS (
            SELECT episode_start AS shutdown_date, next_episode_start AS restart_date
            FROM episodes_seq
            WHERE is_active = false AND NOT is_first_episode
        )
        SELECT
            s.shutdown_date,
            s.restart_date,
            (s.restart_date - s.shutdown_date) AS offline_days,
            b.bore_oil_vol AS oil_before,
            a.bore_oil_vol AS oil_after,
            ROUND(100.0 * a.bore_oil_vol / NULLIF(b.bore_oil_vol, 0), 1) AS recovery_pct
        FROM shutdowns s
        LEFT JOIN {VIEW_DAILY} b
            ON b.npd_well_bore_code = %s AND b.production_date = s.shutdown_date - 1
        LEFT JOIN {VIEW_DAILY} a
            ON a.npd_well_bore_code = %s AND a.production_date = s.restart_date
        ORDER BY s.shutdown_date
    """, (well_code, well_code, well_code))
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
