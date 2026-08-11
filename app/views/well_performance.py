import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

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
    go.Scatter(x=daily["production_date"], y=daily["bore_oil_vol"], name="Oil", mode="lines"),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=daily["production_date"], y=daily["bore_wat_vol"], name="Water", mode="lines"),
    secondary_y=False,
)
fig.add_trace(
    go.Scatter(x=daily["production_date"], y=daily["bore_gas_vol"], name="Gas", mode="lines"),
    secondary_y=True,
)
fig.update_yaxes(title_text="Oil / Water (Sm³ / day)", secondary_y=False)
fig.update_yaxes(title_text="Gas (Sm³ / day)", secondary_y=True)
st.plotly_chart(fig, width="stretch")

st.subheader("Water production")
fig2 = px.line(daily, x="production_date", y="bore_wat_vol")
fig2.update_layout(yaxis_title="Sm³ / day", xaxis_title=None)
st.plotly_chart(fig2, width="stretch")

st.subheader("On-stream hours and shutdown/restart events")
st.caption("Shutdown/restart = a change in active state between two consecutive recorded days  -  A11")
fig3 = px.bar(daily, x="production_date", y="on_stream_hrs")
fig3.update_layout(yaxis_title="Hours / day", xaxis_title=None)
st.plotly_chart(fig3, width="stretch")

transitions = q.well_transitions(well_code)
tc1, tc2 = st.columns(2)
tc1.metric("Shutdowns", int((transitions["transition_type"] == "shutdown").sum()))
tc2.metric("Restarts", int((transitions["transition_type"] == "restart").sum()))
if not transitions.empty:
    st.dataframe(
        transitions.rename(columns={"production_date": "date", "transition_type": "event"}),
        width="stretch",
        hide_index=True,
    )
else:
    st.caption("No shutdown/restart transitions recorded for this well.")

st.subheader("Peak production")
peak_row = daily.loc[daily["bore_oil_vol"].idxmax()] if daily["bore_oil_vol"].notna().any() else None
if peak_row is not None:
    st.write(
        f"Peak daily oil: **{peak_row['bore_oil_vol']:,.1f} Sm³** on "
        f"**{peak_row['production_date'].date()}**"
    )
fig4 = go.Figure()
fig4.add_trace(go.Scatter(x=daily["production_date"], y=daily["bore_oil_vol"], mode="lines", name="Daily oil"))
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
fig5 = px.area(daily_sorted, x="production_date", y="cumulative_oil")
fig5.update_layout(yaxis_title="Cumulative Sm³", xaxis_title=None)
st.plotly_chart(fig5, width="stretch")

st.caption(f"Source: `{q.VIEW_DAILY}`")
