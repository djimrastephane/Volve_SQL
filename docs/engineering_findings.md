# Engineering questions and key findings (A1–A12)

Full detail behind the summary table in [`README.md`](../README.md#9-engineering-questions-and-10-key-findings)
Section 9/10. Each question is answered directly in
[`sql/06_analysis.sql`](../sql/06_analysis.sql), built on the `analytics`
views. Figures below are live output from `volve_analytics`.

**A1 — Which wellbores produced the most cumulative oil?**
*SQL:* `RANK() OVER (ORDER BY total_oil DESC NULLS LAST)` on
`vw_well_lifetime_summary`.
*Finding:* 15/9-F-12 leads at 4,579,609.55 Sm³, ahead of 15/9-F-14
(3,942,233.39) and 15/9-F-11 (1,147,849.10). 15/9-F-4 is a pure injector —
`total_oil` is NULL, not zero.
*Interpretation:* `NULLS LAST` is not cosmetic — PostgreSQL's default
`DESC` order sorts NULL first, which silently ranked the injector #1 during
development until caught on reproducibility review. NULL and zero are
different claims (never produced, vs. produced nothing) and the ranking
logic has to respect that distinction explicitly.

**A2 — Which wellbores produced the most gas / water?**
*SQL:* `RANK()`/`DENSE_RANK() ... NULLS LAST`, same view.
*Finding:* Gas leader is 15/9-F-12 (667,542,278.02); water leader is
15/9-F-14 (7,121,249.74) — not the same wellbore that leads oil, showing gas
and water behave differently across the field's wells.

**A3 — When did each wellbore first produce?**
*SQL:* `MIN(production_date) WHERE bore_oil_vol > 0`, grouped per wellbore.
*Finding:* Wellbores entered production in staggered waves rather than all
at field startup — consistent with the field's actual phased development.
*Interpretation:* using `bore_oil_vol > 0` rather than the row's first
recorded date matters — a wellbore's earliest row is often a DQ-001/DQ-003
blank-state record, not its first barrel.

**A4 — What was each wellbore's peak daily oil rate, and when?**
*SQL:* `MAX(bore_oil_vol)` per wellbore, joined back to its date via a
window function.
*Finding:* Peak daily rates vary by more than an order of magnitude across
wellbores, and peak dates cluster in each well's early producing life —
consistent with typical reservoir decline behavior.

**A5 — How did each wellbore's production compare with its own peak at
30/90/365 days after peak?**
*SQL:* `ROW_NUMBER() OVER (PARTITION BY npd_well_bore_code ORDER BY
bore_oil_vol DESC)` to find each well's peak day, then `LEFT JOIN` back to
the exact calendar dates `peak_date + 30/90/365`.
*Finding:* The gap at the +90-day checkpoint is wide — from 12.8% below
peak (15/9-F-12) to 70.0% below peak (15/9-F-1 C) — with no single pattern
across wells.
*Interpretation:* This is a point-in-time comparison to each well's own
peak day, not decline-curve analysis — checkpoints use an exact
calendar-date match, not a smoothed trend, so a single shutdown landing
exactly on a checkpoint can read as a large change unrelated to reservoir
performance. `app/`'s Well Comparison page pairs this with the normalized
production profile (the fuller trajectory) so a surprising checkpoint value
can be checked in context rather than taken at face value.

**A6 — How did field-wide water-oil ratio trend over time?**
*SQL:* `SUM(water)/NULLIF(SUM(oil),0)` on `vw_field_monthly_summary`,
ordered by `month_start`.
*Finding:* Water cut rises through the field's life, the expected signature
of natural water breakthrough/injection support as a waterflood field
matures.

**A7 — How did cumulative water injection trend?**
*SQL:* `SUM() OVER (ORDER BY month_start)` running total on
`vw_field_monthly_summary`.
*Finding:* Injection volume grows through the field's operating life as
more injector wellbores come online and injection intensifies to support
pressure maintenance.

**A8 — What were the field's highest-producing months?**
*SQL:* `RANK() OVER (ORDER BY oil_volume DESC)` on
`vw_field_monthly_summary`.
*Finding:* The top 10 months are all Oct 2008–Jan 2010, led by December
2008 at 276,638.95 Sm³ — the field's early peak, before later decline and
water cut growth take hold.

**A9 — What share of field oil did each wellbore contribute, per year?**
*SQL:* `SUM(oil) OVER (PARTITION BY year)` window total, divided into each
wellbore's yearly sum; verified to sum to exactly 100.0% per year.
*Finding:* Contribution mix shifts materially year over year as wells
mature and new ones start up — no single wellbore dominates every year.

**A10 — How many wells were actively producing/injecting through time?**
*SQL:* `count(DISTINCT npd_well_bore_code) FILTER (WHERE on_stream_hrs > 0)`
per month, on `vw_field_monthly_summary`.
*Finding:* Active well count ramps from 0 up to 7 as the field is developed,
and eventually back toward 0 — a clean visual of the field's full lifecycle
in one series.

**A11 — How often did wells shut down and restart?**
*SQL:* `LAG(is_active) OVER (PARTITION BY npd_well_bore_code ORDER BY
production_date)` compared with `CASE WHEN ... THEN 'shutdown'/'restart'
END`.
*Finding:* Transition counts vary widely by wellbore; 15/9-F-4 has the most
at 124 shutdown/restart events over its recorded life.
*Interpretation:* a high transition count is a legitimate operational
signature (frequent well intervention/testing on an injector), not a data
quality problem — worth distinguishing from the DQ register's genuine
exceptions.
*Extended:* `sql/06_analysis.sql` also reconstructs full downtime episodes
from these transitions ("gaps and islands" — `LAG()` + a running `SUM()`
groups consecutive same-state days into one episode), with offline duration
and oil production immediately before/after each shutdown. Surfaced on the
dashboard's Well Performance page.

**A12 — Does a new well coming online affect field oil rate?**
*SQL:* before/after average `bore_oil_vol` window around each wellbore's
first-production date, joined against `vw_field_monthly_summary`.
*Finding:* Field oil rate rises measurably after new wellbores start up —
e.g. around 15/9-F-4's 2008-04 entry, field average moves from roughly
66,226 to roughly 145,182 Sm³/day before vs. after.
*Interpretation:* a directional, not strictly causal, read — other wells'
own trajectories move over the same window — but the direction is
consistent with what bringing a new producer online should do.
