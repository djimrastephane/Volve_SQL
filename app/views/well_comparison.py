import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

import colors as c
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
col_metric, col_scale = st.columns([3, 1])
with col_metric:
    metric = st.radio(
        "Metric", ["Oil", "Gas", "Water"], horizontal=True, key="superposed_metric"
    )
with col_scale:
    log_scale = st.checkbox(
        "Log scale", key="superposed_log",
        help="Peak monthly oil ranges ~21x across wells (166K vs 7.9K Sm³) - "
             "a real difference, not an axis artifact, but it can flatten "
             "smaller wells' trends on a linear axis. Toggle to compare shapes."
    )
metric_col = {"Oil": "oil_volume", "Gas": "gas_volume", "Water": "water_volume"}[metric]
history = q.monthly_production_multi(selected_codes)
if not history.empty:
    # Default categorical palette, not a stream-themed monochrome scale:
    # with up to 7 overlapping lines, telling wells apart by hue matters
    # more here than reinforcing "this is an oil chart" - shades of one
    # color made wells hard to distinguish once more than 2-3 were selected.
    fig0 = px.line(history, x="month_start", y=metric_col, color="wellbore_name")
    fig0.update_layout(yaxis_title=f"{metric} (Sm³ / month)", xaxis_title=None)
    if log_scale:
        fig0.update_yaxes(type="log")
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
        color_discrete_sequence=c.shades(c.OIL_SCALE, rank_selected["wellbore_name"].nunique()),
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
    # Default categorical palette - same reasoning as the superposed chart
    # above, only more so: this is a dense multi-line overlay where a
    # monochrome scale made wells nearly impossible to tell apart.
    fig2 = px.line(profiles, x="days_since_first_oil", y="pct_of_peak", color="wellbore_name")
    fig2.update_layout(yaxis_title="% of peak daily oil", xaxis_title="Days since first oil")
    st.plotly_chart(fig2, width="stretch")
else:
    st.caption("No oil-producing history for the selected wells.")

st.subheader("Water trends")
st.caption("Monthly water/oil ratio per well  -  A6 generalized across wells")
water = q.water_trends(selected_codes)
if not water.empty:
    # Real date axis, not a categorical string month label - the previous
    # version forced type="category" on a "YYYY-MM" string, which put one
    # tick per calendar month (up to ~100+ across 9 years x 7 wells) instead
    # of the automatic year-spaced ticks every other time-series chart in
    # this app gets from a real date column. That was the actual
    # readability problem, not the ratio values themselves (verified live:
    # they genuinely reach ~25-30 late in some wells' lives - real rising
    # water cut, not division-by-near-zero noise).
    fig3 = px.line(water, x="month_start", y="water_oil_ratio", color="wellbore_name")
    fig3.update_layout(yaxis_title="Water / oil ratio", xaxis_title=None)
    st.plotly_chart(fig3, width="stretch")
else:
    st.caption("No monthly data for the selected wells.")

st.subheader("Production change from peak")
st.caption(
    "How did production compare with peak at 30, 90, and 365 days after "
    "peak?  -  A5. This is a point-in-time comparison to each well's own "
    "peak day, not decline-curve analysis: checkpoints use an exact "
    "calendar-date match (peak date + N days), not a smoothed trend, so a "
    "single shutdown landing exactly on a checkpoint can show as a 100% "
    "drop that has nothing to do with reservoir performance. The "
    "normalized profile above shows the fuller trajectory for context "
    "when a checkpoint value looks surprising - hover a bar below for the "
    "exact peak and checkpoint volumes behind each percentage."
)
decl = q.decline(selected_codes)
if not decl.empty:
    st.dataframe(
        decl.rename(columns={
            "wellbore_name": "Well", "peak_date": "Peak date", "peak_volume": "Peak oil (Sm³/d)",
            "oil_30_days_after_peak": "Oil +30d (Sm³/d)", "pct_decline_30_days": "% below peak +30d",
            "oil_90_days_after_peak": "Oil +90d (Sm³/d)", "pct_decline_90_days": "% below peak +90d",
            "oil_365_days_after_peak": "Oil +365d (Sm³/d)", "pct_decline_365_days": "% below peak +365d",
        }),
        width="stretch", hide_index=True,
    )

    checkpoints = [
        ("+30 days", "oil_30_days_after_peak", "pct_decline_30_days"),
        ("+90 days", "oil_90_days_after_peak", "pct_decline_90_days"),
        ("+365 days", "oil_365_days_after_peak", "pct_decline_365_days"),
    ]
    decline_long = pd.DataFrame([
        {
            "wellbore_name": row["wellbore_name"],
            "peak_date": str(row["peak_date"]),
            "peak_volume": row["peak_volume"],
            "checkpoint": label,
            "oil_at_checkpoint": row[oil_col],
            "pct_below_peak": row[pct_col],
        }
        for _, row in decl.iterrows()
        for label, oil_col, pct_col in checkpoints
    ])

    fig4 = px.bar(
        decline_long, x="wellbore_name", y="pct_below_peak", color="checkpoint", barmode="group",
        custom_data=["peak_volume", "peak_date", "checkpoint", "oil_at_checkpoint"],
    )
    fig4.update_traces(
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Peak oil: %{customdata[0]:,.0f} Sm³/d<br>"
            "Peak date: %{customdata[1]}<br>"
            "Checkpoint: %{customdata[2]}<br>"
            "Oil at checkpoint: %{customdata[3]:,.0f} Sm³/d<br>"
            "Change from peak: %{y:.1f}%<extra></extra>"
        )
    )
    fig4.update_layout(yaxis_title="% below peak", xaxis_title=None, legend_title_text="Checkpoint")
    st.plotly_chart(fig4, width="stretch")
else:
    st.caption("No oil-producing history for the selected wells.")

st.caption(f"Source: `{q.VIEW_LIFETIME}`, `{q.VIEW_DAILY}`, `{q.VIEW_MONTHLY}`")
