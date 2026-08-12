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

# Fixed per-well color, not reassigned by charting order - keeps a given
# well the same color across every chart on this page, and stable if the
# selection changes, instead of shifting when a well drops out of view.
well_color_map = dict(zip(wells["wellbore_name"], c.WELL_PALETTE))

producers_tab, injectors_tab = st.tabs(["Producers", "Injectors"])

# -----------------------------------------------------------------------
# Producers: 5 of this field's 7 wells. Every section here is oil-centric -
# a real comparison for this group, unlike mixing in the 2 injectors, which
# would just rank them last at everything an oil well does.
# -----------------------------------------------------------------------
with producers_tab:
    producer_wells = wells[wells["well_type"] == "OP"]
    selected_names = st.multiselect(
        "Select wells", producer_wells["wellbore_name"], default=list(producer_wells["wellbore_name"]),
        key="compare_wells_producers",
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
        metric = st.radio("Metric", ["Oil", "Gas", "Water"], horizontal=True, key="producers_superposed_metric")
    with col_scale:
        log_scale = st.checkbox(
            "Log scale", key="producers_superposed_log",
            help="Peak monthly oil ranges ~21x across wells (166K vs 7.9K Sm³) - "
                 "a real difference, not an axis artifact, but it can flatten "
                 "smaller wells' trends on a linear axis. Toggle to compare shapes."
        )
    metric_col = {"Oil": "oil_volume", "Gas": "gas_volume", "Water": "water_volume"}[metric]
    history = q.monthly_production_multi(selected_codes)
    if not history.empty:
        # Fixed categorical palette, not a stream-themed monochrome scale:
        # with up to 5 overlapping lines, telling wells apart by hue matters
        # more here than reinforcing "this is an oil chart" - shades of one
        # color made wells hard to distinguish once more than 2-3 were
        # selected. WELL_PALETTE is a hand-picked set verified distinguishable
        # across all 7 wells at once (Plotly's own default qualitative
        # palette put two near-identical blues and two near-identical
        # greens/teals among them).
        fig0 = px.line(history, x="month_start", y=metric_col, color="wellbore_name",
                        color_discrete_map=well_color_map)
        fig0.update_layout(yaxis_title=f"{metric} (Sm³ / month)", xaxis_title=None, legend_title_text="Well")
        if log_scale:
            fig0.update_yaxes(type="log")
        st.plotly_chart(fig0, width="stretch")
    else:
        st.caption("Select at least one well.")

    st.subheader("Production ranking")
    st.caption(
        "Field-wide rank (all 7 wells), filtered to the selected wells  -  A1, A2. "
        "Click a row to open that well on Well Performance."
    )
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
        rank_event = st.dataframe(
            rank_selected[["wellbore_name", "total_oil", "oil_rank", "total_gas", "gas_rank",
                            "total_water", "water_rank"]].rename(columns={
                "wellbore_name": "Well", "total_oil": "Cumulative oil (Sm³)", "oil_rank": "Oil rank",
                "total_gas": "Cumulative gas (Sm³)", "gas_rank": "Gas rank",
                "total_water": "Cumulative water (Sm³)", "water_rank": "Water rank",
            }),
            width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "Cumulative oil (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
                "Cumulative gas (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
                "Cumulative water (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
            },
        )
        if rank_event.selection.rows:
            selected_well = rank_selected.iloc[rank_event.selection.rows[0]]["wellbore_name"]
            st.session_state["well_performance_select"] = selected_well
            st.switch_page("views/well_performance.py")
    else:
        st.caption("Select at least one well.")

    st.subheader("Normalized production profiles")
    st.caption(
        "Oil production indexed to days since each well's first positive oil day, "
        "as % of that well's own peak daily oil  -  generalizes A3/A4 for shape comparison. "
        "30-day rolling average, not the raw daily series: this chart's job is comparing "
        "decline *shape* across wells, and raw daily values swing enough day-to-day (shut-in "
        "days, choke changes, restart ramp-up) that overlapping raw lines were unreadable. "
        "The exact-date, no-smoothing values this deliberately trades away are on Well "
        "Performance's per-well Production history chart."
    )
    profiles = q.normalized_profiles(selected_codes)
    if not profiles.empty:
        profiles = profiles.sort_values(["wellbore_name", "days_since_first_oil"]).copy()
        profiles["pct_of_peak_smoothed"] = (
            profiles.groupby("wellbore_name")["pct_of_peak"]
            .transform(lambda s: s.rolling(30, min_periods=1).mean())
        )
        fig2 = px.line(profiles, x="days_since_first_oil", y="pct_of_peak_smoothed", color="wellbore_name",
                        color_discrete_map=well_color_map)
        fig2.update_layout(
            yaxis_title="% of peak daily oil (30-day avg)", xaxis_title="Days since first oil",
            legend_title_text="Well",
        )
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
        fig3 = px.line(water, x="month_start", y="water_oil_ratio", color="wellbore_name",
                        color_discrete_map=well_color_map)
        fig3.update_layout(yaxis_title="Water / oil ratio", xaxis_title=None, legend_title_text="Well")
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

# -----------------------------------------------------------------------
# Injectors: the other 2 wells. No normalized-profile / water-oil-ratio /
# decline-from-peak equivalent exists here yet - those are oil-specific
# constructs (A3-A6 all key off oil production) with no injection analogue
# built on this page. What's shown is what's actually comparable: cumulative
# and monthly water injection.
# -----------------------------------------------------------------------
with injectors_tab:
    st.caption(
        "15/9-F-4 never produces oil (0 of 3,327 recorded days); 15/9-F-5 "
        "has a real 129-day oil-producing period before settling as an "
        "injector - visible in the ranking table below, though it isn't "
        "the focus of this tab. Normalized decline shape, water/oil ratio, "
        "and peak-vs-checkpoint comparison (Producers tab) are oil-specific "
        "constructs with no injection equivalent built here yet."
    )
    injector_wells = wells[wells["well_type"] == "WI"]
    selected_names_inj = st.multiselect(
        "Select wells", injector_wells["wellbore_name"], default=list(injector_wells["wellbore_name"]),
        key="compare_wells_injectors",
    )
    selected_codes_inj = wells.loc[
        wells["wellbore_name"].isin(selected_names_inj), "npd_well_bore_code"
    ].astype(int).tolist()

    st.subheader("Water injection history (superposed)")
    st.caption(
        "Actual monthly water injection on real calendar time, selected wells overlaid on "
        "one chart - the injector equivalent of the Producers tab's superposed chart."
    )
    log_scale_inj = st.checkbox("Log scale", key="injectors_superposed_log")
    history_inj = q.monthly_production_multi(selected_codes_inj)
    if not history_inj.empty:
        fig_inj = px.line(history_inj, x="month_start", y="water_injection_volume", color="wellbore_name",
                           color_discrete_map=well_color_map)
        fig_inj.update_layout(
            yaxis_title="Water injection (Sm³ / month)", xaxis_title=None, legend_title_text="Well",
        )
        if log_scale_inj:
            fig_inj.update_yaxes(type="log")
        st.plotly_chart(fig_inj, width="stretch")
    else:
        st.caption("Select at least one well.")

    st.subheader("Injection ranking")
    st.caption(
        "Field-wide rank (all 7 wells), filtered to the selected wells - ranked by "
        "cumulative water injection. Oil/gas/water columns are also shown in the table "
        "for transparency - 15/9-F-5's are real, 15/9-F-4's are blank because it never "
        "produces either. Click a row to open that well on Well Performance."
    )
    rank_inj = q.ranking()
    rank_selected_inj = rank_inj[rank_inj["wellbore_name"].isin(selected_names_inj)]
    if not rank_selected_inj.empty:
        fig_rank_inj = px.bar(
            rank_selected_inj.sort_values("injection_rank"),
            x="wellbore_name", y="total_water_injection", color="wellbore_name",
            color_discrete_sequence=c.shades(c.WATER_SCALE, rank_selected_inj["wellbore_name"].nunique()),
        )
        fig_rank_inj.update_layout(yaxis_title="Cumulative water injection (Sm³)", xaxis_title=None, showlegend=False)
        st.plotly_chart(fig_rank_inj, width="stretch")
        rank_inj_event = st.dataframe(
            rank_selected_inj[["wellbore_name", "total_water_injection", "injection_rank",
                                "total_oil", "oil_rank", "total_gas", "gas_rank",
                                "total_water", "water_rank"]].rename(columns={
                "wellbore_name": "Well",
                "total_water_injection": "Cumulative water injection (Sm³)", "injection_rank": "Injection rank",
                "total_oil": "Cumulative oil (Sm³)", "oil_rank": "Oil rank",
                "total_gas": "Cumulative gas (Sm³)", "gas_rank": "Gas rank",
                "total_water": "Cumulative water (Sm³)", "water_rank": "Water rank",
            }),
            width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "Cumulative water injection (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
                "Cumulative oil (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
                "Cumulative gas (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
                "Cumulative water (Sm³)": st.column_config.NumberColumn(format="%,.0f"),
            },
        )
        if rank_inj_event.selection.rows:
            selected_well = rank_selected_inj.iloc[rank_inj_event.selection.rows[0]]["wellbore_name"]
            st.session_state["well_performance_select"] = selected_well
            st.switch_page("views/well_performance.py")
    else:
        st.caption("Select at least one well.")

st.caption(f"Source: `{q.VIEW_LIFETIME}`, `{q.VIEW_DAILY}`, `{q.VIEW_MONTHLY}`")
