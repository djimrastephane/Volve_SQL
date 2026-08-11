import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import queries as q

st.title("Field Overview")
st.caption("Whole-field view, all 7 wellbores combined.")

kpis = q.field_kpis().iloc[0]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Cumulative oil (Sm³)", f"{kpis['total_oil']:,.0f}")
c2.metric("Cumulative gas (Sm³)", f"{kpis['total_gas']:,.0f}")
c3.metric("Cumulative water produced (Sm³)", f"{kpis['total_water']:,.0f}")
c4.metric("Cumulative water injected (Sm³)", f"{kpis['total_water_injection']:,.0f}")
st.caption(f"Source: `{q.VIEW_LIFETIME}`")

field = q.field_monthly()

st.subheader("Active wells")
st.caption("Wells with at least one on-stream day that month  -  engineering question A10")
fig = px.line(field, x="month_start", y="active_wells", markers=True)
fig.update_layout(yaxis_title="Active wells", xaxis_title=None)
st.plotly_chart(fig, width="stretch")

st.subheader("Production history")
st.caption(
    "Oil and water share the left axis; gas is plotted on the right axis - "
    "cumulative gas is ~150x cumulative oil (1.48B vs 10M Sm³), so on a "
    "single shared axis gas dominates the chart and flattens oil/water to "
    "the baseline, making them unreadable."
)
fig2 = make_subplots(specs=[[{"secondary_y": True}]])
fig2.add_trace(
    go.Scatter(x=field["month_start"], y=field["oil_volume"], name="Oil", mode="lines"),
    secondary_y=False,
)
fig2.add_trace(
    go.Scatter(x=field["month_start"], y=field["water_volume"], name="Water", mode="lines"),
    secondary_y=False,
)
fig2.add_trace(
    go.Scatter(x=field["month_start"], y=field["gas_volume"], name="Gas", mode="lines"),
    secondary_y=True,
)
fig2.update_yaxes(title_text="Oil / Water (Sm³ / month)", secondary_y=False)
fig2.update_yaxes(title_text="Gas (Sm³ / month)", secondary_y=True)
st.plotly_chart(fig2, width="stretch")

st.subheader("Water injection")
col_a, col_b = st.columns(2)
with col_a:
    fig3 = px.bar(field, x="month_start", y="water_injection_volume")
    fig3.update_layout(title="Monthly", yaxis_title="Sm³", xaxis_title=None)
    st.plotly_chart(fig3, width="stretch")
with col_b:
    field["cumulative_water_injection"] = field["water_injection_volume"].cumsum()
    fig3b = px.area(field, x="month_start", y="cumulative_water_injection")
    fig3b.update_layout(title="Cumulative  -  A7", yaxis_title="Sm³", xaxis_title=None)
    st.plotly_chart(fig3b, width="stretch")

st.subheader("Field production trends")
col_c, col_d = st.columns(2)
with col_c:
    field["water_oil_ratio"] = field["water_volume"] / field["oil_volume"].replace(0, None)
    fig4 = px.line(field, x="month_start", y="water_oil_ratio")
    fig4.update_layout(title="Water / oil ratio  -  A6", yaxis_title="Ratio", xaxis_title=None)
    st.plotly_chart(fig4, width="stretch")
with col_d:
    top_months = q.top_field_months(10)
    fig5 = px.bar(top_months, x="month_start", y="oil_volume")
    fig5.update_layout(title="Highest-producing months  -  A8", yaxis_title="Sm³ oil", xaxis_title=None)
    st.plotly_chart(fig5, width="stretch")

st.caption(f"Source: `{q.VIEW_FIELD_MONTHLY}`")
