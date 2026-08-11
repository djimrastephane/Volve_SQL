# Data

## Obtaining the source file

This project expects the Volve daily/monthly production workbook at:

```
data/raw/Volve production data.xlsx
```

This file is **not included in this repository** — see [`NOTICE`](../NOTICE)
and the README's "Data license and attribution" section for why. Download it
yourself from the Volve Data Village release and place it at the path above
before running `src/profile_source.py`, `notebooks/01_source_exploration.ipynb`,
`notebooks/02_data_quality.ipynb`, or `src/load_postgres.py` — all of them
read this exact path.

Verify the file you obtained matches the one this project was built and
tested against:

```
md5 -q "data/raw/Volve production data.xlsx"
# expected: a13d7d43ec10fdabc51ad1f27f06664b
```

## `profiling/`

Small CSV outputs from `src/profile_source.py` — column-level summary
statistics (missing-value counts, numeric ranges, identifier checks), not a
copy of the underlying production data. Safe to version-control; committed
here as a record of the profiling phase's output.
