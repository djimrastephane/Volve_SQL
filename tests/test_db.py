"""
test_db.py

app/db.py's pure connection-retry logic - no database needed. Finding 15
of the 2026-09-09 security review: the previous retry logic retried on
ANY psycopg2.Error raised anywhere (including a deterministic SQL error
or a statement_timeout abort, neither of which retrying ever fixes).
_connect_with_retry() is scoped narrowly to the act of establishing a
connection itself - these tests pin that scope down with a fake
connect_fn, not a real database.
"""

from __future__ import annotations

import psycopg2
import pytest

import db


class TestConnectWithRetry:
    @pytest.fixture(autouse=True)
    def _no_sleep_between_retries(self, monkeypatch):
        monkeypatch.setattr(db, "CONNECT_RETRY_DELAY_S", 0)

    def test_returns_result_on_first_success(self):
        calls = []

        def connect_fn():
            calls.append(1)
            return "connection"

        assert db._connect_with_retry(connect_fn) == "connection"
        assert len(calls) == 1

    def test_retries_once_on_operational_error_then_succeeds(self):
        attempts = {"n": 0}

        def connect_fn():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise psycopg2.OperationalError("could not connect")
            return "connection"

        assert db._connect_with_retry(connect_fn) == "connection"
        assert attempts["n"] == 2

    def test_gives_up_after_the_configured_attempt_budget(self):
        attempts = {"n": 0}

        def connect_fn():
            attempts["n"] += 1
            raise psycopg2.OperationalError("still down")

        with pytest.raises(psycopg2.OperationalError):
            db._connect_with_retry(connect_fn)
        assert attempts["n"] == db.CONNECT_RETRY_ATTEMPTS

    def test_does_not_retry_a_non_operational_error(self):
        """A deterministic error (e.g. a programming error) from the
        connect call itself must propagate immediately, not be retried -
        only psycopg2.OperationalError is treated as potentially
        transient."""
        attempts = {"n": 0}

        def connect_fn():
            attempts["n"] += 1
            raise psycopg2.ProgrammingError("bad connection string")

        with pytest.raises(psycopg2.ProgrammingError):
            db._connect_with_retry(connect_fn)
        assert attempts["n"] == 1
