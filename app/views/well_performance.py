import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import colors as c
import queries as q

st.title("Well Performance")

wells = q.list_wells()
well_type_by_name = dict(zip(wells["wellbore_name"], wells["well_type_label"]))
well_name = st.selectbox(
    "Select well", wells["wellbore_name"],
    format_func=lambda name: f"{name} ({well_type_by_name[name]})",
    key="well_performance_select",
)
well_code = int(wells.loc[wells["wellbore_name"] == well_name, "npd_well_bore_code"].iloc[0])

lifetime = q.well_lifetime(well_code).iloc[0]
daily = q.well_daily(well_code)
snapshot = q.well_snapshot(well_code).iloc[0]
episodes = q.well_downtime_episodes(well_code)
availability = q.well_availability(well_code)
monthly_wor = q.water_trends([well_code])
completed = episodes.dropna(subset=["restart_date"])
still_down = len(episodes) - len(completed)

latest_wor_row = monthly_wor.dropna(subset=["water_oil_ratio"]).tail(1)
has_oil_production = daily["bore_oil_vol"].notna().any()

# Orientation, not exhaustive detail: what has this well produced, is it
# currently operating, and where is it in its life - the diagnostic detail
# behind each number is one scroll away, in the section it belongs to.
r1c1, r1c2, r1c3, r1c4 = st.columns(4)
r1c1.metric("Cumulative oil (Sm³)", f"{lifetime['total_oil']:,.0f}" if lifetime["total_oil"] is not None else "n/a")
r1c2.metric("Peak oil (Sm³/d)", f"{lifetime['peak_daily_oil']:,.0f}" if lifetime["peak_daily_oil"] is not None else "n/a")
r1c3.metric(
    "Latest oil (Sm³/d)",
    f"{snapshot['latest_oil_rate']:,.1f}" if snapshot["latest_oil_rate"] is not None else "n/a",
    help=(
        f"As of {snapshot['latest_record_date'].date()}, this well's most "
        f"recently recorded day - "
        + ("active." if snapshot["latest_is_active"] else "inactive on that date, not a decline to zero.")
    ),
)
r1c4.metric("Availability", f"{availability:.1f}%" if availability is not None else "n/a")

r2c1, r2c2, r2c3, r2c4 = st.columns(4)
r2c1.metric("Production days", f"{lifetime['number_of_production_days']:,.0f}")
r2c2.metric("Inactive episodes", len(episodes))
r2c3.metric(
    "Water/oil ratio",
    f"{latest_wor_row['water_oil_ratio'].iloc[0]:.2f}" if not latest_wor_row.empty else "n/a",
    help=(
        f"Latest calendar month with oil production: {latest_wor_row['month_start'].iloc[0].strftime('%Y-%m')}."
        if not latest_wor_row.empty
        else "No month with recorded oil production for this well."
    ),
)
r2c4.metric(
    "Recorded period",
    f"{lifetime['first_record_date'].year} → {lifetime['last_record_date'].year}",
    help=f"{lifetime['first_record_date']} → {lifetime['last_record_date']}",
)

with st.expander("View data lineage / SQL source"):
    st.caption(
        f"`{q.VIEW_LIFETIME}`, `{q.VIEW_DAILY}`, `{q.VIEW_MONTHLY}`  -  "
        f"peak from A4, water/oil ratio from A6, availability and inactive "
        f"episodes extend A11 (see sql/06_analysis.sql, \"A11 extended\")."
    )
    st.caption(f"Recorded days (including days with no on-stream-hours reading): {lifetime['recorded_days']:,.0f}")

st.header("Production history")
_first_oil_str = (
    str(snapshot["first_oil_date"].date())
    if not pd.isna(snapshot["first_oil_date"])
    else "n/a (this well never produces oil)"
)
st.caption(
    f"Daily oil / gas / water volumes since first oil on {_first_oil_str}. "
    "Oil and water share the left axis; gas is plotted on the right axis - "
    "this well's gas-to-oil ratio is ~140-155x (consistent across all 7 "
    "wells), which would otherwise flatten oil/water to the baseline on a "
    "single shared axis."
)
fig = make_subplots(specs=[[{"secondary_y": True}]])
fig.add_trace(
    go.Scatter(x=daily["production_date"], y=daily["bore_oil_vol"], name="Oil",
               mode="lines", line=dict(color=c.OIL)),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=daily["production_date"], y=daily["bore_wat_vol"], name="Water",
               mode="lines", line=dict(color=c.WATER)),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=daily["production_date"], y=daily["bore_gas_vol"], name="Gas",
               mode="lines", line=dict(color=c.GAS)),
    secondary_y=True,
)
fig.update_yaxes(title_text="Oil / Water (Sm³ / day)", secondary_y=False)
fig.update_yaxes(title_text="Gas (Sm³ / day)", secondary_y=True)
st.plotly_chart(fig, width="stretch")

has_injection = daily["bore_wi_vol"].astype("float64").gt(0).any()
if has_injection:
    st.subheader("Water injection")
    if has_oil_production:
        st.caption(
            "Oil overlaid for reference on a separate axis - this well's peak "
            "water injection is ~25x its peak oil (unlike the field-wide "
            "~2x, sharing one axis here would flatten oil to the baseline, "
            "the same problem fixed on Field Overview's production chart). "
            "A dual axis makes both readable, but can visually suggest a "
            "correlation that isn't really there - read the timing, not the "
            "relative heights."
        )
    else:
        st.caption(
            "This well never produces oil (a pure injector) - no oil trace "
            "to overlay."
        )
    fig2b = make_subplots(specs=[[{"secondary_y": True}]])
    fig2b.add_trace(
        go.Scatter(x=daily["production_date"], y=daily["bore_wi_vol"], name="Water injection",
                   mode="lines", line=dict(color=c.WATER)),
        secondary_y=False,
    )
    if has_oil_production:
        fig2b.add_trace(
            go.Scatter(x=daily["production_date"], y=daily["bore_oil_vol"], name="Oil",
                       mode="lines", line=dict(color=c.OIL)),
            secondary_y=True,
        )
        fig2b.update_yaxes(title_text="Oil (Sm³ / day)", secondary_y=True)
    fig2b.update_yaxes(title_text="Water injection (Sm³ / day)", secondary_y=False)
    st.plotly_chart(fig2b, width="stretch")

st.header("Availability & interruptions")
st.caption(
    "\"Shutdown\" / \"restart\" below are concise labels for an *observed* "
    "state change, not a confirmed operational event (planned workover, "
    "field-wide outage, or a genuine failure all look the same here) - "
    "a shutdown is this well's first inactive day (ON_STREAM_HRS = 0) "
    "after an observed active day, and a restart is its return to active "
    "state. Reconstructed from daily records with PostgreSQL window "
    "functions: LAG() flags each day the state differs from the day "
    "before, a running SUM() of those flags groups consecutive same-state "
    "days into one episode  -  extends A11's transition count into full "
    "episodes (see sql/06_analysis.sql, \"A11 extended\")."
)
fig3 = px.bar(daily, x="production_date", y="on_stream_hrs")
fig3.update_layout(yaxis_title="Hours / day", xaxis_title=None)
st.plotly_chart(fig3, width="stretch")

ac1, ac2, ac3, ac4 = st.columns(4)
ac1.metric("Offline days", f"{completed['offline_days'].sum():,.0f}" if not completed.empty else "0")
ac2.metric("Longest outage", f"{completed['offline_days'].max():,.0f} d" if not completed.empty else "n/a")
ac3.metric("Median outage", f"{completed['offline_days'].median():,.0f} d" if not completed.empty else "n/a")
ac4.metric("Restarts", len(completed))
st.caption(
    "Availability = on-stream hours as a % of hours across days with a "
    "known state (days with no on-stream-hours reading are excluded, not "
    "counted as inactive)."
    + (f" {still_down} inactive episode had no restart before this well's "
       "last recorded day - still inactive when the record ends (duration "
       "unknown), excluded from offline-day totals above."
       if still_down else "")
)

has_water_production = daily["bore_wat_vol"].notna().any()
if has_water_production:
    st.header("Water performance")
    st.caption("Raw water rate alongside the water/oil ratio it produces - A6 for this well.")
    wc1, wc2 = st.columns(2)
    with wc1:
        fig2 = px.line(daily, x="production_date", y="bore_wat_vol", color_discrete_sequence=[c.WATER])
        fig2.update_layout(yaxis_title="Water (Sm³ / day)", xaxis_title=None)
        st.plotly_chart(fig2, width="stretch")
    with wc2:
        if monthly_wor["water_oil_ratio"].notna().any():
            fig_wor = px.line(monthly_wor, x="month_start", y="water_oil_ratio", color_discrete_sequence=[c.WATER])
            fig_wor.update_layout(yaxis_title="Water / oil ratio (monthly)", xaxis_title=None)
            st.plotly_chart(fig_wor, width="stretch")
        else:
            st.caption("No monthly oil/water data for this well.")

has_pressure = daily["avg_downhole_pressure"].notna().any()
if has_pressure:
    st.header("Operating conditions")
    st.caption(
        "Downhole pressure and choke size - not recorded for this field's "
        "water injectors (0% coverage on those two wells), present for all "
        "5 oil producers."
    )
    oc1, oc2 = st.columns(2)
    with oc1:
        fig_p = px.line(daily, x="production_date", y="avg_downhole_pressure")
        fig_p.update_layout(yaxis_title="Downhole pressure (Bar)", xaxis_title=None)
        st.plotly_chart(fig_p, width="stretch")
    with oc2:
        fig_ch = px.line(daily, x="production_date", y="avg_choke_size_p")
        fig_ch.update_layout(yaxis_title="Choke size (%)", xaxis_title=None)
        st.plotly_chart(fig_ch, width="stretch")

st.header("Event / restart performance")
if not episodes.empty:
    episodes = episodes.copy()
    episodes["shutdown_date"] = episodes["shutdown_date"].dt.date
    episodes["restart_date"] = episodes["restart_date"].dt.date
    for oil_col in ["oil_before", "oil_after", "recovery_pct"]:
        episodes[oil_col] = pd.to_numeric(episodes[oil_col], errors="coerce")
    table = episodes.rename(columns={
        "shutdown_date": "Shutdown", "restart_date": "Restart",
        "offline_days": "Offline days", "oil_before": "Oil before (Sm³/d)",
        "oil_after": "Oil after (Sm³/d)", "recovery_pct": "Recovery %",
    })
    st.dataframe(
        table, width="stretch", hide_index=True,
        column_config={
            "Oil before (Sm³/d)": st.column_config.NumberColumn(format="%.2f"),
            "Oil after (Sm³/d)": st.column_config.NumberColumn(format="%.2f"),
            "Recovery %": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.caption(
        "Oil before/after are exact-date checkpoints (the day before the "
        "shutdown, the day of the restart), not a smoothed trend - same "
        "methodology as A5's peak-vs-checkpoint comparison. Recovery % can "
        "read as several hundred, even several thousand, percent when oil "
        "immediately before a shutdown was already near zero - a small "
        "absolute change becomes a large ratio. Read it next to the raw "
        "before/after values, not in isolation. A \"None\" oil value means "
        "this well has no oil measurement for that day (e.g. it was "
        "operating as a water injector at the time), not a reading of zero."
    )
else:
    st.caption("No inactive periods recorded for this well.")

st.subheader("Peak production")
if has_oil_production:
    peak_row = daily.loc[daily["bore_oil_vol"].idxmax()]
    st.write(
        f"Peak daily oil: **{peak_row['bore_oil_vol']:,.1f} Sm³** on "
        f"**{peak_row['production_date'].date()}**"
    )
    fig4 = go.Figure()
    fig4.add_trace(go.Scatter(
        x=daily["production_date"], y=daily["bore_oil_vol"],
        mode="lines", name="Daily oil", line=dict(color=c.OIL),
    ))
    fig4.add_trace(go.Scatter(
        x=[peak_row["production_date"]], y=[peak_row["bore_oil_vol"]],
        mode="markers", marker=dict(size=12, color="red"), name="Peak",
    ))
    fig4.update_layout(yaxis_title="Sm³ / day", xaxis_title=None)
    st.plotly_chart(fig4, width="stretch")

    st.subheader("Cumulative production")
    daily_sorted = daily.sort_values("production_date").copy()
    daily_sorted["cumulative_oil"] = daily_sorted["bore_oil_vol"].fillna(0).cumsum()
    fig5 = px.area(daily_sorted, x="production_date", y="cumulative_oil", color_discrete_sequence=[c.OIL])
    fig5.update_layout(yaxis_title="Cumulative Sm³", xaxis_title=None)
    st.plotly_chart(fig5, width="stretch")
else:
    st.caption("This well never produces oil (a pure injector) - no peak or cumulative oil to show.")
