import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import streamlit as st

import queries as q

st.title("Well Comparison")

wells = q.list_wells()
selected_names = st.multiselect(
    "Select wells", wells["wellbore_name"], default=list(wells["wellbore_name"])
)
selected_codes = wells.loc[
    wells["wellbore_name"].isin(selected_names), "npd_well_bore_code"
].astype(int).tolist()

st.subheader("Production history (superposed)")
st.caption(
    "Actual monthly production on real calendar time, selected wells overlaid on one "
    "chart - shows raw magnitude and timing together (e.g. one well declining while "
    "another is still ramping up), which the normalized view below deliberately discards."
)
metric = st.radio(
    "Metric", ["Oil", "Gas", "Water"], horizontal=True, key="superposed_metric"
)
metric_col = {"Oil": "oil_volume", "Gas": "gas_volume", "Water": "water_volume"}[metric]
history = q.monthly_production_multi(selected_codes)
if not history.empty:
    fig0 = px.line(history, x="month_start", y=metric_col, color="wellbore_name")
    fig0.update_layout(yaxis_title=f"{metric} (Sm³ / month)", xaxis_title=None)
    st.plotly_chart(fig0, width="stretch")
else:
    st.caption("Select at least one well.")

st.subheader("Production ranking")
st.caption("Field-wide rank (all 7 wells), filtered to the selected wells  -  A1, A2")
rank = q.ranking()
rank_selected = rank[rank["wellbore_name"].isin(selected_names)]
if not rank_selected.empty:
    fig = px.bar(
        rank_selected.sort_values("oil_rank"),
        x="wellbore_name", y="total_oil", color="wellbore_name",
    )
    fig.update_layout(yaxis_title="Cumulative oil (Sm³)", xaxis_title=None, showlegend=False)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(
        rank_selected[["wellbore_name", "total_oil", "oil_rank", "total_gas", "gas_rank",
                        "total_water", "water_rank"]],
        width="stretch", hide_index=True,
    )
else:
    st.caption("Select at least one well.")

st.subheader("Normalized production profiles")
st.caption(
    "Oil production indexed to days since each well's first positive oil day, "
    "as % of that well's own peak daily oil  -  generalizes A3/A4 for shape comparison"
)
profiles = q.normalized_profiles(selected_codes)
if not profiles.empty:
    fig2 = px.line(profiles, x="days_since_first_oil", y="pct_of_peak", color="wellbore_name")
    fig2.update_layout(yaxis_title="% of peak daily oil", xaxis_title="Days since first oil")
    st.plotly_chart(fig2, width="stretch")
else:
    st.caption("No oil-producing history for the selected wells.")

st.subheader("Water trends")
st.caption("Monthly water/oil ratio per well  -  A6 generalized across wells")
water = q.water_trends(selected_codes)
if not water.empty:
    water["month_start"] = water["year"].astype(str) + "-" + water["month"].astype(str).str.zfill(2)
    fig3 = px.line(water, x="month_start", y="water_oil_ratio", color="wellbore_name")
    fig3.update_layout(yaxis_title="Water / oil ratio", xaxis_title=None)
    fig3.update_xaxes(type="category")
    st.plotly_chart(fig3, width="stretch")
else:
    st.caption("No monthly data for the selected wells.")

st.subheader("Production decline")
st.caption("% decline in daily oil, 30 / 90 / 365 days after each well's peak  -  A5")
decl = q.decline(selected_codes)
if not decl.empty:
    st.dataframe(
        decl[["wellbore_name", "peak_date", "peak_volume",
              "pct_decline_30_days", "pct_decline_90_days", "pct_decline_365_days"]],
        width="stretch", hide_index=True,
    )
    decline_long = decl.melt(
        id_vars="wellbore_name",
        value_vars=["pct_decline_30_days", "pct_decline_90_days", "pct_decline_365_days"],
        var_name="window", value_name="pct_decline",
    )
    fig4 = px.bar(decline_long, x="wellbore_name", y="pct_decline", color="window", barmode="group")
    fig4.update_layout(yaxis_title="% decline from peak", xaxis_title=None)
    st.plotly_chart(fig4, width="stretch")
else:
    st.caption("No oil-producing history for the selected wells.")

st.caption(f"Source: `{q.VIEW_LIFETIME}`, `{q.VIEW_DAILY}`, `{q.VIEW_MONTHLY}`")
