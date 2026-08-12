import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

import nlsql
import queries as q

st.title("Ask the Data")
st.caption(
    f"A local LLM (Ollama, `{nlsql.OLLAMA_MODEL}`) turns your question into SQL "
    "against analytics.\\* views - no data or schema leaves this machine. The "
    "answer shown below is always the literal result of running that SQL, "
    "never an LLM paraphrase of it, and the SQL itself is always visible."
)

examples = [
    "Which well produced the most oil in 2014?",
    "Which wells had the largest production decline?",
    "Show the production history of 15/9-F-1 C.",
]
st.caption("Examples:  " + "   •   ".join(f"_{e}_" for e in examples))

question = st.text_input(
    "Question", placeholder="Which well produced the most oil in 2014?"
)

if st.button("Ask", type="primary") and question:
    sql, df, error = None, None, None
    with st.spinner("Generating SQL and querying the database..."):
        try:
            sql, df = nlsql.ask(question)
        except nlsql.NLSQLError as exc:
            error = str(exc)
            sql = exc.sql
    st.session_state["ask_result"] = {"sql": sql, "df": df, "error": error}

result = st.session_state.get("ask_result")
if result:
    sql, df, error = result["sql"], result["df"], result["error"]

    if error:
        st.error(error)
        if sql:
            with st.expander("View SQL (rejected)"):
                st.code(sql, language="sql")
    else:
        st.subheader("Answer")
        if df.empty:
            st.write("No rows matched this question.")
        elif df.shape[0] == 1 and df.shape[1] <= 3:
            row = df.iloc[0]
            cols = st.columns(len(df.columns))
            for col_widget, col_name in zip(cols, df.columns):
                value = row[col_name]
                # SQL integer/numeric aggregates (e.g. count(*)) come back as
                # numpy.int64/float64, which are NOT instances of Python's
                # int/float - .item() converts to the native Python type so
                # the isinstance checks below actually match.
                value = value.item() if hasattr(value, "item") else value
                label = col_name.replace("_", " ").title()
                if pd.isna(value):
                    col_widget.metric(label, "n/a")
                elif isinstance(value, float):
                    col_widget.metric(label, f"{value:,.2f}")
                elif isinstance(value, int):
                    col_widget.metric(label, f"{value:,}")
                else:
                    col_widget.metric(label, str(value))
        elif "wellbore_name" in df.columns and set(df["wellbore_name"]) <= set(q.list_wells()["wellbore_name"]):
            st.caption("Click a row to open that well on Well Performance.")
            answer_event = st.dataframe(
                df, width="stretch", hide_index=True,
                on_select="rerun", selection_mode="single-row",
            )
            if answer_event.selection.rows:
                selected_well = df.iloc[answer_event.selection.rows[0]]["wellbore_name"]
                st.session_state["well_performance_select"] = selected_well
                st.switch_page("views/well_performance.py")
        else:
            st.dataframe(df, width="stretch", hide_index=True)

        st.subheader("Source")
        views = nlsql.source_views(sql)
        st.write(", ".join(f"`{v}`" for v in views) if views else "(none detected)")

        with st.expander("View SQL"):
            st.code(sql, language="sql")
