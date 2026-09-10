"""
test_dashboard_smoke.py

Finding 14 of the 2026-09-09 security review: "Add focused page smoke
tests." Runs every app/views/*.py page through Streamlit's own headless
AppTest harness (streamlit.testing.v1) - the real page script, executed
the same way Streamlit itself executes it, not just its underlying query
functions called directly. This is what actually caught two real crashes
during that review's remediation that calling the query functions in
isolation had not: queries.active_wells_by_type() raising on
pd.date_range(None, None) and well_performance.py indexing an empty
wells frame, both only on a schema-only/no-data database - exactly the
class of bug finding 14 is about.

The empty-database scenario (a schema-only database with no data loaded
at all - the crash-prone case finding 14 named explicitly) is NOT
exercised in this file: app/db.py resolves VOLVE_DB_NAME into module-level
connection constants at IMPORT time (see that file's own DB_PASSWORD
comment), and this process's db/queries modules are imported once and
shared across the whole pytest session (conftest.py's sys.path setup) -
so a test here cannot point a single page render at a different database
than every other test in this session already committed to. It was
instead verified manually, once, standalone
(`VOLVE_DB_NAME=<schema-only db> python -m streamlit.testing...` /
equivalent AppTest script) against a real schema-only database created
for that purpose - see docs/remediation_tracker.md finding 14 for the
evidence (both crashes it caught and the fixes that resolved them:
app/queries.py's active_wells_by_type() and
app/views/well_performance.py's wells-empty guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VIEWS_DIR = PROJECT_ROOT / "app" / "views"

PAGES = [
    "field_overview.py",
    "well_performance.py",
    "well_comparison.py",
    "data_quality.py",
    "ask_the_data.py",
]


def _run_page(page: str, *, timeout: int = 60):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(VIEWS_DIR / page))
    at.run(timeout=timeout)
    return at


@pytest.fixture(scope="module", autouse=True)
def _require_fixture(loaded_fixture):
    return loaded_fixture


class TestDashboardSmokeLoadedFixture:
    """Every page must render with no uncaught exception against a
    normal, loaded database - app.py's own default landing behavior."""

    @pytest.mark.parametrize("page", PAGES)
    def test_page_renders_without_exception(self, page):
        at = _run_page(page)
        assert not list(at.exception), [str(e) for e in at.exception]
