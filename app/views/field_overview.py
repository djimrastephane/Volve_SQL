import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import colors as c
import queries as q

st.title("Field Overview")
st.caption("Whole-field view - 7 wellbores: 5 oil producers, 2 water injectors.")

kpis_by_type = q.field_kpis_by_type().set_index("well_type")
producer_kpis = kpis_by_type.loc["Producer"]
injector_kpis = kpis_by_type.loc["Injector"]

# oil_rank/injection_rank are computed field-wide (A1/A2), but producers so
# dominate oil (and injectors water injection) that rank 1 in each is always
# the top well of that type - no separate producer/injector-scoped query
# needed.
rank = q.ranking()
top_producer = rank.loc[rank["oil_rank"] == 1].iloc[0]
top_injector = rank.loc[rank["injection_rank"] == 1].iloc[0]
top_producer_share = 100 * top_producer["total_oil"] / producer_kpis["total_oil"]
top_injector_share = 100 * top_injector["total_water_injection"] / injector_kpis["total_water_injection"]

st.markdown("**Producers**  (5 wells)")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Cumulative oil (Sm³)", f"{producer_kpis['total_oil']:,.0f}")
p2.metric("Cumulative gas (Sm³)", f"{producer_kpis['total_gas']:,.0f}")
p3.metric("Cumulative water produced (Sm³)", f"{producer_kpis['total_water']:,.0f}")
p4.metric(
    "Top producer", top_producer["wellbore_name"],
    help=f"{top_producer['total_oil']:,.0f} Sm³ - {top_producer_share:.0f}% of producers' cumulative oil.",
)

st.markdown("**Injectors**  (2 wells)")
i1, i2 = st.columns(2)
i1.metric("Cumulative water injected (Sm³)", f"{injector_kpis['total_water_injection']:,.0f}")
i2.metric(
    "Top injector", top_injector["wellbore_name"],
    help=f"{top_injector['total_water_injection']:,.0f} Sm³ - {top_injector_share:.0f}% of injectors' cumulative water injected.",
)
st.caption(
    f"Also a real residual of {injector_kpis['total_oil']:,.0f} Sm³ oil, "
    f"{injector_kpis['total_gas']:,.0f} Sm³ gas, and {injector_kpis['total_water']:,.0f} Sm³ "
    "water produced - not a data error. 15/9-F-5 has a genuine 129-day "
    "producing period before it became an injector (see Well Comparison's "
    "Injectors tab)."
)
st.caption(
    f"{top_producer['wellbore_name']} alone accounts for {top_producer_share:.0f}% of "
    f"producers' cumulative oil, vs {top_injector['wellbore_name']}'s {top_injector_share:.0f}% "
    "of injectors' cumulative water injected - producers contribute far less "
    "equally than injectors do (hover a \"Top\" metric above for the exact "
    "volume behind each share)."
)
st.caption(f"Source: `{q.VIEW_LIFETIME}`")

field = q.field_monthly()

st.subheader("Active wells")
st.caption(
    "Wells with at least one on-stream day that month  -  engineering question A10, "
    "split into this field's 5 oil producers and 2 water injectors, plus the total. "
    "A well's type here is fixed (its dominant type across its whole recorded "
    "history, same classification as Well Comparison's Producers/Injectors "
    "tabs), not the day-level value, so it doesn't switch category mid-chart."
)
by_type = q.active_wells_by_type()
totals = by_type.groupby("month_start")["active_wells"].sum().reset_index()
fig = go.Figure()
for well_type, color in [("Producer", c.OIL), ("Injector", c.WATER)]:
    series = by_type[by_type["well_type"] == well_type]
    fig.add_trace(go.Scatter(
        x=series["month_start"], y=series["active_wells"], name=well_type,
        mode="lines+markers", line=dict(color=color),
    ))
fig.add_trace(go.Scatter(
    x=totals["month_start"], y=totals["active_wells"], name="Total",
    mode="lines", line=dict(color="#9c9c9c", dash="dash"),
))
fig.update_layout(yaxis_title="Active wells", xaxis_title=None, legend_title_text=None)
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
    go.Scatter(x=field["month_start"], y=field["oil_volume"], name="Oil",
               mode="lines", line=dict(color=c.OIL)),
    secondary_y=False,
)
fig2.add_trace(
    go.Scatter(x=field["month_start"], y=field["water_volume"], name="Water",
               mode="lines", line=dict(color=c.WATER)),
    secondary_y=False,
)
fig2.add_trace(
    go.Scatter(x=field["month_start"], y=field["gas_volume"], name="Gas",
               mode="lines", line=dict(color=c.GAS)),
    secondary_y=True,
)
fig2.update_yaxes(title_text="Oil / Water (Sm³ / month)", secondary_y=False)
fig2.update_yaxes(title_text="Gas (Sm³ / month)", secondary_y=True)
st.plotly_chart(fig2, width="stretch")

st.subheader("Water injection")
st.caption(
    "Oil production overlaid for reference - peak monthly water injection "
    "is only ~2x peak monthly oil (533K vs 277K Sm³), close enough to share "
    "one axis, so trends can be compared directly rather than through two "
    "independently-scaled axes that could make unrelated series look "
    "correlated (or hide a real one)."
)
col_a, col_b = st.columns(2)
with col_a:
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(x=field["month_start"], y=field["water_injection_volume"],
                           name="Water injection", marker_color=c.WATER))
    fig3.add_trace(go.Scatter(x=field["month_start"], y=field["oil_volume"],
                               name="Oil", mode="lines", line=dict(color=c.OIL)))
    fig3.update_layout(title="Monthly", yaxis_title="Sm³ / month", xaxis_title=None)
    st.plotly_chart(fig3, width="stretch")
with col_b:
    field["cumulative_water_injection"] = field["water_injection_volume"].cumsum()
    field["cumulative_oil"] = field["oil_volume"].fillna(0).cumsum()
    fig3b = go.Figure()
    fig3b.add_trace(go.Scatter(x=field["month_start"], y=field["cumulative_water_injection"],
                                name="Water injection", mode="lines", fill="tozeroy",
                                line=dict(color=c.WATER)))
    fig3b.add_trace(go.Scatter(x=field["month_start"], y=field["cumulative_oil"],
                                name="Oil", mode="lines", line=dict(color=c.OIL)))
    fig3b.update_layout(title="Cumulative  -  A7", yaxis_title="Sm³", xaxis_title=None)
    st.plotly_chart(fig3b, width="stretch")

st.subheader("Field production trends")
col_c, col_d = st.columns(2)
with col_c:
    field["water_oil_ratio"] = field["water_volume"] / field["oil_volume"].replace(0, None)
    fig4 = px.line(field, x="month_start", y="water_oil_ratio", color_discrete_sequence=[c.WATER])
    fig4.update_layout(title="Water / oil ratio  -  A6", yaxis_title="Ratio", xaxis_title=None)
    st.plotly_chart(fig4, width="stretch")
with col_d:
    top_months = q.top_field_months(10)
    fig5 = px.bar(top_months, x="month_start", y="oil_volume", color_discrete_sequence=[c.OIL])
    fig5.update_layout(title="Highest-producing months  -  A8", yaxis_title="Sm³ oil", xaxis_title=None)
    st.plotly_chart(fig5, width="stretch")

st.caption(f"Source: `{q.VIEW_FIELD_MONTHLY}`")
