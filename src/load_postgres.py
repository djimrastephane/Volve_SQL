
"""
load_postgres.py

Loads the Volve Excel workbook into PostgreSQL:

    Excel -> raw (Python)  ->  core (SQL, executed from here)  ->  validate

Idempotent by truncate-and-reload: running this script twice produces the
same database state (no upsert semantics - this is a fixed source snapshot,
not an incremental feed). The whole load runs in a single transaction, so
any failure leaves the database exactly as it was before the run.

Design decisions, made explicitly before writing this file:
  - one script, no classes (load_workbook / load_raw / transform_core /
    validate_load / main)
  - truncate-and-reload, single transaction, fail loudly and roll back
  - raw stays as close to the source as practical: Python only reads Excel
    and inserts, with no renaming beyond the lowercase snake_case columns
    already defined in sql/02_create_tables.sql
  - the raw -> core transformation is written as SQL, executed here, not
    done in pandas - SQL is the primary skill this project demonstrates
  - the monthly stray non-data row (notebooks/02_data_quality.ipynb,
    Section 5) is excluded explicitly and the exclusion is counted and
    reported, not silently dropped

See sql/02_create_tables.sql for the table/constraint definitions this
script loads into, and Section 24 of the data-quality notebook for the
evidence behind every one of them.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.errors
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Overridable so this same script can load a different fixed snapshot -
# e.g. tests/fixtures/generate_sample_workbook.py's tiny synthetic
# workbook - without ever writing into data/raw/, which may hold the real
# licensed workbook on a developer's machine (see data/README.md).
WORKBOOK_PATH = Path(os.environ.get(
    "VOLVE_WORKBOOK_PATH", str(PROJECT_ROOT / "data" / "raw" / "Volve production data.xlsx")
))

DAILY_SHEET = "Daily Production Data"
MONTHLY_SHEET = "Monthly Production Data"

# Other connection parameters (host, port, user, password) follow standard
# libpq environment variables (PGHOST, PGUSER, PGPASSWORD, ...) - the same
# defaults `psql -d volve_analytics` already relies on. Only the database
# name is a project-specific default, overridable via VOLVE_DB_NAME.
DB_NAME = os.environ.get("VOLVE_DB_NAME", "volve_analytics")

# validate_load() checks the loaded row/wellbore counts against exact
# expected values - by design, since this is a fixed source snapshot, not
# an incremental feed (see module docstring). The real snapshot's counts
# are the default, so a normal run against data/raw/Volve production
# data.xlsx is checked exactly as before; overriding both lets this same
# script validate a different fixed snapshot instead - e.g. the tiny
# synthetic workbook in tests/fixtures/, which CI loads to exercise this
# whole pipeline without the licensed real data (see data/README.md).
EXPECTED_DAILY_ROWS = int(os.environ.get("VOLVE_EXPECTED_DAILY_ROWS", "15634"))
EXPECTED_WELLBORE_COUNT = int(os.environ.get("VOLVE_EXPECTED_WELLBORE_COUNT", "7"))
# The documented Section 5 stray row's EXACT signature (notebooks/
# 02_data_quality.ipynb Section 5): every key column blank AND every
# measurement column holding the literal unit-header text a spreadsheet
# author left in row 2 of the source sheet - not just "some key is NULL".
# A monthly source row with a partial key (e.g. Year/Month present but
# NPDCode blank, or vice versa) does NOT match this and is never silently
# folded into "the documented header anomaly" - see transform_core().
MONTHLY_HEADER_SIGNATURE_SQL = """
    wellbore_name IS NULL AND npdcode IS NULL AND year IS NULL AND month IS NULL
    AND on_stream = 'hrs' AND oil = 'Sm3' AND gas = 'Sm3' AND water = 'Sm3'
    AND gi = 'Sm3' AND wi = 'Sm3'
"""
EXPECTED_HEADER_ROWS = int(os.environ.get("VOLVE_EXPECTED_HEADER_ROWS", "1"))

# Arbitrary fixed key for pg_advisory_xact_lock (see _acquire_load_lock) -
# any int works; picked once and never reused for anything else in this
# schema, so a concurrent second load run can never be mistaken for
# unrelated advisory-lock traffic.
LOAD_LOCK_KEY = 385_260_147
LOAD_LOCK_TIMEOUT_S = int(os.environ.get("VOLVE_LOAD_LOCK_TIMEOUT_S", "10"))

# Set by `make load-fixture` only (never by `make load`, which loads the
# real workbook) - see _refuse_unless_fixture_target_is_disposable().
FIXTURE_LOAD = os.environ.get("VOLVE_FIXTURE_LOAD") == "1"
# tests/fixtures/generate_sample_workbook.py's WELL_A/WELL_B codes -
# matches tests/conftest.py's FIXTURE_WELL_A_CODE/FIXTURE_WELL_B_CODE.
# Duplicated here rather than imported (that module lives under tests/,
# not on this script's import path, and pulling in a test-only module
# from production loader code would be the wrong direction of coupling)
# - both call sites document why they must agree.
FIXTURE_WELLBORE_CODES = {90001, 90002}

# Excel column name -> raw column name, positionally paired. Daily sheet
# columns loaded cleanly in the data-quality notebook (Sections 2-3), so
# every column here keeps a type close to its pandas-inferred one.
DAILY_EXCEL_COLUMNS = [
    "DATEPRD", "WELL_BORE_CODE", "NPD_WELL_BORE_CODE", "NPD_WELL_BORE_NAME",
    "NPD_FIELD_CODE", "NPD_FIELD_NAME", "NPD_FACILITY_CODE", "NPD_FACILITY_NAME",
    "ON_STREAM_HRS", "AVG_DOWNHOLE_PRESSURE", "AVG_DOWNHOLE_TEMPERATURE", "AVG_DP_TUBING",
    "AVG_ANNULUS_PRESS", "AVG_CHOKE_SIZE_P", "AVG_CHOKE_UOM", "AVG_WHP_P", "AVG_WHT_P",
    "DP_CHOKE_SIZE", "BORE_OIL_VOL", "BORE_GAS_VOL", "BORE_WAT_VOL", "BORE_WI_VOL",
    "FLOW_KIND", "WELL_TYPE",
]
RAW_DAILY_COLUMNS = [
    "dateprd", "well_bore_code", "npd_well_bore_code", "npd_well_bore_name",
    "npd_field_code", "npd_field_name", "npd_facility_code", "npd_facility_name",
    "on_stream_hrs", "avg_downhole_pressure", "avg_downhole_temperature", "avg_dp_tubing",
    "avg_annulus_press", "avg_choke_size_p", "avg_choke_uom", "avg_whp_p", "avg_wht_p",
    "dp_choke_size", "bore_oil_vol", "bore_gas_vol", "bore_wat_vol", "bore_wi_vol",
    "flow_kind", "well_type",
]

# Monthly sheet: wellbore_name/npdcode/year/month load cleanly. The other
# six columns are handled separately below - Section 5 found a stray
# non-data row that mixes literal unit strings ("hrs", "Sm3") into what
# would otherwise be numeric columns, so raw stores them as text.
MONTHLY_CLEAN_EXCEL_COLUMNS = ["Wellbore name", "NPDCode", "Year", "Month"]
MONTHLY_CLEAN_RAW_COLUMNS = ["wellbore_name", "npdcode", "year", "month"]
MONTHLY_TEXT_EXCEL_COLUMNS = ["On Stream", "Oil", "Gas", "Water", "GI", "WI"]
MONTHLY_TEXT_RAW_COLUMNS = ["on_stream", "oil", "gas", "water", "gi", "wi"]

MONTHLY_EXCEL_COLUMNS = MONTHLY_CLEAN_EXCEL_COLUMNS + MONTHLY_TEXT_EXCEL_COLUMNS
RAW_MONTHLY_COLUMNS = MONTHLY_CLEAN_RAW_COLUMNS + MONTHLY_TEXT_RAW_COLUMNS


class LoadError(Exception):
    """Raised for any failure that should stop the load and roll back."""


# ---------------------------------------------------------------------------
# Value conversion helpers
# ---------------------------------------------------------------------------

def _clean(value):
    """Convert one pandas/numpy scalar to a native Python value for
    psycopg2, mapping any null-like value to None. Used for columns that
    are already unambiguously typed (dates, integers, plain text).
    """
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _monthly_measurement_to_text(value):
    """Convert one cell from a contaminated monthly measurement column to
    the TEXT value raw stores it as. A cell is either NaN (-> None), a
    genuine number read by pandas as float (-> its decimal string), or the
    literal unit string from the stray row (-> unchanged). Preserving the
    stray row's text is the point: raw's job is to not decide it's invalid.
    """
    if pd.isna(value):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return str(float(value))
    return str(value)


def _row_tuples(df: pd.DataFrame, excel_columns: list[str], converters: list) -> list[tuple]:
    """Select excel_columns from df in order and apply the matching
    per-column converter function, returning a list of row tuples ready
    for psycopg2 parameter binding.
    """
    selected = df[excel_columns]
    rows = []
    for row in selected.itertuples(index=False, name=None):
        rows.append(tuple(conv(v) for conv, v in zip(converters, row)))
    return rows


# ---------------------------------------------------------------------------
# load_workbook
# ---------------------------------------------------------------------------

def load_workbook() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the daily and monthly worksheets from the source workbook.

    Fails loudly (LoadError) if the workbook, either worksheet, or any
    required column is missing. This script does not guess or silently
    proceed against a different shape of source data than the one
    notebooks/02_data_quality.ipynb validated.
    """
    if not WORKBOOK_PATH.exists():
        raise LoadError(f"Source workbook not found at {WORKBOOK_PATH}")

    try:
        workbook = pd.ExcelFile(WORKBOOK_PATH)
    except Exception as exc:
        raise LoadError(f"Failed to open workbook: {exc}") from exc

    for sheet in (DAILY_SHEET, MONTHLY_SHEET):
        if sheet not in workbook.sheet_names:
            raise LoadError(
                f"Required worksheet '{sheet}' not found. Worksheets present: {workbook.sheet_names}"
            )

    daily_df = pd.read_excel(workbook, sheet_name=DAILY_SHEET)
    monthly_df = pd.read_excel(workbook, sheet_name=MONTHLY_SHEET)

    missing_daily = [c for c in DAILY_EXCEL_COLUMNS if c not in daily_df.columns]
    if missing_daily:
        raise LoadError(f"Daily worksheet missing required column(s): {missing_daily}")

    missing_monthly = [c for c in MONTHLY_EXCEL_COLUMNS if c not in monthly_df.columns]
    if missing_monthly:
        raise LoadError(f"Monthly worksheet missing required column(s): {missing_monthly}")

    print(f"Workbook loaded: {len(daily_df)} daily rows, {len(monthly_df)} monthly rows")
    return daily_df, monthly_df


# ---------------------------------------------------------------------------
# Fixture-safety guard
# ---------------------------------------------------------------------------

def _refuse_unless_fixture_target_is_disposable(conn, *, enabled: bool = FIXTURE_LOAD) -> None:
    """`make load-fixture` (VOLVE_FIXTURE_LOAD=1) truncates and reloads
    core/raw with a 20-row synthetic stand-in - exactly the operation a
    2026-09-09 security review flagged as able to silently replace a
    populated real-data database's contents if pointed at the wrong one
    (VOLVE_DB_NAME set to the production database name by habit or
    mistake). This is checked BEFORE load_raw()'s TRUNCATE, not after: a
    target is only "positively identified as disposable" if every
    wellbore code already in core.wellbore is one this fixture itself
    would load (or the table is empty) - any other code means this
    database holds data the fixture never put there (most plausibly the
    real 7-wellbore dataset), and the load is refused rather than
    destroying it. Row count alone was deliberately not used as the
    marker: a database name or count can coincidentally match; the
    wellbore identity actually loaded cannot.
    """
    if not enabled:
        return
    with conn.cursor() as cur:
        cur.execute("SELECT npd_well_bore_code FROM core.wellbore")
        existing_codes = {row[0] for row in cur.fetchall()}
    unexpected = existing_codes - FIXTURE_WELLBORE_CODES
    if unexpected:
        raise LoadError(
            f"Refusing to load the fixture: core.wellbore already contains "
            f"wellbore code(s) {sorted(unexpected)} that this fixture did not "
            f"load (expected only {sorted(FIXTURE_WELLBORE_CODES)} or an empty "
            f"table). This looks like a real or otherwise non-fixture dataset - "
            f"not touching it. If this really is a database you want to "
            f"overwrite with the fixture, clear it first with a command you "
            f"trust, not this loader."
        )


# ---------------------------------------------------------------------------
# load_raw
# ---------------------------------------------------------------------------

def load_raw(conn, daily_df: pd.DataFrame, monthly_df: pd.DataFrame) -> None:
    """Load Excel data into the raw schema, truncating first.

    Values are passed through unchanged in meaning (only type-adapted for
    the database driver) - no cleaning, no exclusion. That happens only in
    transform_core().
    """
    daily_converters = [_clean] * len(DAILY_EXCEL_COLUMNS)
    daily_rows = _row_tuples(daily_df, DAILY_EXCEL_COLUMNS, daily_converters)

    monthly_converters = [_clean] * len(MONTHLY_CLEAN_EXCEL_COLUMNS) + [
        _monthly_measurement_to_text
    ] * len(MONTHLY_TEXT_EXCEL_COLUMNS)
    monthly_rows = _row_tuples(monthly_df, MONTHLY_EXCEL_COLUMNS, monthly_converters)

    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE raw.daily_production_source RESTART IDENTITY")
        cur.execute("TRUNCATE TABLE raw.monthly_production_source RESTART IDENTITY")

        execute_values(
            cur,
            f"INSERT INTO raw.daily_production_source ({', '.join(RAW_DAILY_COLUMNS)}) VALUES %s",
            daily_rows,
        )
        execute_values(
            cur,
            f"INSERT INTO raw.monthly_production_source ({', '.join(RAW_MONTHLY_COLUMNS)}) VALUES %s",
            monthly_rows,
        )

    print(f"raw.daily_production_source:   {len(daily_rows)} rows loaded")
    print(f"raw.monthly_production_source: {len(monthly_rows)} rows loaded")


# ---------------------------------------------------------------------------
# transform_core
# ---------------------------------------------------------------------------

def transform_core(conn) -> dict[str, int]:
    """Build core tables from raw via explicit SQL. The transformation
    logic lives here as SQL, not in pandas, on purpose.

    The monthly stray non-data row (Section 5: the one row with NPDCode,
    Year, and Month all NULL) is excluded explicitly by a WHERE clause
    naming exactly that condition - not a broad dropna() - and the
    exclusion is counted and reported below, not silently applied.
    """
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE core.daily_production, core.monthly_reference, core.wellbore RESTART IDENTITY"
        )

        # core.wellbore: Section 6 confirmed NPD_WELL_BORE_CODE <-> name <->
        # well_bore_code is 1:1 in every direction, and Section 8 confirmed
        # field/facility are stable per wellbore - so DISTINCT over all
        # seven columns is expected to yield exactly one row per wellbore.
        # If that assumption were ever violated by a future extract, the
        # primary key on core.wellbore would reject the duplicate and this
        # script would fail loudly rather than silently pick one variant.
        cur.execute("""
            INSERT INTO core.wellbore (
                npd_well_bore_code, npd_well_bore_name, well_bore_code,
                npd_field_code, npd_field_name, npd_facility_code, npd_facility_name
            )
            SELECT DISTINCT
                npd_well_bore_code, npd_well_bore_name, well_bore_code,
                npd_field_code, npd_field_name, npd_facility_code, npd_facility_name
            FROM raw.daily_production_source
            WHERE npd_well_bore_code IS NOT NULL
        """)
        wellbore_count = cur.rowcount

        # core.daily_production: no filtering - Section 4/9 confirmed 0
        # duplicate keys and 0 unparseable dates in this source. If that
        # ever changed, the NOT NULL / PRIMARY KEY constraints on
        # core.daily_production reject the offending rows and this
        # transaction rolls back, rather than the load silently succeeding
        # on a different row count than expected.
        cur.execute("""
            INSERT INTO core.daily_production (
                npd_well_bore_code, production_date, well_type, flow_kind,
                on_stream_hrs, avg_downhole_pressure, avg_downhole_temperature, avg_dp_tubing,
                avg_annulus_press, avg_choke_size_p, avg_choke_uom, avg_whp_p, avg_wht_p,
                dp_choke_size, bore_oil_vol, bore_gas_vol, bore_wat_vol, bore_wi_vol
            )
            SELECT
                npd_well_bore_code, dateprd, well_type, flow_kind,
                on_stream_hrs, avg_downhole_pressure, avg_downhole_temperature, avg_dp_tubing,
                avg_annulus_press, avg_choke_size_p, avg_choke_uom, avg_whp_p, avg_wht_p,
                dp_choke_size, bore_oil_vol, bore_gas_vol, bore_wat_vol, bore_wi_vol
            FROM raw.daily_production_source
        """)
        daily_count = cur.rowcount

        cur.execute("SELECT count(*) FROM raw.monthly_production_source")
        raw_monthly_count = cur.fetchone()[0]

        cur.execute(f"""
            SELECT count(*) FROM raw.monthly_production_source
            WHERE {MONTHLY_HEADER_SIGNATURE_SQL}
        """)
        header_row_count = cur.fetchone()[0]
        if header_row_count != EXPECTED_HEADER_ROWS:
            raise LoadError(
                f"Expected exactly {EXPECTED_HEADER_ROWS} documented units-header row(s) "
                f"(Section 5) in raw.monthly_production_source, found {header_row_count} - "
                "the known anomaly population drifted, refusing to guess at a new load."
            )

        # A row with SOME but not all of npdcode/year/month NULL, that is
        # NOT the documented header row, is neither a valid business row
        # nor the known anomaly - the loader has no basis for silently
        # classifying it as either. Finding 6 of the 2026-09-09 security
        # review: the old check ("any of npdcode/year/month NULL") would
        # have folded a real monthly record missing only its month into
        # "the documented header anomaly" and miscounted it - reject it
        # loudly instead, with enough of its content to look up by hand.
        cur.execute(f"""
            SELECT id, wellbore_name, npdcode, year, month
            FROM raw.monthly_production_source
            WHERE (npdcode IS NULL OR year IS NULL OR month IS NULL)
              AND NOT ({MONTHLY_HEADER_SIGNATURE_SQL})
            LIMIT 20
        """)
        unexpected_partial_key_rows = cur.fetchall()
        if unexpected_partial_key_rows:
            raise LoadError(
                f"{len(unexpected_partial_key_rows)} monthly source row(s) have a partial "
                "key (some but not all of npdcode/year/month NULL) that does not match the "
                "documented units-header signature - refusing to silently classify as that "
                f"anomaly or load them as business rows. Row(s) (raw.id, wellbore_name, "
                f"npdcode, year, month): {unexpected_partial_key_rows}"
            )

        cur.execute("""
            SELECT count(*) FROM raw.monthly_production_source
            WHERE npdcode IS NULL OR year IS NULL OR month IS NULL
        """)
        excluded_monthly_rows = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO core.monthly_reference (
                npd_well_bore_code, reference_year, reference_month,
                on_stream_hrs, oil_vol, gas_vol, water_vol, gas_injection_vol, water_injection_vol
            )
            SELECT
                npdcode, year, month,
                on_stream::numeric, oil::numeric, gas::numeric,
                water::numeric, gi::numeric, wi::numeric
            FROM raw.monthly_production_source
            WHERE npdcode IS NOT NULL AND year IS NOT NULL AND month IS NOT NULL
        """)
        monthly_count = cur.rowcount

    print(f"core.wellbore:          {wellbore_count} rows")
    print(f"core.daily_production:  {daily_count} rows")
    print()
    print(f"Monthly source rows:        {raw_monthly_count}")
    print(f"Rows excluded from core:    {excluded_monthly_rows}")
    print("Reason:                     invalid monthly key (NPDCode/Year/Month NULL) -")
    print("                            documented Section 5 source anomaly (stray units-header row)")
    print(f"core.monthly_reference:     {monthly_count} rows")

    return {
        "wellbore": wellbore_count,
        "daily_production": daily_count,
        "raw_monthly_rows": raw_monthly_count,
        "excluded_monthly_rows": excluded_monthly_rows,
        "monthly_reference": monthly_count,
    }


# ---------------------------------------------------------------------------
# Reconciliation (a generic structural invariant - holds for any valid
# load, fixture or real, not a fixed-snapshot expectation)
# ---------------------------------------------------------------------------

def _daily_monthly_reconciliation_checks(conn) -> list[tuple[str, bool, str]]:
    """Re-expresses sql/04_quality_checks.sql's QC-014/015/016 (same 1e-6
    tolerance, same COALESCE-to-0 rule; see that file's own comment for
    why) but callable INSIDE the load transaction, not only as a separate
    post-commit psql invocation that reports FAIL without failing the job
    (finding 5 of the 2026-09-09 security review). This is a general
    invariant, not a fixed-snapshot expectation - it holds for the real
    15,634-row dataset and for the tiny synthetic fixture equally, which
    is why it is factored out from validate_load()'s EXPECTED_DAILY_ROWS-style
    checks and unconditionally required by both.
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH daily_agg AS (
                SELECT
                    npd_well_bore_code,
                    extract(YEAR FROM production_date)::int AS reference_year,
                    extract(MONTH FROM production_date)::int AS reference_month,
                    sum(bore_oil_vol) AS oil_vol_sum, sum(bore_gas_vol) AS gas_vol_sum,
                    sum(bore_wat_vol) AS water_vol_sum, sum(bore_wi_vol) AS water_injection_vol_sum
                FROM core.daily_production
                GROUP BY npd_well_bore_code, reference_year, reference_month
            ),
            joined AS (
                SELECT
                    d.npd_well_bore_code AS d_code, m.npd_well_bore_code AS m_code,
                    d.oil_vol_sum, m.oil_vol, d.gas_vol_sum, m.gas_vol,
                    d.water_vol_sum, m.water_vol, d.water_injection_vol_sum, m.water_injection_vol
                FROM daily_agg d
                FULL JOIN core.monthly_reference m
                    ON d.npd_well_bore_code = m.npd_well_bore_code
                    AND d.reference_year = m.reference_year
                    AND d.reference_month = m.reference_month
            )
            SELECT
                count(*) FILTER (WHERE m_code IS NULL) AS daily_only_groups,
                count(*) FILTER (WHERE d_code IS NULL) AS monthly_only_groups,
                count(*) FILTER (WHERE
                    abs(coalesce(oil_vol_sum, 0) - coalesce(oil_vol, 0)) > 0.000001
                    OR abs(coalesce(gas_vol_sum, 0) - coalesce(gas_vol, 0)) > 0.000001
                    OR abs(coalesce(water_vol_sum, 0) - coalesce(water_vol, 0)) > 0.000001
                    OR abs(coalesce(water_injection_vol_sum, 0) - coalesce(water_injection_vol, 0)) > 0.000001
                ) AS value_mismatches
            FROM joined
        """)
        daily_only_groups, monthly_only_groups, value_mismatches = cur.fetchone()

    return [
        (
            "Daily/monthly reconciliation: (wellbore, year, month) groups in daily but not monthly_reference",
            daily_only_groups == 0,
            f"{daily_only_groups} group(s)",
        ),
        (
            "Daily/monthly reconciliation: (wellbore, year, month) groups in monthly_reference but not daily",
            monthly_only_groups == 0,
            f"{monthly_only_groups} group(s)",
        ),
        (
            "Daily/monthly reconciliation: oil/gas/water/water-injection sums match (tolerance 0.000001)",
            value_mismatches == 0,
            f"{value_mismatches} mismatched group(s)",
        ),
    ]


# ---------------------------------------------------------------------------
# validate_load
# ---------------------------------------------------------------------------

def validate_load(conn, daily_df: pd.DataFrame, monthly_df: pd.DataFrame, core_counts: dict) -> None:
    """Run every validation check agreed before coding. Raises LoadError on
    the first pass through all checks that finds any failure, so main()
    always rolls back rather than committing a partially-validated load.
    """
    checks: list[tuple[str, bool, str]] = []

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.daily_production_source")
        raw_daily_count = cur.fetchone()[0]
        checks.append((
            "raw daily row count = Excel daily row count",
            raw_daily_count == len(daily_df),
            f"{raw_daily_count} vs {len(daily_df)}",
        ))

        cur.execute("SELECT count(*) FROM core.daily_production")
        core_daily_count = cur.fetchone()[0]
        checks.append((
            f"core daily row count = {EXPECTED_DAILY_ROWS:,}",
            core_daily_count == EXPECTED_DAILY_ROWS,
            f"{core_daily_count}",
        ))

        cur.execute("SELECT count(*) FROM core.wellbore")
        core_wellbore_count = cur.fetchone()[0]
        checks.append((
            f"core wellbore count = {EXPECTED_WELLBORE_COUNT}",
            core_wellbore_count == EXPECTED_WELLBORE_COUNT,
            f"{core_wellbore_count}",
        ))

        # The PRIMARY KEY on core.daily_production already enforces this at
        # insert time (a violation would have raised in transform_core()
        # and rolled back before reaching here). This is a second,
        # independent confirmation, not the only line of defense.
        cur.execute("""
            SELECT count(*) FROM (
                SELECT npd_well_bore_code, production_date
                FROM core.daily_production
                GROUP BY npd_well_bore_code, production_date
                HAVING count(*) > 1
            ) duplicated_keys
        """)
        dup_keys = cur.fetchone()[0]
        checks.append((
            "core daily PK uniqueness holds",
            dup_keys == 0,
            f"{dup_keys} duplicate key(s)",
        ))

        cur.execute("""
            SELECT count(*) FROM core.daily_production dp
            LEFT JOIN core.wellbore w ON dp.npd_well_bore_code = w.npd_well_bore_code
            WHERE w.npd_well_bore_code IS NULL
        """)
        orphaned = cur.fetchone()[0]
        checks.append((
            "core daily FK coverage = 100%",
            orphaned == 0,
            f"{orphaned} orphaned row(s)",
        ))

        cur.execute("SELECT count(*) FROM raw.monthly_production_source")
        raw_monthly_count = cur.fetchone()[0]
        checks.append((
            "raw monthly row count = Excel monthly row count",
            raw_monthly_count == len(monthly_df),
            f"{raw_monthly_count} vs {len(monthly_df)}",
        ))

        expected_monthly_core = core_counts["raw_monthly_rows"] - core_counts["excluded_monthly_rows"]
        checks.append((
            "core monthly row count = raw monthly rows - excluded rows",
            core_counts["monthly_reference"] == expected_monthly_core,
            f"{core_counts['monthly_reference']} vs {expected_monthly_core}",
        ))

        # Sum check: catches accidental type-conversion or filtering
        # mistakes that a row count alone would not. NUMERIC is exact
        # arithmetic in PostgreSQL, so raw and core sums must match exactly
        # if the same values were carried through unchanged - no tolerance
        # needed.
        cur.execute("""
            SELECT sum(bore_oil_vol), sum(bore_gas_vol), sum(bore_wat_vol), sum(bore_wi_vol)
            FROM raw.daily_production_source
        """)
        raw_sums = cur.fetchone()
        cur.execute("""
            SELECT sum(bore_oil_vol), sum(bore_gas_vol), sum(bore_wat_vol), sum(bore_wi_vol)
            FROM core.daily_production
        """)
        core_sums = cur.fetchone()
        checks.append((
            "SUM(oil/gas/water/water-injection): raw = core",
            raw_sums == core_sums,
            f"raw={raw_sums} core={core_sums}",
        ))

    # Outside the `with conn.cursor()` block above (it opens its own), but
    # still inside the same load transaction/connection - a failure here
    # rolls back the same as every other check in this function.
    checks.extend(_daily_monthly_reconciliation_checks(conn))

    print("\nValidation checks:")
    failed = []
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}  ({detail})")
        if not passed:
            failed.append(name)

    if failed:
        raise LoadError(f"Validation failed: {failed}")


# ---------------------------------------------------------------------------
# Load lock and provenance (sql/08_load_provenance.sql)
# ---------------------------------------------------------------------------

def _acquire_load_lock(conn) -> None:
    """A bounded, transaction-scoped advisory lock (pg_advisory_xact_lock,
    automatically released at commit/rollback - never left held by a
    crashed process) serializing concurrent load runs against the same
    database, so two overlapping `make load`/`make load-fixture`
    invocations cannot both TRUNCATE and reload core/raw at once. Bounded
    by SET LOCAL lock_timeout, not an indefinite wait - a second load run
    started while one is already in progress fails fast with a clear
    error (still inside the same transaction, so still rolls back
    cleanly) rather than queuing forever.
    """
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = %s", (f"{LOAD_LOCK_TIMEOUT_S}s",))
        try:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOAD_LOCK_KEY,))
        except psycopg2.errors.LockNotAvailable as exc:
            raise LoadError(
                f"Could not acquire the load lock within {LOAD_LOCK_TIMEOUT_S}s - "
                "another load appears to already be in progress against this database."
            ) from exc


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record_load_provenance(conn, core_counts: dict, workbook_path: Path = WORKBOOK_PATH) -> None:
    """Inserts one row into core.load_runs, in the SAME transaction as
    the load itself - committed together or rolled back together, so a
    row here always corresponds to data that really did become the live
    dataset (see that table's own comment in
    sql/08_load_provenance.sql). Silently skipped, not fatal, if that
    table doesn't exist yet (a database that hasn't applied that
    migration) - provenance is additive, not a load precondition.

    workbook_path defaults to the module-level WORKBOOK_PATH (what
    main() actually loaded) but is an explicit parameter, not an
    implicit read of that global, specifically so a caller (a test, or
    any future script reusing this function) can pass its own path
    instead of depending on process-wide state resolved once at import
    time from an environment variable. A CI failure caught exactly this:
    a test calling this function directly, without going through
    main(), silently fell back to the real (gitignored, CI-absent)
    workbook path and failed with FileNotFoundError - not because the
    function was wrong for its real call site, but because reaching
    into module state instead of taking a parameter made it impossible
    to call correctly from anywhere else.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('core.load_runs')")
        if cur.fetchone()[0] is None:
            print("\n(core.load_runs not present - skipping provenance record; "
                  "apply sql/08_load_provenance.sql to enable it)")
            return
        cur.execute(
            """
            INSERT INTO core.load_runs (
                source_path, source_sha256, daily_rows, monthly_rows, wellbore_count, status
            ) VALUES (%s, %s, %s, %s, %s, 'success')
            RETURNING load_id
            """,
            (
                str(workbook_path), _source_sha256(workbook_path),
                core_counts["daily_production"], core_counts["monthly_reference"],
                core_counts["wellbore"],
            ),
        )
        load_id = cur.fetchone()[0]
    print(f"core.load_runs:         load_id {load_id}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        daily_df, monthly_df = load_workbook()
    except LoadError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        conn = psycopg2.connect(dbname=DB_NAME)
    except psycopg2.OperationalError as exc:
        print(f"FAIL: could not connect to database '{DB_NAME}': {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        with conn:  # commits on clean exit, rolls back on any exception
            _acquire_load_lock(conn)
            _refuse_unless_fixture_target_is_disposable(conn)
            load_raw(conn, daily_df, monthly_df)
            core_counts = transform_core(conn)
            validate_load(conn, daily_df, monthly_df, core_counts)
            record_load_provenance(conn, core_counts)
        print("\nLoad committed. Running this script again will reproduce the same database state.")
    except (LoadError, psycopg2.Error) as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        print("Transaction rolled back - database state is unchanged from before this run.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
