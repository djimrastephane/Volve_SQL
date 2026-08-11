# Ask the Data - model benchmark

`app/bench_nlsql.py` turns the 12 engineering questions in `sql/06_analysis.sql`
into a text-to-SQL evaluation set and runs it against candidate local Ollama
models with an identical prompt (same schema card, few-shot examples, and
system rules from `app/nlsql.py` - the model is the only variable). Ground
truth for every question is computed live from `analytics.*` at the start
of each run, not hardcoded.

The 5 models below were tested because they were already pulled on the
machine this project was built on - this is not a claim that they are the
5 best models available, or that this result generalizes to a different
model lineup. The reusable part is the harness: anyone with different
models available (local or hosted) should run their own candidates through
the same eval set rather than assume this project's winner transfers.

Run: `python app/bench_nlsql.py <model> [model ...]`

## Result

| Metric | qwen2.5-coder:14b | qwen3:14b | qwen3:8b | llama3:latest | mistral:latest |
|---|---|---|---|---|---|
| Valid PostgreSQL SQL | 12/12 | 11/12 | 11/12 | 9/12 | 12/12 |
| Correct tables/views | 11/12 | 10/11 | 10/11 | 8/9 | 10/12 |
| Executes successfully | 11/12 | 11/12 | 11/12 | 8/12 | 10/12 |
| Hallucinated columns | 0/12 | 0/12 | 0/12 | 1/12 | 1/12 |
| Correct result (of attempts that executed) | 9/11 | 10/11 | 10/11 | 7/8 | 8/10 |
| **Correct result (of all 12 asked)** | **9/12** | **10/12** | **10/12** | **7/12** | **8/12** |
| Respects DQ rules (no NULL trap) | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| Median latency | 1.6s | 22.2s | 22.3s | 0.9s | 1.7s |

"Correct result of attempts that executed" is the raw pass rate among
questions the model got far enough to run at all - it flatters a model that
fails early (a smaller denominator). "Correct result of all 12 asked" is the
fair bottom line: every question counts, whether the model produced usable
SQL for it or not.

## Two false negatives caught and fixed before trusting this table

The first run of this benchmark under-reported two models' correctness
because of bugs in the *checker*, not the model:

- **A5** ("largest production decline"): the checker picked the first
  numeric column to rank by (`peak_volume`) instead of the one the question
  actually asks about (`pct_decline_90_days`) - it reported the well with
  the biggest peak as the answer instead of the well with the biggest
  decline. Verified by running the flagged SQL manually.
- **A8** ("highest-producing month"): the checker matched any column
  containing "month" and grabbed the bare `month` integer column instead of
  the `month_start` date column that was actually present.

Both were fixed by preferring semantically-named columns over positional
guessing, and by trusting each query's own `ORDER BY` (row 0) rather than
re-ranking the result. The table above is from the corrected run.

## Findings that held up after verification

- **A3** ("which well started producing earliest") - all five models
  answered incorrectly, and identically: 15/9-F-5 (in fact the *last* well
  to start) instead of 15/9-F-12. Confirmed via a direct query that
  15/9-F-12 is correct. Root cause, from inspecting the generated SQL: every
  model used `vw_well_lifetime_summary.first_record_date` (a wellbore's
  first *recorded* row) instead of the first date with `bore_oil_vol > 0` -
  exactly the trap `sql/06_analysis.sql`'s own A3 comment warns about (a
  wellbore's earliest row can be a DQ-001/DQ-003 blank-state record, not its
  first barrel). A schema card and few-shot examples are not enough on their
  own to prevent this - worth remembering if this eval set grows.
- **A11** (shutdown/restart via `LAG` + `CASE`) - the hardest question in
  the set, and every model failed it, differently: qwen2.5-coder produced
  SQL that parsed but errored at execution (`operator does not exist:
  integer > interval`); qwen3:14b timed out; qwen3:8b and llama3 failed to
  emit a parseable SQL-only response at all.
- Zero hallucinated columns across all three Qwen variants (36 attempts
  total). llama3 and mistral each hallucinated one column reference on a
  question involving `analytics.vw_data_quality_review` - both apparently
  confused it with a different view's columns. At this sample size that is
  a real reliability gap, not noise.

## Decision

`OLLAMA_MODEL` defaults to `qwen2.5-coder:14b` (`app/nlsql.py`) - the best
of the 5 models tested here, not a claim that it is the best model for this
task in general. Among these 5: correctness is statistically tied with
qwen3:14b/8b (9/12 vs 10/12 - one question, a wrong month digit), it is
~14x faster (1.6s vs ~22s median - the difference between a usable
interactive tool and a frustrating wait), and unlike the qwen3 variants it
never failed to produce parseable SQL, even on the question every model got
wrong in a different way (A11). If you have a different set of models
available, `python app/bench_nlsql.py <model> [model ...]` re-runs this
same evaluation against them and `OLLAMA_MODEL` can be pointed at whichever
wins.
