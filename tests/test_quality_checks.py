"""
test_quality_checks.py

sql/04_quality_checks.sql, run for real via psql (the same way
.github/workflows/ci.yml and REVIEWER_GUIDE.md's "sanity check" do) - not
just individual queries pulled out of it, since the DO block at the end
(which RAISEs on any FAIL row - see that file's own comment, finding 5/13
of the 2026-09-09 security review) and the snapshot_check-based SKIP
downgrade only make sense evaluated as the whole script psql actually
runs. Skipped, like every other DB-dependent test in this suite, if
psql/a connection isn't available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUALITY_CHECKS_SQL = PROJECT_ROOT / "sql" / "04_quality_checks.sql"


def _run_quality_checks(admin_conn) -> subprocess.CompletedProcess:
    if shutil.which("psql") is None:
        pytest.skip("psql not on PATH")
    dsn = admin_conn.get_dsn_parameters()
    cmd = ["psql"]
    if dsn.get("host"):
        cmd += ["-h", dsn["host"]]
    if dsn.get("port"):
        cmd += ["-p", dsn["port"]]
    if dsn.get("user"):
        cmd += ["-U", dsn["user"]]
    cmd += ["-d", dsn["dbname"], "-v", "ON_ERROR_STOP=1", "-f", str(QUALITY_CHECKS_SQL)]
    env = dict(os.environ)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


class TestQualityChecksScript:
    def test_exits_zero_against_the_loaded_fixture(self, admin_conn, loaded_fixture):
        """The fixture is not the real 15,634-row snapshot, so every
        fixed-snapshot check (row counts, known DQ population sizes)
        should report SKIP, not FAIL - and the script overall must still
        exit 0, proving finding 5/13's fix does not turn a legitimate
        non-real dataset into a false CI failure."""
        result = _run_quality_checks(admin_conn)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "overall_status" in result.stdout
        assert "skip_count" in result.stdout

    def test_reports_skip_not_fail_for_fixed_snapshot_checks_on_a_non_real_dataset(self, admin_conn, loaded_fixture):
        result = _run_quality_checks(admin_conn)
        assert " FAIL" not in result.stdout, (
            "a fixed-snapshot check reported FAIL against the fixture instead of SKIP:\n" + result.stdout
        )

    # A positive control (this script actually exits non-zero on a real
    # structural defect, e.g. a corrupted monthly value) is not exercised
    # here: reproducing it needs a COMMITTED corruption for a fresh psql
    # subprocess to see, which would either pollute the shared fixture
    # other tests in this suite depend on or require a second, separately
    # provisioned database this suite does not set up (see conftest.py's
    # module docstring). The underlying reconciliation logic this script
    # shares with src/load_postgres.py's transactional check IS covered
    # that way, in-process, against an uncommitted corruption - see
    # test_load_postgres.py::TestDailyMonthlyReconciliation.
