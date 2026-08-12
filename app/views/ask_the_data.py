import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

import nlsql

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
                label = col_name.replace("_", " ").title()
                if isinstance(value, float):
                    col_widget.metric(label, f"{value:,.2f}")
                elif isinstance(value, int):
                    col_widget.metric(label, f"{value:,}")
                else:
                    col_widget.metric(label, str(value))
        else:
            st.dataframe(df, width="stretch", hide_index=True)

        st.subheader("Source")
        views = nlsql.source_views(sql)
        st.write(", ".join(f"`{v}`" for v in views) if views else "(none detected)")

        with st.expander("View SQL"):
            st.code(sql, language="sql")
