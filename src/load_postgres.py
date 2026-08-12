
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

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
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
            load_raw(conn, daily_df, monthly_df)
            core_counts = transform_core(conn)
            validate_load(conn, daily_df, monthly_df, core_counts)
        print("\nLoad committed. Running this script again will reproduce the same database state.")
    except (LoadError, psycopg2.Error) as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        print("Transaction rolled back - database state is unchanged from before this run.", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
