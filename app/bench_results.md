# Ask the Data - model benchmark

## 2026-09-09 remediation update

Finding 16 of that date's security review: ranking checkers compared only
the winning well's *name*, not its value; the production-history question
(A12) was graded "correct" on any nonempty result; A11's question asked for
a set of wells but its checker only ever validated a single "most
transitions" well; A5 and the old A12 reused sentences verbatim from
`app/nlsql.py`'s own `FEW_SHOT` examples, so the model never had to
generalize to answer them. All fixed in `app/bench_nlsql.py` - see that
file's `check_top_entity`'s `value_key`/`value_hints`, `check_production_history`,
and the reworded `EVAL_SET`. Three held-out cases were added: a paraphrase
of A1 in different wording, a deliberately ambiguous free-form question
(graded "not applicable," never silently counted as a pass), and an
adversarial request for something outside the allowed schema.

Re-run against **the real dataset** (not a fixture, and not the previous
Docker-only sandbox this project was first built in) on this machine, all
5 previously-tested models included - genuine output, not fabricated. One
real bug in the new checker code was found and fixed *during* this
process (see "A checker bug the live run itself caught," below) before
these numbers were captured.

```
Run at:           2026-09-09T23:29:35Z
Prompt hash:       434048e0fcb37e1c
Dataset revision:  1
Model digests:
  qwen2.5-coder:14b  9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849
  qwen3:14b          bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8
  qwen3:8b           500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41
  llama3:latest      365c0bd3c000a25d28ddbf732fe1c6add414de7275464c4e4d1c3b5fcb5d8ad1
  mistral:latest     6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf
```

### Result (new eval set - 15 items: A1-A12 minus A6/A9, reworded A5/A11/A12, plus 3 held-out cases)

| Metric | qwen2.5-coder:14b | qwen3:14b | qwen3:8b | llama3:latest | mistral:latest |
|---|---|---|---|---|---|
| Valid PostgreSQL SQL | 14/15 | 11/15 | 13/15 | 12/15 | 12/15 |
| Correct tables/views | 13/14 | 11/11 | 12/13 | 12/12 | 10/12 |
| Executes successfully | 13/15 | 11/15 | 13/15 | 10/15 | 10/15 |
| Hallucinated columns | 1/15 | 0/15 | 0/15 | 2/15 | 1/15 |
| **Correct result (value-aware, of gradable items)** | **10/12** | **11/11** | **11/12** | **9/9** | **8/10** |
| Respects DQ rules (no NULL trap) | 5/5 | 5/5 | 5/5 | 4/4 | 5/5 |
| Median latency | 1.3s | 34.7s | 15.9s | 1.1s | 4.4s |

"Correct result" denominators differ across models because a model that
fails to produce valid/executable SQL for a question removes that question
from its own gradable set (e.g. qwen3:14b's 11/11 looks perfect but it only
reached 11 of 15 questions at all - 4 hit the 90-second generation
timeout, counted as failures in "Executes successfully," not silently
dropped). Read "Correct result" together with "Executes successfully," not
alone.

This is **not directly comparable** to the "Result" table further down (old
eval set, 12 questions, name-only grading) - different questions
(A5/A11/A12 reworded to stop duplicating `app/nlsql.py` `FEW_SHOT`
sentences verbatim, 3 new held-out cases: a paraphrase, an ambiguous
free-form question, an adversarial request), and a stricter, value-aware
grading scheme. Full per-attempt results (raw SQL, every scored field, run
metadata for all 5 models) saved to `app/bench_results.json`.

### What held up, and what's new

- **A3 ("which well started producing earliest") still fails on all 5
  models**, unchanged from the finding below - every model still uses
  `first_record_date` instead of the first day with `bore_oil_vol > 0`.
- **The adversarial question (HELD3) was correctly refused before
  execution by every single model** - either the model itself declined, or
  its output failed `app/nlsql.py`'s validator. The safety layer held
  end-to-end against a live model, not only in `test_nlsql.py`'s unit
  tests.
- **The ambiguous question (HELD2) was never counted as a false pass** -
  `correct_result` is `None` for it on every model that executed it,
  exactly as designed (see `run_one`'s `free_form` handling).
- **qwen3:14b hit the 90-second generation timeout on 4 of 15 questions**
  (A7, A11, HELD2, HELD3) - a genuine reliability finding about that
  model's latency variance under `stream:false`, not a benchmark bug (the
  error text "Could not reach Ollama" is `requests`' generic message for a
  client-side timeout, not a connection refusal - worth a clearer message
  in `app/nlsql.py` if this recurs). qwen2.5-coder:14b never hit it.
- **DQ4 and A11 hallucinated/missing columns recur across models**
  (qwen2.5-coder, llama3, mistral all failed at least one of these with
  `UndefinedColumn`) - both require a JOIN the few-shot example
  demonstrates but isn't always followed.

### A checker bug the live run itself caught

The first attempt at this re-run crashed outright: `check_top_entity`'s
`value_hints=("transition", "count", "n")` for A11 included the bare
single-character hint `"n"`, which - as a substring match - matched
`llama3`'s own well-*name* column (e.g. `"wellbore_name"` contains `"n"`)
instead of a numeric count column, and `float("15/9-F-4")` raised
`ValueError`, killing the whole run instead of failing that one attempt.
Fixed two ways: removed the bare-letter hints (A11 now uses only
`"transition"`, DQ4 only `"record_count"`), and made the value comparison
itself defensive (a non-numeric match now degrades to a name-only check
and a note, never a crash). Regression-tested in
`tests/test_bench_nlsql.py::TestCheckTopEntityValueRobustness` - it
reproduces this exact failure with a minimal DataFrame. Consistent with
this project's own standard: found via actually running the thing, not
inspection, and fixed with a test that pins the failure down.

The original per-model comparison and its own analysis (12-question eval
set, name-only grading, 2026-08 run) are kept below as the still-accurate
record of that earlier evaluation.

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
