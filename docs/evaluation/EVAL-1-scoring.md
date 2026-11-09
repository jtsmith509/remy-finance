# EVAL-1: Pre-registered evaluation and scoring protocol

- **Status:** **approved by Josh, 2026-11-09. Frozen**: changes only through EVAL-2. The record changes in §6 must be in place before the live run.
- **Date:** 2026-11-09
- **Source:** spec §11, "Tournament protocol" and "Pre-registered metrics and statistical power"; handoff R1.7 (reports); the power estimate in `power-estimate.md`
- **Applies to:** every engine and baseline that writes to the registry, starting with MKT-1 and RAND-1

Once approved, this document is frozen. Any change creates **EVAL-2** with a date, a reason and a link back to this version. Analyses run under EVAL-1 stay labelled EVAL-1, and anything not specified here is labelled **post hoc** wherever it appears.

## 1. Scope and data

1. **Modes are never combined.**
   - Prospective is the primary evidence.
   - Replay is reported separately, labelled "replay", and is never evidence of an edge.
2. **What counts as a prospective record.** Prospective data are all prospective-registry records written after this protocol is approved, **including the 5-day live run** (see question 4).
3. **Unit of analysis.** The unit is one recommendation (one trade ID).
   - **Fill rate** uses every recommendation.
   - **Returns** use filled trades only.
   - An unfilled trade is an outcome, never missing data.
4. **Closed trades only.**
   - Returns are analysed only for trades closed before the analysis cutoff.
   - Censoring rule: an analysis on cutoff date *C* includes only trades with **decision date ≤ C − 61 calendar days**. Under MOVEGEN-1 (60 days maximum) every such trade has closed. This avoids early closes, such as assignments, dominating recent cohorts.
5. **The realized return** is `TradeSummary.realized_return`: realized net P&L divided by the recommendation's capital at risk. It includes every fee, assignment cash flow and dividend owed.
6. **Corrections.** The primary analysis uses values as corrected by `CORRECTION` events. A sensitivity analysis uses the original values. Both are reported.
7. **Exclusions.**
   - None after the fact.
   - Data gaps make trades unfilled, which is already recorded.
   - Bug signals are never selected (DR-0030), and their count is reported.
   - A trade affected by a known data defect stays in. It is annotated, and the result is reported with and without it as a labelled sensitivity analysis.

## 2. Metrics

### 2.1 Forecast quality (strategy-return distributions)
Each recommendation's predicted distribution of net return on capital at risk is scored against the realized return:
- **CRPS**, estimated by the average quantile (pinball) loss over the recorded quantile levels: `2 × mean over levels τ of (1{r < q_τ} − τ)(q_τ − r)`. Lower is better. It's reported as a mean per engine with a cluster-bootstrap interval (§4).
- **Log score**, on a pre-registered categorical binning of returns. Strategy returns have point masses at maximum loss and maximum gain, so a continuous log density isn't defined.
  - The bins: {≤ −0.99}, (−0.99, −0.5], (−0.5, −0.25], (−0.25, 0], (0, 0.25], (0.25, 0.5], (0.5, 1], (1, 2], > 2.
  - Score: `−log p(bin of realized)`, with p floored at 10⁻⁴.
- **PIT histogram.** PIT is the forecast CDF at the realized return, interpolated linearly between recorded quantiles. It's randomised uniformly within any point mass. It's shown in 10 bins, with a χ² test as a descriptive check.
- **Coverage** of the forecast's 5% and 95% quantiles (and 1% and 99% if recorded), meaning the observed exceedance rates. They're tested as in §4.
- **Mean bias:** the mean of (realized − predicted mean).

### 2.2 Underlying-return distributions (spec §11)
- For each fitted expiration, MKT-1's risk-neutral distribution of S_T/F is scored against the realized official close on expiration day.
- **Scores:** CRPS and the log score, which is well defined here because the distribution is continuous. The density at the realized value comes from the recorded quantile grid.
- **What it measures:** calibration of the market-implied density. Risk premia make some miscalibration *expected*, which is informative rather than a defect.

### 2.3 Trade performance
- **Fill rate:** overall, by structure type and by underlying price band, with unfilled reasons (insufficient size, no quote, gap).
- **Realized versus predicted net return on capital at risk:** the mean, the median, and the distribution of the gap.
- **P&L distribution and drawdown.**
  - Each baseline's realized P&L is booked on each trade's close date.
  - The cumulative curve is reported with its maximum drawdown in dollars, and as a fraction of the average capital at risk outstanding.
- **Selection by rank:** the mean realized return for MKT-1's ranks 1–5, and the Spearman correlation between predicted mean and realized return.
- **Counts:** assumed assignments, forced closes, exit retries, and `PIN_RISK_APPROXIMATE` flags.

## 3. Primary hypotheses (confirmatory)
Only these two tests are confirmatory. Every other number is descriptive.

| ID | Null hypothesis | Statistic | Test |
|---|---|---|---|
| **P1** | MKT-1's predicted mean net return is unbiased: E[realized − predicted] = 0 | mean of (realized − predicted) over filled MKT-1 trades | two-sided, cluster bootstrap |
| **P2** | MKT-1's selection adds nothing over random: E[R_MKT-1] − E[R_RAND-1] = 0 | difference of mean realized returns over filled trades | two-sided, cluster bootstrap |

- **Multiplicity:** Holm–Bonferroni across P1 and P2, with family-wise α = 0.05.
- **Expected result:** neither is expected to reject in R1 (see the power estimate). P1 could reject if risk premia are large, for example if short-volatility structures systematically beat the risk-neutral forecast. That would be a finding about the market, not a defect.

## 4. Statistical method
- **Clusters.** Trades are clustered by the **ISO week of their expiration**, because trades that expire the same week share the terminal shock.
- **Intervals and tests.** Confidence intervals and p-values come from a cluster bootstrap that resamples clusters with replacement: 10,000 resamples, a fixed seed derived from `sha256("EVAL-1")`, and percentile intervals.
- **Design effect.** The naive iid standard error is also reported, together with the design effect, `Var_cluster / Var_iid`. This feeds the power update.
- **Paired comparisons** (P2 and later engine tournaments): both arms are restricted to the same decision snapshots, and clusters are shared.
- **Tournament (later engines).**
  - Every engine is compared with MKT-1 and RAND-1 on identical snapshots: (a) on its own recommended portfolio, and (b) on predictions for a common fixed contract set, the spec's two modes.
  - A new engine's own confirmatory hypotheses must be added in an EVAL-n revision *before* its first prospective record.

## 5. Analysis schedule (no optional stopping)
- **Monthly reports** (R1.7) are **descriptive only**, labelled "interim; not a test", and show no p-values for P1 or P2.
- **Confirmatory analyses** run only at pre-registered dates: **12 months** after the first approved prospective record, then every 12 months. Each one uses the censoring rule in §1.4 and is recorded with its code commit and registry chain heads.
- **The 5-day live run** produces a machinery report only: fills, events, chain verification and gaps.

## 6. Prerequisites in the records (changes requested)
The metrics in §2 need more than the five quantiles MKT-1 records today (DR-0030 §6). Once this protocol is approved, these changes go in through a new DR linked to DR-0030, **before** the live run:
1. **More quantile levels** for strategy returns: 1%, 2.5%, 5%, 10%, 20%, …, 90%, 95%, 97.5%, 99% (15 levels).
2. **Categorical bin probabilities** for the §2.1 bins, recorded on the recommendation, as a new optional field of `ReturnDistribution`.
3. **MKT-1's risk-neutral quantiles of S_T/F**, at the same 15 levels plus 0.1% and 99.9%, recorded per fitted expiration in the run log, for §2.2.
4. **Persisted run logs** (`BaselineRun`), as part of the R1.6 scheduler work, so null decisions and bug signals can be audited.

## 7. Integrity
- **Freezing.** The protocol is frozen in git. Its commit hash and approval date go into every report header.
- **Code commits.** Reports print the code commit of every engine whose records they score. Records from a different engine version are reported as separate groups, never silently pooled.
- **Post-hoc analyses** are labelled post hoc wherever they appear: tables, charts and memos.

## 8. Questions for Josh (answered 2026-11-09)
1. **Primary hypotheses:** are P1 (MKT-1 forecast bias) and P2 (MKT-1 selection versus random) the right two confirmatory tests for R1's baselines? Or should either be descriptive only, leaving the confirmatory slots for later engines? **P1 and P2, both confirmatory, with Holm.**
2. **Confirmatory schedule:** 12 months after the first approved prospective record, then yearly. Shorter would mean testing with almost no power (see the power estimate, §3.4). **12 months, then yearly.**
3. **Record changes (§6):** approve 15 quantile levels, categorical bins and the risk-neutral quantiles in the run log, to be added through a new DR before the live run? **Approved: all three, through a new DR linked to DR-0030.**
4. **The 5-day live run:** should its records count as prospective evidence under EVAL-1 (proposed, provided the protocol is approved first), or be treated as a dry run and excluded? **They count as prospective evidence; the clock starts at the first live record.**
5. **Clusters:** is the ISO week of expiration acceptable, or should clusters be wider, for example the calendar month of expiration, which is more conservative? **ISO week of expiration.**
