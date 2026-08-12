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
st.caption("Whole-field performance, 7 wellbores (5 oil producers, 2 water injectors).")

lifetime = q.field_lifetime_summary()
field = q.field_monthly()
current_active = field.loc[field["active_wells"] > 0].iloc[-1]

k1, k2, k3 = st.columns(3)
k1.metric("Cumulative oil (Sm³)", f"{lifetime['total_oil']:,.0f}")
k2.metric("Peak oil rate (Sm³/d)", f"{lifetime['peak_oil_rate']:,.0f}", help=f"Field-wide daily total, all wells summed, on {lifetime['peak_date']}.")
k3.metric("Peak date", str(lifetime["peak_date"]))

k4, k5, k6 = st.columns(3)
k4.metric("Field life", f"{lifetime['field_life_years']:.1f} years", help=f"{lifetime['first_record_date']} → {lifetime['last_record_date']}")
k5.metric("Water injected (Sm³)", f"{lifetime['total_water_injection']:,.0f}")
k6.metric(
    "Current/late active wells", f"{int(current_active['active_wells'])}",
    help=f"As of {current_active['month_start'].strftime('%Y-%m')}, the last month with any well on-stream - "
         "the field's last 3 recorded months show 0 active wells, a decommissioning tail, not a meaningful "
         "\"current\" figure.",
)
st.caption(
    f"Also {lifetime['total_gas']:,.0f} Sm³ cumulative gas and {lifetime['total_water']:,.0f} Sm³ "
    "cumulative water produced."
)
st.caption(f"Source: `{q.VIEW_LIFETIME}`, `{q.VIEW_DAILY}`")

st.header("Field production history")
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

st.header("Field activity & availability")
st.caption(
    "Active wells counts any well with at least one on-stream day that "
    "month, so a well on-stream for 1 day and one on-stream for 31 both "
    "count the same - field availability (on-stream hours as a % of "
    "possible hours across days with a known state) tells a different, "
    "complementary story: 7 \"active\" wells does not mean 7 reliably "
    "producing wells."
)
ac1, ac2 = st.columns(2)
with ac1:
    by_type = q.active_wells_by_type()
    type_totals = by_type.groupby("month_start")["active_wells"].sum().reset_index()
    fig_active = go.Figure()
    for well_type, color in [("Producer", c.OIL), ("Injector", c.WATER)]:
        series = by_type[by_type["well_type"] == well_type]
        fig_active.add_trace(go.Scatter(
            x=series["month_start"], y=series["active_wells"], name=well_type,
            mode="lines+markers", line=dict(color=color),
        ))
    fig_active.add_trace(go.Scatter(
        x=type_totals["month_start"], y=type_totals["active_wells"], name="Total",
        mode="lines", line=dict(color="#9c9c9c", dash="dash"),
    ))
    fig_active.update_layout(title="Active wells", yaxis_title="Active wells", xaxis_title=None, legend_title_text=None)
    st.plotly_chart(fig_active, width="stretch")
with ac2:
    availability = q.field_availability_monthly()
    fig_avail = px.line(availability, x="month_start", y="availability_pct", color_discrete_sequence=[c.OIL])
    fig_avail.update_layout(title="Field availability", yaxis_title="%", xaxis_title=None)
    st.plotly_chart(fig_avail, width="stretch")

st.header("Well contribution")
st.caption(
    "Each well's cumulative oil as a % of the field's total  -  A1/A2 "
    "generalized, shows at a glance whether production was broadly "
    "distributed or concentrated in a few wells."
)
summary = q.well_summary()
contribution = summary.dropna(subset=["oil_pct_of_field"]).sort_values("oil_pct_of_field")
fig_contrib = px.bar(
    contribution, x="oil_pct_of_field", y="wellbore_name", orientation="h",
    text=contribution["oil_pct_of_field"].map(lambda v: f"{v:.0f}%"),
    color_discrete_sequence=[c.OIL],
)
fig_contrib.update_traces(textposition="outside")
fig_contrib.update_layout(xaxis_title="% of field cumulative oil", yaxis_title=None, showlegend=False)
st.plotly_chart(fig_contrib, width="stretch")
never_produced = summary.loc[summary["oil_pct_of_field"].isna(), "wellbore_name"]
if not never_produced.empty:
    st.caption(f"Not shown, never produces oil: {', '.join(never_produced)}.")

st.header("Production & injection")
st.caption(
    "Oil production overlaid with water injection over time - peak monthly "
    "water injection is only ~2x peak monthly oil (533K vs 277K Sm³), close "
    "enough to share one axis, so trends can be compared directly rather "
    "than through two independently-scaled axes that could make unrelated "
    "series look correlated (or hide a real one). This shows temporal "
    "association only - this dataset alone does not establish reservoir "
    "response or a causal injection-support relationship."
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

st.header("Well summary")
st.caption("Click a row to open that well on Well Performance.")
summary_display = summary.copy()
summary_display["first_oil_date"] = summary_display["first_oil_date"].dt.date
summary_display["peak_date"] = summary_display["peak_date"].dt.date
display_table = summary_display[
    ["wellbore_name", "first_oil_date", "peak_oil", "peak_date", "total_oil", "availability_pct"]
].rename(columns={
    "wellbore_name": "Well", "first_oil_date": "First oil", "peak_oil": "Peak oil (Sm³/d)",
    "peak_date": "Peak date", "total_oil": "Cumulative oil (Sm³)", "availability_pct": "Availability %",
})
event = st.dataframe(
    display_table, width="stretch", hide_index=True,
    on_select="rerun", selection_mode="single-row",
    column_config={"Cumulative oil (Sm³)": st.column_config.NumberColumn(format="%,.0f")},
)
if event.selection.rows:
    selected_well = summary_display.iloc[event.selection.rows[0]]["wellbore_name"]
    st.session_state["well_performance_select"] = selected_well
    st.switch_page("views/well_performance.py")

st.header("Field production trends")
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

st.caption(f"Source: `{q.VIEW_FIELD_MONTHLY}`, `{q.VIEW_DAILY}`, `{q.VIEW_LIFETIME}`")
