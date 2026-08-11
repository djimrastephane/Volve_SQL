# Data

## Obtaining the source file

This project expects the Volve daily/monthly production workbook at:

```
data/raw/Volve production data.xlsx
```

This file is **not included in this repository** — see [`NOTICE`](../NOTICE)
and the README's "Data license and attribution" section for why. Download it
yourself from Equinor's
[Volve data-sharing page](https://www.equinor.com/energy/volve-data-sharing),
under the
[Volve Data Village Terms and Conditions for Use of License to Data](https://cdn.equinor.com/files/h61q9gi9/global/de6532f6134b9a953f6c41bac47a0c055a3712d3.pdf?equinor-hrs-terms-and-conditions-for-licence-to-data-volve.pdf=),
and place it at the path above before running `src/profile_source.py`,
`notebooks/01_source_exploration.ipynb`, `notebooks/02_data_quality.ipynb`,
or `src/load_postgres.py` — all of them read this exact path.

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
