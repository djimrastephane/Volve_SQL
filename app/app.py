"""
app.py

Entry point: streamlit run app/app.py

Connects only to analytics.* views (sql/07_app_role.sql enforces this at
the database layer - see app/db.py). Proves the pipeline end to end:

    Excel -> Python ingestion -> raw -> core -> analytics -> Streamlit -> engineer
"""

import streamlit as st

st.set_page_config(
    page_title="Volve Production Analytics",
    page_icon="\U0001F6E2️",
    layout="wide",
)

field_overview = st.Page(
    "views/field_overview.py", title="Field Overview", icon="\U0001F30D", default=True
)
well_performance = st.Page(
    "views/well_performance.py", title="Well Performance", icon="\U0001F4C8"
)
well_comparison = st.Page(
    "views/well_comparison.py", title="Well Comparison", icon="⚖️"
)
data_quality = st.Page(
    "views/data_quality.py", title="Data Quality", icon="\U0001F50D"
)
ask_the_data = st.Page(
    "views/ask_the_data.py", title="Ask the Data", icon="\U0001F4AC"
)

pg = st.navigation({
    "Dashboard": [field_overview, well_performance, well_comparison, data_quality],
    "Ask the Data": [ask_the_data],
})

with st.sidebar:
    st.markdown("### VOLVE PRODUCTION ANALYTICS")
    st.caption(
        "Reads analytics.\\* views only, via the volve\\_app role "
        "(no access to core or raw)."
    )

pg.run()
