import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import colors as c
import queries as q

st.title("Well Performance")

wells = q.list_wells()
well_name = st.selectbox("Select well", wells["wellbore_name"], key="well_performance_select")
well_code = int(wells.loc[wells["wellbore_name"] == well_name, "npd_well_bore_code"].iloc[0])

lifetime = q.well_lifetime(well_code).iloc[0]
daily = q.well_daily(well_code)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Peak daily oil (Sm³)", f"{lifetime['peak_daily_oil']:,.0f}" if lifetime["peak_daily_oil"] is not None else "n/a")
c2.metric("Cumulative oil (Sm³)", f"{lifetime['total_oil']:,.0f}" if lifetime["total_oil"] is not None else "n/a")
c3.metric("Recorded days", f"{lifetime['recorded_days']:,.0f}")
c4.metric("Production days", f"{lifetime['number_of_production_days']:,.0f}")
st.caption(f"Source: `{q.VIEW_LIFETIME}`  -  peak from A4")

st.subheader("Production history")
st.caption(
    "Daily oil / gas / water volumes. Oil and water share the left axis; "
    "gas is plotted on the right axis - this well's gas-to-oil ratio is "
    "~140-155x (consistent across all 7 wells), which would otherwise "
    "flatten oil/water to the baseline on a single shared axis."
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

st.subheader("Water production")
fig2 = px.line(daily, x="production_date", y="bore_wat_vol", color_discrete_sequence=[c.WATER])
fig2.update_layout(yaxis_title="Sm³ / day", xaxis_title=None)
st.plotly_chart(fig2, width="stretch")

has_injection = daily["bore_wi_vol"].astype("float64").gt(0).any()
if has_injection:
    st.subheader("Water injection")
    has_oil = daily["bore_oil_vol"].notna().any()
    if has_oil:
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
    if has_oil:
        fig2b.add_trace(
            go.Scatter(x=daily["production_date"], y=daily["bore_oil_vol"], name="Oil",
                       mode="lines", line=dict(color=c.OIL)),
            secondary_y=True,
        )
        fig2b.update_yaxes(title_text="Oil (Sm³ / day)", secondary_y=True)
    fig2b.update_yaxes(title_text="Water injection (Sm³ / day)", secondary_y=False)
    st.plotly_chart(fig2b, width="stretch")

st.subheader("On-stream hours and inactive periods")
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

episodes = q.well_downtime_episodes(well_code)
availability = q.well_availability(well_code)
completed = episodes.dropna(subset=["restart_date"])
still_down = len(episodes) - len(completed)

st.markdown("**On-stream performance**")
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Availability", f"{availability:.1f}%" if availability is not None else "n/a")
k2.metric("Shutdown events", len(episodes))
k3.metric("Offline days", f"{completed['offline_days'].sum():,.0f}" if not completed.empty else "0")
k4.metric("Longest outage", f"{completed['offline_days'].max():,.0f} d" if not completed.empty else "n/a")
k5.metric("Median outage", f"{completed['offline_days'].median():,.0f} d" if not completed.empty else "n/a")
k6.metric("Restarts", len(completed))
st.caption(
    "Availability = on-stream hours as a % of hours across days with a "
    "known state (days with no on-stream-hours reading are excluded, not "
    "counted as inactive)  -  " + f"`{q.VIEW_DAILY}`."
    + (f" {still_down} shutdown episode had no restart before this well's "
       "last recorded day - still inactive when the record ends (duration "
       "unknown), excluded from offline-day totals above."
       if still_down else "")
)

st.markdown("**Shutdown / restart history**")
if not episodes.empty:
    episodes = episodes.copy()
    episodes["shutdown_date"] = episodes["shutdown_date"].dt.date
    episodes["restart_date"] = episodes["restart_date"].dt.date
    table = episodes.rename(columns={
        "shutdown_date": "Shutdown", "restart_date": "Restart",
        "offline_days": "Offline days", "oil_before": "Oil before (Sm³/d)",
        "oil_after": "Oil after (Sm³/d)", "recovery_pct": "Recovery %",
    })
    st.dataframe(table, width="stretch", hide_index=True)
    st.caption(
        "Oil before/after are exact-date checkpoints (the day before the "
        "shutdown, the day of the restart), not a smoothed trend - same "
        "methodology as A5's peak-vs-checkpoint comparison. Recovery % can "
        "read as several hundred, even several thousand, percent when oil "
        "immediately before a shutdown was already near zero - a small "
        "absolute change becomes a large ratio. Read it next to the raw "
        "before/after values, not in isolation. A blank oil value means "
        "this well has no oil measurement for that day (e.g. it was "
        "operating as a water injector at the time)."
    )
else:
    st.caption("No inactive periods recorded for this well.")

st.subheader("Peak production")
peak_row = daily.loc[daily["bore_oil_vol"].idxmax()] if daily["bore_oil_vol"].notna().any() else None
if peak_row is not None:
    st.write(
        f"Peak daily oil: **{peak_row['bore_oil_vol']:,.1f} Sm³** on "
        f"**{peak_row['production_date'].date()}**"
    )
fig4 = go.Figure()
fig4.add_trace(go.Scatter(
    x=daily["production_date"], y=daily["bore_oil_vol"],
    mode="lines", name="Daily oil", line=dict(color=c.OIL),
))
if peak_row is not None:
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

st.caption(f"Source: `{q.VIEW_DAILY}`")
