"""
db.py

Connection and query helpers for the Streamlit dashboard. Every query in
this app reads analytics.* views only - the volve_app role
(sql/07_app_role.sql) has no grant on core or raw, so "analytics-schema-only"
is a database-enforced fact, not just an app-level convention. See NOTICE /
README "Data model" for what each view exposes.
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg2
import psycopg2.extensions
import streamlit as st

# NUMERIC columns come back from psycopg2 as Decimal by default, which
# plotly/pandas handle awkwardly for charting. This registers a process-wide
# cast to float for display purposes only - it does not touch how anything
# is stored or computed in PostgreSQL (sql/06_analysis.sql and the notebook
# still work in exact NUMERIC arithmetic; this is purely a rendering choice
# for the dashboard).
_DEC2FLOAT = psycopg2.extensions.new_type(
    psycopg2.extensions.DECIMAL.values,
    "DEC2FLOAT",
    lambda value, curs: float(value) if value is not None else None,
)
psycopg2.extensions.register_type(_DEC2FLOAT)

DB_NAME = os.environ.get("VOLVE_DB_NAME", "volve_analytics")
DB_USER = os.environ.get("VOLVE_APP_DB_USER", "volve_app")
DB_HOST = os.environ.get("PGHOST")
DB_PORT = os.environ.get("PGPORT")


@st.cache_resource(show_spinner=False)
def get_connection():
    kwargs = {"dbname": DB_NAME, "user": DB_USER}
    if DB_HOST:
        kwargs["host"] = DB_HOST
    if DB_PORT:
        kwargs["port"] = DB_PORT
    conn = psycopg2.connect(**kwargs)
    conn.set_session(readonly=True, autocommit=True)
    # Defense in depth for Ask the Data (app/nlsql.py): an LLM-generated
    # query that somehow slips past validation still can't run away -
    # every query on this connection is capped, not only NL-generated ones.
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '10s'")
    return conn


@st.cache_data(ttl=3600, show_spinner=False)
def run_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except psycopg2.Error:
        get_connection.clear()
        conn = get_connection()
        return pd.read_sql(sql, conn, params=params)
