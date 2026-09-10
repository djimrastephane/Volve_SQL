"""
db.py

Connection and query helpers for the Streamlit dashboard. Every query in
this app reads analytics.* views only - the volve_app role
(sql/07_app_role.sql) has no grant on core or raw, so "analytics-schema-only"
is a database-enforced fact, not just an app-level convention. See NOTICE /
README "Data model" for what each view exposes.

Two separate connection paths, not one shared connection:
  - run_query()            fixed, developer-authored dashboard queries
                            (app/queries.py). Pooled (small bounded pool),
                            cached, cache key includes the committed
                            dataset revision (get_dataset_revision()) so a
                            reload can't leave one page showing a mix of
                            old and new values.
  - run_generated_query()  LLM-generated SQL (app/nlsql.py "Ask the
                            Data"). A fresh, disposable connection per
                            call, closed afterwards - never pooled,
                            never cached, concurrency-bounded - so a
                            session-level change, error, or cancellation
                            on one generated query can never reach the
                            next one or the dashboard's own pooled
                            connections. See that function's docstring.

Both paths: readonly session, a short statement_timeout and lock_timeout
set at connection time (not by a later SET, which finding 2 of the
security review showed a hostile SELECT can override on a shared
connection), and a row/byte budget enforced during fetch via a named
(server-side) cursor - checked as rows stream in, not after
pd.read_sql has already built the full DataFrame.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import uuid

import pandas as pd
import psycopg2
import psycopg2.extensions
import psycopg2.pool
import streamlit as st

# Structured, not indiscriminate: every log call below names a request
# category, a duration, and/or a row/byte count - never the SQL text or
# question content itself (that stays in the UI's own "View SQL"
# transparency feature, which the user who asked already sees - this is
# the operator-facing signal, a different audience). Configuring handlers
# is left to whatever runs this app (Streamlit's own logging setup, a
# platform's log collector) - this module only names and uses a logger.
logger = logging.getLogger("volve.db")

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
# Deliberately NOT just PGPASSWORD: this app connects as volve_app, not
# whatever role PGUSER/PGPASSWORD are currently set for (an operator
# running `make setup`/`make docker-set-app-password` as the postgres
# superuser has PGPASSWORD set to THAT role's password - reusing it here
# would silently authenticate as the wrong role's credential, or just
# fail). Falls back to no explicit password (native local trust auth,
# this project's default dev environment - sql/07_app_role.sql's own
# comment) when unset, so nothing changes for that environment.
DB_PASSWORD = os.environ.get("VOLVE_APP_DB_PASSWORD")

_CONNECT_TIMEOUT_S = 5

DASHBOARD_STATEMENT_TIMEOUT_MS = int(os.environ.get("VOLVE_DASHBOARD_STATEMENT_TIMEOUT_MS", "15000"))
DASHBOARD_LOCK_TIMEOUT_MS = int(os.environ.get("VOLVE_DASHBOARD_LOCK_TIMEOUT_MS", "5000"))
DASHBOARD_POOL_MAXCONN = int(os.environ.get("VOLVE_DASHBOARD_POOL_MAXCONN", "5"))
DASHBOARD_MAX_ROWS = int(os.environ.get("VOLVE_DASHBOARD_MAX_ROWS", "200000"))
DASHBOARD_MAX_BYTES = int(os.environ.get("VOLVE_DASHBOARD_MAX_BYTES", str(100 * 1024 * 1024)))
DASHBOARD_CACHE_MAX_ENTRIES = int(os.environ.get("VOLVE_DASHBOARD_CACHE_MAX_ENTRIES", "512"))

GENERATED_STATEMENT_TIMEOUT_MS = int(os.environ.get("VOLVE_ASK_STATEMENT_TIMEOUT_MS", "10000"))
GENERATED_LOCK_TIMEOUT_MS = int(os.environ.get("VOLVE_ASK_LOCK_TIMEOUT_MS", "5000"))
GENERATED_MAX_ROWS = int(os.environ.get("VOLVE_ASK_MAX_ROWS", "5000"))
GENERATED_MAX_BYTES = int(os.environ.get("VOLVE_ASK_MAX_BYTES", str(5 * 1024 * 1024)))
GENERATED_MAX_CONCURRENCY = int(os.environ.get("VOLVE_ASK_MAX_CONCURRENCY", "3"))
GENERATED_ACQUIRE_TIMEOUT_S = float(os.environ.get("VOLVE_ASK_ACQUIRE_TIMEOUT_S", "5"))

CONNECT_RETRY_ATTEMPTS = int(os.environ.get("VOLVE_CONNECT_RETRY_ATTEMPTS", "2"))
CONNECT_RETRY_DELAY_S = float(os.environ.get("VOLVE_CONNECT_RETRY_DELAY_S", "0.5"))


class QueryTooLargeError(RuntimeError):
    """Raised when a result would exceed the row/byte budget before it is
    fully fetched - a normal (if unusual) query failure for callers to
    handle, not an unbounded materialization."""


class TooManyConcurrentQuestionsError(RuntimeError):
    """Raised by run_generated_query() when GENERATED_MAX_CONCURRENCY
    in-flight generated queries are already running and a new one could
    not acquire a slot within GENERATED_ACQUIRE_TIMEOUT_S."""


def _connect_with_retry(connect_fn):
    """Retries only the act of ESTABLISHING a connection (not executing a
    query on one already open) on psycopg2.OperationalError, with a
    small fixed budget - a transient network hiccup while connecting is
    worth one retry. Finding 15 of the 2026-09-09 security review: the
    previous version retried on ANY psycopg2.Error raised anywhere,
    including a query that failed after connecting for a deterministic
    reason (bad SQL, a permission error) or one that hit
    statement_timeout - retrying either of those never helps and just
    repeats the same failure, or the same expensive query, a second
    time. Nothing in this module retries a query itself; only connect_fn
    (psycopg2.connect, or a connection pool's getconn - both of which can
    raise OperationalError from the connection attempt itself) is ever
    passed here.
    """
    last_exc = None
    for attempt in range(CONNECT_RETRY_ATTEMPTS):
        try:
            return connect_fn()
        except psycopg2.OperationalError as exc:
            last_exc = exc
            logger.warning(
                "connect_retry attempt=%d/%d error_category=%s",
                attempt + 1, CONNECT_RETRY_ATTEMPTS, type(exc).__name__,
            )
            if attempt + 1 < CONNECT_RETRY_ATTEMPTS:
                time.sleep(CONNECT_RETRY_DELAY_S)
    raise last_exc


def _connect_kwargs(*, statement_timeout_ms: int, lock_timeout_ms: int) -> dict:
    kwargs = {
        "dbname": DB_NAME,
        "user": DB_USER,
        "connect_timeout": _CONNECT_TIMEOUT_S,
        # Baked into the connection at startup via libpq's `options`
        # rather than a `SET ...` issued after connecting - a `SET` runs
        # as ordinary SQL on the session and is exactly the kind of
        # statement the security review showed a hostile generated query
        # can also issue (SELECT set_config(...)) to weaken it later on a
        # shared/reused connection. Baking it into connection startup
        # means every connection this module ever opens has these limits
        # from its first statement, not as a mutable session setting a
        # later query could plausibly touch (the function allowlist in
        # app/nlsql.py also blocks set_config specifically, but this
        # doesn't rely on that layer being correct either).
        "options": f"-c statement_timeout={statement_timeout_ms} -c lock_timeout={lock_timeout_ms}",
    }
    if DB_HOST:
        kwargs["host"] = DB_HOST
    if DB_PORT:
        kwargs["port"] = DB_PORT
    if DB_PASSWORD:
        kwargs["password"] = DB_PASSWORD
    return kwargs


class _ReadOnlyThreadedConnectionPool(psycopg2.pool.ThreadedConnectionPool):
    """ThreadedConnectionPool sets no session attributes of its own - this
    override makes every physical connection the pool ever creates
    readonly/autocommit-off (needed for the named server-side cursor
    _fetch_bounded() uses) at creation time, so no borrower can forget to
    set it and no borrower can rely on a state some other borrower left
    behind, since pooled connections keep whatever session state they had
    when returned.
    """

    def _connect(self, key=None):
        conn = super()._connect(key)
        conn.set_session(readonly=True, autocommit=False)
        return conn


@st.cache_resource(show_spinner=False)
def _dashboard_pool() -> _ReadOnlyThreadedConnectionPool:
    return _ReadOnlyThreadedConnectionPool(
        1, DASHBOARD_POOL_MAXCONN,
        **_connect_kwargs(
            statement_timeout_ms=DASHBOARD_STATEMENT_TIMEOUT_MS,
            lock_timeout_ms=DASHBOARD_LOCK_TIMEOUT_MS,
        ),
    )


@contextlib.contextmanager
def _dashboard_connection():
    pool = _dashboard_pool()
    conn = _connect_with_retry(pool.getconn)
    healthy = True
    try:
        yield conn
    except psycopg2.Error:
        # Discard rather than return a connection that just raised - a
        # broken/half-open session is not safely reusable by the next
        # borrower. A clean abort (e.g. QueryTooLargeError, which is not
        # a psycopg2.Error) does not hit this branch; that connection is
        # rolled back and returned to the pool normally below.
        healthy = False
        raise
    finally:
        if healthy:
            with contextlib.suppress(psycopg2.Error):
                conn.rollback()  # ends the transaction the named cursor opened
            pool.putconn(conn)
        else:
            pool.putconn(conn, close=True)


def _fetch_bounded(conn, sql: str, params: tuple, *, max_rows: int, max_bytes: int) -> pd.DataFrame:
    """Executes sql on conn via a named (server-side) cursor and streams
    the result in batches, raising QueryTooLargeError the moment either
    budget is exceeded - not after pd.read_sql has already built the full
    DataFrame in memory. conn must be readonly/autocommit=False (named
    cursors require an open transaction); the caller is responsible for
    ending that transaction (rollback/close) once this returns or raises.
    """
    cur_name = f"volve_{uuid.uuid4().hex}"
    with conn.cursor(name=cur_name) as cur:
        cur.itersize = min(max_rows, 1000) or 1000
        cur.execute(sql, params)
        # cur.description is None until the first fetch on a named
        # (server-side) cursor - reading it right after execute(), before
        # any row has actually been fetched, silently returns no columns
        # at all (confirmed against a real server: not documented psycopg2
        # behavior worth assuming). Read it after the fetch loop instead,
        # where it is always populated, including for a zero-row result.
        rows: list[tuple] = []
        total_bytes = 0
        for row in cur:
            rows.append(row)
            total_bytes += sum(len(repr(v)) for v in row)
            if len(rows) > max_rows or total_bytes > max_bytes:
                logger.warning(
                    "query_too_large rows=%d bytes=%d max_rows=%d max_bytes=%d",
                    len(rows), total_bytes, max_rows, max_bytes,
                )
                raise QueryTooLargeError(
                    f"Query result exceeds the {max_rows:,}-row / {max_bytes:,}-byte "
                    "budget for this query type - refused before fully materializing it."
                )
        columns = [d.name for d in cur.description] if cur.description else []
        return pd.DataFrame.from_records(rows, columns=columns)


@st.cache_data(ttl=300, show_spinner=False)
def get_dataset_revision() -> str:
    """The most recently committed load's identifier
    (analytics.vw_load_revision, added by sql/08_load_provenance.sql - a
    view, not a direct core.load_runs grant, so volve_app's "zero grant
    on core" boundary stays literally true), or "unknown" if that view
    doesn't exist yet (a database that hasn't applied that migration) or
    isn't reachable. Folded into run_query()'s cache key (see
    _run_query_cached) so a successful reload invalidates every dashboard
    cache entry at once, rather than leaving some widgets on the old
    dataset until their individual 1-hour TTL happens to expire. Cached
    for 5 minutes itself (not on every call) so checking it isn't another
    round trip on every single dashboard query.
    """
    try:
        with _dashboard_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT load_id::text FROM analytics.vw_load_revision LIMIT 1")
            row = cur.fetchone()
            return row[0] if row and row[0] else "no-load-recorded"
    except psycopg2.Error:
        return "unknown"


@st.cache_data(ttl=3600, show_spinner=False, max_entries=DASHBOARD_CACHE_MAX_ENTRIES)
def _run_query_cached(sql: str, params: tuple, _revision: str) -> pd.DataFrame:
    with _dashboard_connection() as conn:
        return _fetch_bounded(
            conn, sql, params, max_rows=DASHBOARD_MAX_ROWS, max_bytes=DASHBOARD_MAX_BYTES
        )


def run_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Runs one of app/queries.py's fixed, developer-authored queries
    against the pooled dashboard connection. Cache key includes the
    current dataset revision (get_dataset_revision()), not just sql/params,
    so results from before and after a reload are never conflated."""
    return _run_query_cached(sql, params, get_dataset_revision())


# ---------------------------------------------------------------------------
# Generated SQL (app/nlsql.py "Ask the Data")
# ---------------------------------------------------------------------------

_generated_query_slots = threading.BoundedSemaphore(GENERATED_MAX_CONCURRENCY)


def run_generated_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Runs LLM-generated SQL (already validated by app/nlsql._validate_sql)
    on a connection that is opened fresh for this call and closed at the
    end of it - never pooled, never reused, never cached. This is the
    isolation the security review's finding 2 asked for: a session-level
    change, an error, or a client-side cancellation on one generated query
    cannot contaminate the next one, or the dashboard's own pooled
    connections, because there is no shared session or pool entry for it
    to persist on in the first place.

    Concurrency is bounded by GENERATED_MAX_CONCURRENCY (a bounded
    semaphore, not an unbounded thread-per-request pattern) - a request
    that can't get a slot within GENERATED_ACQUIRE_TIMEOUT_S fails with
    TooManyConcurrentQuestionsError rather than queuing indefinitely or
    opening yet another connection anyway.
    """
    acquired = _generated_query_slots.acquire(timeout=GENERATED_ACQUIRE_TIMEOUT_S)
    if not acquired:
        logger.warning("generated_query_concurrency_limit max_concurrency=%d", GENERATED_MAX_CONCURRENCY)
        raise TooManyConcurrentQuestionsError(
            f"{GENERATED_MAX_CONCURRENCY} questions are already being answered - try again shortly."
        )
    t0 = time.monotonic()
    outcome = "error"
    try:
        conn = _connect_with_retry(lambda: psycopg2.connect(**_connect_kwargs(
            statement_timeout_ms=GENERATED_STATEMENT_TIMEOUT_MS,
            lock_timeout_ms=GENERATED_LOCK_TIMEOUT_MS,
        )))
        conn.set_session(readonly=True, autocommit=False)
        try:
            result = _fetch_bounded(
                conn, sql, params, max_rows=GENERATED_MAX_ROWS, max_bytes=GENERATED_MAX_BYTES
            )
            outcome = "success"
            return result
        finally:
            # Not returned to any pool - closed outright, so there is
            # nothing left for a later request to inherit regardless of
            # how this one ended (success, QueryTooLargeError, a
            # statement_timeout abort, or any other psycopg2.Error).
            conn.close()
    finally:
        logger.info(
            "generated_query outcome=%s duration_ms=%d", outcome, int((time.monotonic() - t0) * 1000)
        )
        _generated_query_slots.release()
