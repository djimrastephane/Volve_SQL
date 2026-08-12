"""
generate_sample_workbook.py

Builds a tiny, structurally faithful synthetic stand-in for the real Volve
source workbook (data/raw/Volve production data.xlsx), which is licensed
and not redistributed (see data/README.md) - so CI has never been able to
exercise src/load_postgres.py or sql/04_quality_checks.sql against real
rows, only against an empty schema (see .github/workflows/ci.yml's
sql-schema-apply job).

This fixture is not a scaled-down copy of the real dataset and does not
try to reproduce its specific known DQ-001..DQ-006 exception populations -
those are documented facts about the real data (see notebooks/
02_data_quality.ipynb), not something a synthetic fixture should fake.
What it does exercise, on purpose, with exactly two wells:

  - well A: a producer (OP/production), 10 daily rows -
      a real recorded zero (on_stream_hrs = 0 with oil/gas/water actually
      recorded as 0 - a shut-in day, not a blank one) and a genuinely
      blank day (every measurement column empty, the same
      blank-cell -> NaN -> NULL path load_postgres.py relies on for the
      real workbook)
  - well B: an injector (WI/injection), 10 daily rows -
      one day with on_stream_hrs = 0 alongside positive bore_wi_vol, the
      same kind of "zero recorded hours, positive injection" discrepancy
      documented for the real data (see queries.py's DQ-006 handling),
      and no pressure/temperature/choke readings at all, matching the
      real field's injectors (Well Performance's "Operating conditions"
      caption)
  - one stray monthly row (NPDCode/Year/Month all blank, unit-string
    literals in the measurement columns) - the same documented Section 5
    anomaly transform_core() explicitly excludes and counts, not a
    generic dropna()

Every value respects the same CHECK constraints as the real schema (all
volumes >= 0, well_type in ('OP','WI'), avg_choke_uom is NULL or '%') so a
normal load succeeds - this fixture proves the pipeline runs end-to-end,
it is not a constraint-violation test.

Usage:
    python tests/fixtures/generate_sample_workbook.py [output_path]

Defaults to data/raw/Volve production data.xlsx (gitignored - never
overwrites a real licensed workbook the developer may have placed there;
run this only in a scratch/CI environment that doesn't have one).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "raw" / "Volve production data.xlsx"

DAILY_SHEET = "Daily Production Data"
MONTHLY_SHEET = "Monthly Production Data"

WELL_A = dict(  # producer
    npd_well_bore_code=90001, npd_well_bore_name="15/9-TEST-A", well_bore_code="TB-A",
    npd_field_code=9000, npd_field_name="TESTFIELD", npd_facility_code=900,
    npd_facility_name="TESTFACILITY",
)
WELL_B = dict(  # injector
    npd_well_bore_code=90002, npd_well_bore_name="15/9-TEST-B", well_bore_code="TB-B",
    npd_field_code=9000, npd_field_name="TESTFIELD", npd_facility_code=900,
    npd_facility_name="TESTFACILITY",
)

# day -> (on_stream_hrs, oil, gas, water) for well A. Day 5 is a real
# recorded zero (shut-in, explicitly 0 - not missing). Day 6 is a
# genuinely blank day (every column empty in the source).
WELL_A_DAILY = {
    1: (24.0, 100.0, 5000.0, 10.0),
    2: (24.0, 105.0, 5100.0, 11.0),
    3: (24.0, 110.0, 5200.0, 12.0),
    4: (24.0, 108.0, 5150.0, 11.0),
    5: (0.0, 0.0, 0.0, 0.0),
    6: (None, None, None, None),
    7: (24.0, 112.0, 5300.0, 13.0),
    8: (24.0, 115.0, 5350.0, 13.0),
    9: (24.0, 118.0, 5400.0, 14.0),
    10: (24.0, 120.0, 5450.0, 14.0),
}
# day -> (on_stream_hrs, bore_wi_vol) for well B. Day 5 has 0 recorded
# hours alongside positive injection - the same kind of discrepancy the
# real DQ-006 population documents, not an error to be cleaned up.
WELL_B_DAILY = {
    1: (24.0, 200.0), 2: (24.0, 210.0), 3: (24.0, 205.0), 4: (24.0, 195.0),
    5: (0.0, 180.0), 6: (24.0, 190.0), 7: (24.0, 200.0), 8: (24.0, 210.0),
    9: (24.0, 205.0), 10: (24.0, 215.0),
}


def _daily_rows() -> pd.DataFrame:
    rows = []
    for day, (hrs, oil, gas, water) in WELL_A_DAILY.items():
        rows.append({
            "DATEPRD": pd.Timestamp(2020, 1, day), **{
                "WELL_BORE_CODE": WELL_A["well_bore_code"],
                "NPD_WELL_BORE_CODE": WELL_A["npd_well_bore_code"],
                "NPD_WELL_BORE_NAME": WELL_A["npd_well_bore_name"],
                "NPD_FIELD_CODE": WELL_A["npd_field_code"],
                "NPD_FIELD_NAME": WELL_A["npd_field_name"],
                "NPD_FACILITY_CODE": WELL_A["npd_facility_code"],
                "NPD_FACILITY_NAME": WELL_A["npd_facility_name"],
            },
            "ON_STREAM_HRS": hrs,
            "AVG_DOWNHOLE_PRESSURE": 200.0 if hrs is not None else None,
            "AVG_DOWNHOLE_TEMPERATURE": 80.0 if hrs is not None else None,
            "AVG_DP_TUBING": 5.0 if hrs is not None else None,
            "AVG_ANNULUS_PRESS": 50.0 if hrs is not None else None,
            "AVG_CHOKE_SIZE_P": 60.0 if hrs is not None else None,
            "AVG_CHOKE_UOM": "%" if hrs is not None else None,
            "AVG_WHP_P": 20.0 if hrs is not None else None,
            "AVG_WHT_P": 40.0 if hrs is not None else None,
            "DP_CHOKE_SIZE": 3.0 if hrs is not None else None,
            "BORE_OIL_VOL": oil,
            "BORE_GAS_VOL": gas,
            "BORE_WAT_VOL": water,
            "BORE_WI_VOL": None,
            "FLOW_KIND": "production",
            "WELL_TYPE": "OP",
        })
    for day, (hrs, wi) in WELL_B_DAILY.items():
        rows.append({
            "DATEPRD": pd.Timestamp(2020, 1, day),
            "WELL_BORE_CODE": WELL_B["well_bore_code"],
            "NPD_WELL_BORE_CODE": WELL_B["npd_well_bore_code"],
            "NPD_WELL_BORE_NAME": WELL_B["npd_well_bore_name"],
            "NPD_FIELD_CODE": WELL_B["npd_field_code"],
            "NPD_FIELD_NAME": WELL_B["npd_field_name"],
            "NPD_FACILITY_CODE": WELL_B["npd_facility_code"],
            "NPD_FACILITY_NAME": WELL_B["npd_facility_name"],
            "ON_STREAM_HRS": hrs,
            "AVG_DOWNHOLE_PRESSURE": None,
            "AVG_DOWNHOLE_TEMPERATURE": None,
            "AVG_DP_TUBING": None,
            "AVG_ANNULUS_PRESS": None,
            "AVG_CHOKE_SIZE_P": None,
            "AVG_CHOKE_UOM": None,
            "AVG_WHP_P": None,
            "AVG_WHT_P": None,
            "DP_CHOKE_SIZE": None,
            "BORE_OIL_VOL": None,
            "BORE_GAS_VOL": None,
            "BORE_WAT_VOL": None,
            "BORE_WI_VOL": wi,
            "FLOW_KIND": "injection",
            "WELL_TYPE": "WI",
        })

    columns = [
        "DATEPRD", "WELL_BORE_CODE", "NPD_WELL_BORE_CODE", "NPD_WELL_BORE_NAME",
        "NPD_FIELD_CODE", "NPD_FIELD_NAME", "NPD_FACILITY_CODE", "NPD_FACILITY_NAME",
        "ON_STREAM_HRS", "AVG_DOWNHOLE_PRESSURE", "AVG_DOWNHOLE_TEMPERATURE", "AVG_DP_TUBING",
        "AVG_ANNULUS_PRESS", "AVG_CHOKE_SIZE_P", "AVG_CHOKE_UOM", "AVG_WHP_P", "AVG_WHT_P",
        "DP_CHOKE_SIZE", "BORE_OIL_VOL", "BORE_GAS_VOL", "BORE_WAT_VOL", "BORE_WI_VOL",
        "FLOW_KIND", "WELL_TYPE",
    ]
    return pd.DataFrame(rows)[columns]


def _monthly_rows() -> pd.DataFrame:
    well_a_oil = sum(v[1] for v in WELL_A_DAILY.values() if v[1] is not None)
    well_a_gas = sum(v[2] for v in WELL_A_DAILY.values() if v[2] is not None)
    well_a_water = sum(v[3] for v in WELL_A_DAILY.values() if v[3] is not None)
    well_a_hrs = sum(v[0] for v in WELL_A_DAILY.values() if v[0] is not None)
    well_b_wi = sum(v[1] for v in WELL_B_DAILY.values())
    well_b_hrs = sum(v[0] for v in WELL_B_DAILY.values())

    rows = [
        {
            "Wellbore name": WELL_A["npd_well_bore_name"], "NPDCode": WELL_A["npd_well_bore_code"],
            "Year": 2020, "Month": 1,
            "On Stream": well_a_hrs, "Oil": well_a_oil, "Gas": well_a_gas, "Water": well_a_water,
            "GI": None, "WI": None,
        },
        {
            "Wellbore name": WELL_B["npd_well_bore_name"], "NPDCode": WELL_B["npd_well_bore_code"],
            "Year": 2020, "Month": 1,
            "On Stream": well_b_hrs, "Oil": None, "Gas": None, "Water": None,
            "GI": None, "WI": well_b_wi,
        },
        # The documented stray non-data row (notebooks/02_data_quality.ipynb
        # Section 5): key columns blank, measurement columns hold literal
        # unit-header text instead of numbers. transform_core() excludes
        # this by name (NPDCode/Year/Month IS NULL), not a broad dropna().
        {
            "Wellbore name": None, "NPDCode": None, "Year": None, "Month": None,
            "On Stream": "hrs", "Oil": "Sm3", "Gas": "Sm3", "Water": "Sm3",
            "GI": "Sm3", "WI": "Sm3",
        },
    ]
    columns = ["Wellbore name", "NPDCode", "Year", "Month", "On Stream", "Oil", "Gas", "Water", "GI", "WI"]
    return pd.DataFrame(rows)[columns]


def generate(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    daily_df = _daily_rows()
    monthly_df = _monthly_rows()
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        daily_df.to_excel(writer, sheet_name=DAILY_SHEET, index=False)
        monthly_df.to_excel(writer, sheet_name=MONTHLY_SHEET, index=False)
    print(f"Wrote {output_path}")
    print(f"  {DAILY_SHEET}: {len(daily_df)} rows ({len(WELL_A_DAILY)} well A + {len(WELL_B_DAILY)} well B)")
    print(f"  {MONTHLY_SHEET}: {len(monthly_df)} rows (2 real + 1 stray)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    generate(out)
