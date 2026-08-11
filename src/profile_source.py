"""
Phase 1 source-data profiling for the Volve production dataset.

Reads the raw Excel workbook and profiles the "Daily Production Data" and
"Monthly Production Data" worksheets without modifying, cleaning, or loading
anything into a database. Output is a terminal summary plus detailed CSVs
under data/profiling/, used later to inform PostgreSQL schema design.

Run with:
    python src/profile_source.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKBOOK_PATH = PROJECT_ROOT / "data" / "raw" / "Volve production data.xlsx"
PROFILING_DIR = PROJECT_ROOT / "data" / "profiling"

DAILY_SHEET = "Daily Production Data"
MONTHLY_SHEET = "Monthly Production Data"

# Columns from the task brief that should exist on the daily sheet for the
# numeric sanity checks. Some may be absent in a given export; that is
# reported rather than assumed.
NUMERIC_CANDIDATE_COLUMNS = [
    "ON_STREAM_HRS",
    "AVG_DOWNHOLE_PRESSURE",
    "AVG_DOWNHOLE_TEMPERATURE",
    "AVG_DP_TUBING",
    "AVG_ANNULUS_PRESS",
    "AVG_CHOKE_SIZE_P",
    "AVG_WHP_P",
    "AVG_WHT_P",
    "DP_CHOKE_SIZE",
    "BORE_OIL_VOL",
    "BORE_GAS_VOL",
    "BORE_WAT_VOL",
    "BORE_WI_VOL",
]

DUPLICATE_REPORT_COLUMNS = [
    "DATEPRD",
    "NPD_WELL_BORE_CODE",
    "NPD_WELL_BORE_NAME",
    "FLOW_KIND",
    "WELL_TYPE",
    "ON_STREAM_HRS",
    "BORE_OIL_VOL",
    "BORE_GAS_VOL",
    "BORE_WAT_VOL",
    "BORE_WI_VOL",
]


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def require_workbook(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Workbook not found at {path}. Check that the raw Excel file "
            "has been placed under data/raw/ and was not renamed."
        )


def list_worksheets(path: Path) -> list[str]:
    require_workbook(path)
    return pd.ExcelFile(path).sheet_names


def require_sheet(sheet_names: list[str], sheet_name: str) -> None:
    if sheet_name not in sheet_names:
        raise ValueError(
            f"Expected worksheet '{sheet_name}' not found. "
            f"Worksheets present: {sheet_names}"
        )


def require_columns(df: pd.DataFrame, columns: list[str], sheet_name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"Worksheet '{sheet_name}' is missing expected column(s): {missing}. "
            f"Columns present: {list(df.columns)}"
        )


def load_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    sheet_names = list_worksheets(path)
    require_sheet(sheet_names, sheet_name)
    return pd.read_excel(path, sheet_name=sheet_name)


# ---------------------------------------------------------------------------
# Step 2: shape / dtypes / preview
# ---------------------------------------------------------------------------

def report_shape_and_dtypes(df: pd.DataFrame, label: str) -> None:
    print(f"\n--- {label}: shape, columns, dtypes ---")
    print(f"Rows: {len(df)}")
    print(f"Columns: {df.shape[1]}")
    print("\nColumn names:")
    for col in df.columns:
        print(f"  - {col}")
    print("\nInferred dtypes:")
    print(df.dtypes.to_string())
    print("\nFirst 5 rows:")
    print(df.head(5).to_string())


# ---------------------------------------------------------------------------
# Step 3: DATEPRD profiling
# ---------------------------------------------------------------------------

def profile_dateprd(df: pd.DataFrame, date_col: str = "DATEPRD") -> pd.Series:
    """Safely parse date_col and report coverage/invalid/missing counts.

    Returns the parsed datetime series (does not mutate df) so callers can
    reuse it for grain testing without re-parsing.
    """
    require_columns(df, [date_col], "Daily Production Data")

    raw = df[date_col]
    missing_before = raw.isna().sum()

    parsed = pd.to_datetime(raw, errors="coerce")
    # Invalid = became NaT after parsing but was not already missing.
    invalid_count = int((parsed.isna() & raw.notna()).sum())
    missing_count = int(raw.isna().sum())

    print(f"\n--- DATEPRD profiling ---")
    print(f"Earliest production date: {parsed.min()}")
    print(f"Latest production date:   {parsed.max()}")
    print(f"Invalid/unparseable dates: {invalid_count}")
    print(f"Missing dates (originally null): {missing_count}")
    if invalid_count > 0:
        bad_rows = df.loc[parsed.isna() & raw.notna()]
        print(f"WARNING: {invalid_count} unparseable date value(s) found. Sample:")
        print(bad_rows.head(10).to_string())

    return parsed


# ---------------------------------------------------------------------------
# Step 4: wellbore identifier profiling
# ---------------------------------------------------------------------------

def profile_wellbore_identifiers(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["NPD_WELL_BORE_CODE", "NPD_WELL_BORE_NAME", "WELL_BORE_CODE"]
    require_columns(df, cols, "Daily Production Data")

    n_codes = df["NPD_WELL_BORE_CODE"].nunique(dropna=True)
    n_names = df["NPD_WELL_BORE_NAME"].nunique(dropna=True)
    n_well_bore_codes = df["WELL_BORE_CODE"].nunique(dropna=True)

    print("\n--- Wellbore identifier profiling ---")
    print(f"Unique NPD_WELL_BORE_CODE values: {n_codes}")
    print(f"Unique NPD_WELL_BORE_NAME values: {n_names}")
    print(f"Unique WELL_BORE_CODE values: {n_well_bore_codes}")

    # code -> set of names
    code_to_names = df.groupby("NPD_WELL_BORE_CODE")["NPD_WELL_BORE_NAME"].nunique(dropna=True)
    codes_with_multiple_names = code_to_names[code_to_names > 1]

    # name -> set of codes
    name_to_codes = df.groupby("NPD_WELL_BORE_NAME")["NPD_WELL_BORE_CODE"].nunique(dropna=True)
    names_with_multiple_codes = name_to_codes[name_to_codes > 1]

    is_one_to_one = codes_with_multiple_names.empty and names_with_multiple_codes.empty
    print(f"\nEach NPD code maps to exactly one name: {is_one_to_one}")

    if not codes_with_multiple_names.empty:
        print(f"\nINCONSISTENCY: {len(codes_with_multiple_names)} code(s) map to multiple names:")
        for code, n in codes_with_multiple_names.items():
            names = df.loc[df["NPD_WELL_BORE_CODE"] == code, "NPD_WELL_BORE_NAME"].dropna().unique()
            print(f"  code={code!r} -> {n} names: {list(names)}")
    else:
        print("No NPD code maps to multiple names.")

    if not names_with_multiple_codes.empty:
        print(f"\nINCONSISTENCY: {len(names_with_multiple_codes)} name(s) map to multiple codes:")
        for name, n in names_with_multiple_codes.items():
            codes = df.loc[df["NPD_WELL_BORE_NAME"] == name, "NPD_WELL_BORE_CODE"].dropna().unique()
            print(f"  name={name!r} -> {n} codes: {list(codes)}")
    else:
        print("No NPD name maps to multiple codes.")

    check_df = pd.DataFrame(
        {
            "npd_well_bore_code": code_to_names.index,
            "distinct_name_count": code_to_names.values,
        }
    ).merge(
        df[["NPD_WELL_BORE_CODE", "NPD_WELL_BORE_NAME"]]
        .drop_duplicates()
        .rename(columns={"NPD_WELL_BORE_CODE": "npd_well_bore_code"}),
        on="npd_well_bore_code",
        how="left",
    )
    return check_df


# ---------------------------------------------------------------------------
# Step 5: categorical profiling
# ---------------------------------------------------------------------------

def profile_categorical(df: pd.DataFrame, col: str) -> pd.Series:
    require_columns(df, [col], "Daily Production Data")
    counts = df[col].value_counts(dropna=False).sort_values(ascending=False)
    print(f"\n--- {col} value counts (including NULL) ---")
    for value, count in counts.items():
        label = "NULL" if pd.isna(value) else value
        print(f"  {label!r}: {count}")
    return counts


def profile_well_type_stability(df: pd.DataFrame) -> pd.DataFrame:
    """Check whether WELL_TYPE varies over time for the same wellbore."""
    require_columns(df, ["NPD_WELL_BORE_CODE", "WELL_TYPE"], "Daily Production Data")

    distinct_types = df.groupby("NPD_WELL_BORE_CODE")["WELL_TYPE"].nunique(dropna=True)
    unstable = distinct_types[distinct_types > 1]

    print("\n--- WELL_TYPE stability over time per wellbore ---")
    if unstable.empty:
        print("WELL_TYPE is constant per NPD_WELL_BORE_CODE for all wellbores.")
    else:
        print(f"WARNING: {len(unstable)} wellbore(s) have more than one WELL_TYPE value over time:")
        for code, n in unstable.items():
            sub = df.loc[df["NPD_WELL_BORE_CODE"] == code, ["DATEPRD", "WELL_TYPE"]].dropna()
            types_seen = sub["WELL_TYPE"].unique()
            print(f"  code={code!r} -> {n} distinct WELL_TYPE values: {list(types_seen)}")

    return unstable.reset_index().rename(columns={"WELL_TYPE": "distinct_well_type_count"})


# ---------------------------------------------------------------------------
# Step 6: missingness
# ---------------------------------------------------------------------------

def profile_missingness(df: pd.DataFrame) -> pd.DataFrame:
    total_rows = len(df)
    missing_count = df.isna().sum()
    missing_pct = (missing_count / total_rows * 100).round(2)
    non_missing_count = total_rows - missing_count

    report = pd.DataFrame(
        {
            "column": df.columns,
            "total_rows": total_rows,
            "missing_count": missing_count.values,
            "missing_pct": missing_pct.values,
            "non_missing_count": non_missing_count.values,
        }
    ).sort_values("missing_pct", ascending=False).reset_index(drop=True)

    print("\n--- Missing value profile (sorted by missing %) ---")
    print(report.to_string(index=False))
    return report


# ---------------------------------------------------------------------------
# Step 7: grain / duplicate testing
# ---------------------------------------------------------------------------

def profile_grain(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    key_cols = ["NPD_WELL_BORE_CODE", "DATEPRD"]
    require_columns(df, key_cols, "Daily Production Data")

    dup_mask = df.duplicated(subset=key_cols, keep=False)
    dup_rows = df.loc[dup_mask].sort_values(key_cols)

    dup_group_sizes = df.groupby(key_cols).size()
    duplicated_combinations = dup_group_sizes[dup_group_sizes > 1]

    print("\n--- Candidate grain: NPD_WELL_BORE_CODE + DATEPRD ---")
    print(f"Duplicated combinations: {len(duplicated_combinations)}")
    print(f"Rows involved in duplicates: {len(dup_rows)}")

    available_report_cols = [c for c in DUPLICATE_REPORT_COLUMNS if c in df.columns]
    dup_report = dup_rows[available_report_cols]

    if not dup_rows.empty:
        print("\nDuplicate rows:")
        print(dup_report.to_string(index=False))
    else:
        print("No duplicate NPD_WELL_BORE_CODE + DATEPRD combinations found.")

    grain_summary = pd.DataFrame(
        {
            "duplicated_combinations": [len(duplicated_combinations)],
            "rows_involved_in_duplicates": [len(dup_rows)],
            "grain_is_unique": [len(duplicated_combinations) == 0],
        }
    )
    return dup_report, grain_summary


# ---------------------------------------------------------------------------
# Step 8: numeric sanity checks
# ---------------------------------------------------------------------------

def profile_numeric_columns(df: pd.DataFrame, candidate_columns: list[str]) -> pd.DataFrame:
    available = [c for c in candidate_columns if c in df.columns]
    missing_from_sheet = [c for c in candidate_columns if c not in df.columns]

    print("\n--- Numeric sanity checks ---")
    if missing_from_sheet:
        print(f"Note: the following expected numeric columns are not present in this sheet: {missing_from_sheet}")

    rows = []
    for col in available:
        series = pd.to_numeric(df[col], errors="coerce")
        rows.append(
            {
                "column": col,
                "count": int(series.notna().sum()),
                "missing_count": int(series.isna().sum()),
                "min": series.min(),
                "median": series.median(),
                "mean": series.mean(),
                "max": series.max(),
                "zero_count": int((series == 0).sum()),
                "negative_count": int((series < 0).sum()),
            }
        )

    report = pd.DataFrame(rows)
    print(report.to_string(index=False))
    return report


# ---------------------------------------------------------------------------
# Step 9: monthly sheet profiling
# ---------------------------------------------------------------------------

def profile_monthly_sheet(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    key_cols = ["NPDCode", "Year", "Month"]
    require_columns(df, key_cols, MONTHLY_SHEET)

    print(f"\n--- {MONTHLY_SHEET} profiling ---")
    print(f"Rows: {len(df)}")
    print(f"Columns: {df.shape[1]}")
    print("Column names:")
    for col in df.columns:
        print(f"  - {col}")

    key_all_null_mask = df[key_cols].isna().all(axis=1)
    if key_all_null_mask.any():
        print(
            f"\nWARNING: {int(key_all_null_mask.sum())} row(s) have all of "
            f"{key_cols} null. This typically indicates a stray header/units "
            "row embedded as data, which also skews dtype inference on "
            "numeric-looking columns (they get read as text instead of "
            "numbers). Sample:"
        )
        print(df.loc[key_all_null_mask].to_string())

    object_dtype_cols = [c for c in df.columns if c not in key_cols and df[c].dtype == object]
    if object_dtype_cols:
        print(
            f"\nNote: column(s) {object_dtype_cols} loaded as text (object) "
            "dtype rather than numeric, likely due to the row(s) flagged above."
        )

    n_wellbores = df["NPDCode"].nunique(dropna=True)
    print(f"\nUnique wellbore count (NPDCode): {n_wellbores}")

    earliest = df[["Year", "Month"]].dropna().sort_values(["Year", "Month"]).iloc[0]
    latest = df[["Year", "Month"]].dropna().sort_values(["Year", "Month"]).iloc[-1]
    print(f"Earliest year/month: {int(earliest['Year'])}-{int(earliest['Month']):02d}")
    print(f"Latest year/month:   {int(latest['Year'])}-{int(latest['Month']):02d}")

    dup_group_sizes = df.groupby(key_cols).size()
    duplicated_combinations = dup_group_sizes[dup_group_sizes > 1]
    dup_mask = df.duplicated(subset=key_cols, keep=False)
    dup_rows = df.loc[dup_mask].sort_values(key_cols)
    print(f"\nDuplicate NPDCode + Year + Month combinations: {len(duplicated_combinations)}")
    print(f"Rows involved: {len(dup_rows)}")
    if not dup_rows.empty:
        print(dup_rows.to_string(index=False))

    missing_report = profile_missingness(df)

    summary = pd.DataFrame(
        {
            "rows": [len(df)],
            "columns": [df.shape[1]],
            "unique_wellbores": [n_wellbores],
            "earliest_year_month": [f"{int(earliest['Year'])}-{int(earliest['Month']):02d}"],
            "latest_year_month": [f"{int(latest['Year'])}-{int(latest['Month']):02d}"],
            "duplicated_combinations": [len(duplicated_combinations)],
            "rows_involved_in_duplicates": [len(dup_rows)],
            "rows_with_all_key_columns_null": [int(key_all_null_mask.sum())],
        }
    )
    return summary, missing_report


# ---------------------------------------------------------------------------
# Step 10: terminal summary
# ---------------------------------------------------------------------------

def print_terminal_summary(
    daily_df: pd.DataFrame,
    dateprd_parsed: pd.Series,
    well_type_counts: pd.Series,
    flow_kind_counts: pd.Series,
    grain_summary: pd.DataFrame,
    missing_report_daily: pd.DataFrame,
    monthly_summary: pd.DataFrame,
) -> None:
    print("\n" + "=" * 70)
    print("PHASE 1 PROFILING SUMMARY")
    print("=" * 70)

    print("\nDATASET")
    print("Daily Production Data")

    print("\nSHAPE")
    print(f"Rows: {len(daily_df)}")
    print(f"Columns: {daily_df.shape[1]}")

    print("\nDATE COVERAGE")
    print(f"Earliest: {dateprd_parsed.min()}")
    print(f"Latest: {dateprd_parsed.max()}")

    print("\nWELLBORES")
    print(f"Unique NPD codes: {daily_df['NPD_WELL_BORE_CODE'].nunique(dropna=True)}")
    print(f"Unique NPD names: {daily_df['NPD_WELL_BORE_NAME'].nunique(dropna=True)}")

    print("\nWELL TYPES")
    for value, count in well_type_counts.items():
        label = "NULL" if pd.isna(value) else value
        print(f"  {label!r}: {count}")

    print("\nFLOW KINDS")
    for value, count in flow_kind_counts.items():
        label = "NULL" if pd.isna(value) else value
        print(f"  {label!r}: {count}")

    print("\nCANDIDATE GRAIN")
    print("NPD_WELL_BORE_CODE + DATEPRD")
    grain_is_unique = bool(grain_summary["grain_is_unique"].iloc[0])
    print(f"Unique: {'Yes' if grain_is_unique else 'No'}")
    print(f"Duplicate combinations: {grain_summary['duplicated_combinations'].iloc[0]}")
    print(f"Rows involved: {grain_summary['rows_involved_in_duplicates'].iloc[0]}")

    print("\nMISSINGNESS")
    print("Top columns by missing percentage:")
    top_missing = missing_report_daily.head(10)
    for _, row in top_missing.iterrows():
        print(f"  {row['column']}: {row['missing_pct']}% ({int(row['missing_count'])} missing)")

    print("\nMONTHLY DATA")
    print(f"Rows: {monthly_summary['rows'].iloc[0]}")
    print(f"Wellbores: {monthly_summary['unique_wellbores'].iloc[0]}")
    print(f"Date range: {monthly_summary['earliest_year_month'].iloc[0]} to {monthly_summary['latest_year_month'].iloc[0]}")
    print(f"Duplicate well/month combinations: {monthly_summary['duplicated_combinations'].iloc[0]}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Output saving
# ---------------------------------------------------------------------------

def save_outputs(
    output_dir: Path,
    missing_values_daily: pd.DataFrame,
    numeric_profile_daily: pd.DataFrame,
    duplicate_wellbore_dates: pd.DataFrame,
    wellbore_identifier_check: pd.DataFrame,
    monthly_profile: pd.DataFrame,
    missing_values_monthly: pd.DataFrame,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    missing_values_daily.to_csv(output_dir / "missing_values_daily.csv", index=False)
    numeric_profile_daily.to_csv(output_dir / "numeric_profile_daily.csv", index=False)
    duplicate_wellbore_dates.to_csv(output_dir / "duplicate_wellbore_dates.csv", index=False)
    wellbore_identifier_check.to_csv(output_dir / "wellbore_identifier_check.csv", index=False)
    monthly_profile.to_csv(output_dir / "monthly_profile.csv", index=False)
    missing_values_monthly.to_csv(output_dir / "missing_values_monthly.csv", index=False)

    print(f"\nProfiling outputs saved to: {output_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    require_workbook(WORKBOOK_PATH)

    # Step 1: worksheet names
    sheet_names = list_worksheets(WORKBOOK_PATH)
    print("--- Worksheets found ---")
    for name in sheet_names:
        print(f"  - {name}")

    # Step 2: load daily sheet, report shape/dtypes/preview
    daily_df = load_sheet(WORKBOOK_PATH, DAILY_SHEET)
    report_shape_and_dtypes(daily_df, DAILY_SHEET)

    # Step 3: DATEPRD profiling. Overwrite in-memory copy only (source file untouched).
    dateprd_parsed = profile_dateprd(daily_df)
    daily_df = daily_df.copy()
    daily_df["DATEPRD"] = dateprd_parsed

    # Step 4: wellbore identifiers
    wellbore_identifier_check = profile_wellbore_identifiers(daily_df)

    # Step 5: categorical profiling
    well_type_counts = profile_categorical(daily_df, "WELL_TYPE")
    flow_kind_counts = profile_categorical(daily_df, "FLOW_KIND")
    profile_well_type_stability(daily_df)

    # Step 6: missingness
    missing_values_daily = profile_missingness(daily_df)

    # Step 7: grain / duplicates
    duplicate_wellbore_dates, grain_summary = profile_grain(daily_df)

    # Step 8: numeric sanity checks
    numeric_profile_daily = profile_numeric_columns(daily_df, NUMERIC_CANDIDATE_COLUMNS)

    # Step 9: monthly sheet
    monthly_df = load_sheet(WORKBOOK_PATH, MONTHLY_SHEET)
    monthly_summary, missing_values_monthly = profile_monthly_sheet(monthly_df)

    # Step 10: terminal summary
    print_terminal_summary(
        daily_df,
        dateprd_parsed,
        well_type_counts,
        flow_kind_counts,
        grain_summary,
        missing_values_daily,
        monthly_summary,
    )

    # Step 11: save outputs
    save_outputs(
        PROFILING_DIR,
        missing_values_daily,
        numeric_profile_daily,
        duplicate_wellbore_dates,
        wellbore_identifier_check,
        monthly_summary,
        missing_values_monthly,
    )


if __name__ == "__main__":
    main()
