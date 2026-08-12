import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.express as px
import streamlit as st

import queries as q

st.title("Data Quality")
st.caption(
    "Row-level caution list from analytics.vw_data_quality_review "
    "(DQ-001, DQ-003, DQ-004, DQ-005, DQ-006). A row can carry more than "
    "one flag - see notebooks/02_data_quality.ipynb Section 23 for the "
    "full issue register."
)

summary = q.dq_summary()

st.subheader("DQ exceptions")
if not summary.empty:
    fig = px.bar(summary, x="dq_issue", y="record_count")
    fig.update_layout(yaxis_title="Flagged records", xaxis_title=None)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(
        summary.rename(columns={
            "dq_issue": "DQ issue", "record_count": "Records",
            "affected_wells": "Affected wells", "earliest_date": "Earliest date",
            "latest_date": "Latest date",
        }),
        width="stretch", hide_index=True,
    )

st.subheader("Explanation")
for dq_issue, explanation in q.DQ_EXPLANATIONS.items():
    st.markdown(f"**{dq_issue}**  \n{explanation}")

st.subheader("Affected wells and dates")
options = summary["dq_issue"].tolist() if not summary.empty else []
selected_issue = st.selectbox("Inspect a DQ issue", options)
if selected_issue:
    detail = q.dq_detail(selected_issue)
    dc1, dc2 = st.columns(2)
    with dc1:
        by_well = detail.groupby("wellbore_name").size().reset_index(name="record_count")
        fig2 = px.bar(by_well, x="wellbore_name", y="record_count")
        fig2.update_layout(title="Affected wells", yaxis_title="Records", xaxis_title=None)
        st.plotly_chart(fig2, width="stretch")
    with dc2:
        # Grouped by month, not exact date: DQ-001 has 122 distinct dates
        # inside a 4-month span (one bar per day would merge into a solid
        # block, indistinguishable), and DQ-003 has 153 dates scattered
        # across 9+ years (one bar per day would be near-invisible hairlines
        # across a huge range). Monthly bars stay readable at both extremes.
        detail_by_month = detail.copy()
        detail_by_month["month"] = detail_by_month["production_date"].dt.to_period("M").dt.to_timestamp()
        by_date = detail_by_month.groupby("month").size().reset_index(name="record_count")
        fig3 = px.bar(by_date, x="month", y="record_count")
        fig3.update_layout(title="Affected dates (by month)", yaxis_title="Records", xaxis_title=None)
        st.plotly_chart(fig3, width="stretch")
    detail_display = detail.copy()
    detail_display["production_date"] = detail_display["production_date"].dt.date
    st.dataframe(
        detail_display.rename(columns={
            "wellbore_name": "Well", "production_date": "Date", "review_reason": "Reason",
        }),
        width="stretch", hide_index=True,
    )

st.caption(f"Source: `{q.VIEW_DQ}`")
