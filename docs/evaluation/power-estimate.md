# Power estimate for prospective paper trading (spec §11)

- **Status:** approved by Josh, 2026-11-09 (base case with the sensitivity ranges; replay-month update before the live run)
- **Date:** 2026-11-09
- **Version:** EVAL-1 (goes with `EVAL-1-scoring.md`)
- **Reproduce:** `uv run python scripts/power_estimate.py`. The formulas are in `src/remy_finance/reports/power.py`, with tests.

## 1. The question
Spec §11: *"Before prospective paper trading begins, a recorded power estimate states how many effectively independent observations are expected to accumulate per month, given overlapping trades and shared shocks, and how long detecting an edge of a stated size would take."*

## 2. Model and assumptions
The model is kept simple on purpose, so each assumption can be challenged and replaced with a measured value.

| Quantity | Base value | Source / reasoning |
|---|---|---|
| Recommendations per baseline per trading day | 10 | SELECT-1: K = 5 per snapshot, 2 snapshots, at most one per underlying per day; the 50-name universe rarely binds. |
| Trading days per month | 21 | |
| Fill rate | 70% | Unknown until we have data. FILL-1 needs the full displayed size for up to 10 contracts per leg, at both legs' quotes. Range shown: 50–90%. |
| Filled trades per month per baseline | ≈ 147 | 10 × 21 × 0.7 |
| σ, the standard deviation of a trade's net return on capital at risk | 0.7 | Defined-risk structures range from −1 (maximum loss) to several times capital at risk. MKT-1's predicted 5–95% ranges on the synthetic chains are about −0.55 to +0.55, and real outcomes are fatter-tailed. Range shown: 0.4–1.0. |
| ρ, the pairwise correlation of returns within a monthly cohort | 0.10 | Trades are held to expiration, 7–60 days and about a month on average, so a month's trades overlap and share market and volatility shocks. Mixed directions (calls, puts, condors) dilute the shared exposure; a baseline that systematically picks one structure type concentrates it. Range shown: 0.05–0.30. |
| Tests | one-sided, α = 0.05, power 80% | Primary tests in EVAL-1 are two-sided, at α = 0.05 with Holm; one-sided is the optimistic planning case. |

**Effective observations.**
- A month's cohort of *n* trades with pairwise correlation ρ counts as `n_eff = n / (1 + (n − 1) ρ)` (Kish's design effect).
- Successive monthly cohorts are treated as independent: holding periods are about a month, and the market's monthly moves are close to independent.
- Both simplifications favour detection. In reality, correlation decays with time apart rather than being uniform within a month, and volatility regimes persist across months.

## 3. Results

### 3.1 Effective observations per month (per baseline)

| fill rate | ρ = 0.05 | ρ = 0.10 | ρ = 0.20 | ρ = 0.30 |
|---|---|---|---|---|
| 50% (105 filled) | 17 | 9.2 | 4.8 | 3.3 |
| 70% (147 filled) | 18 | 9.4 | 4.9 | 3.3 |
| 90% (189 filled) | 18 | 9.5 | 4.9 | 3.3 |

**The main result.**
- With this many trades, the effective count per month saturates near **1/ρ**. Shared shocks, not the trade count, set the information rate.
- About 150 filled trades a month are worth roughly **9 independent observations**, about **110 a year**.
- Issuing more recommendations would add almost nothing. Lower correlation would add a lot, through different expirations, structure directions and sectors.

### 3.2 Time to detect a mean net return of δ on capital at risk (one baseline, σ = 0.7)

| δ (per trade) | n_eff needed | ρ = 0.05 | ρ = 0.10 | ρ = 0.20 | ρ = 0.30 |
|---|---|---|---|---|---|
| 2% | 7,574 | 428 months | 804 | 1,556 | 2,308 |
| 5% | 1,212 | 68 months | 129 | 249 | 369 |
| 10% | 303 | 17 months | 32 | 62 | 92 |
| 20% | 76 | 4.3 months | 8.0 | 16 | 23 |

### 3.3 Sensitivity to σ (ρ = 0.10, δ = 10%)

| σ | months, one baseline vs 0 | months per arm, MKT-1 vs RAND-1 |
|---|---|---|
| 0.4 | 10 | 21 |
| 0.7 | 32 | 64 |
| 1.0 | 66 | 131 |

### 3.4 Smallest detectable mean after a given time (base case)

| months | 1 | 3 | 6 | 12 | 24 |
|---|---|---|---|---|---|
| detectable δ | 57% | 33% | 23% | 16% | 12% |

### 3.5 Tail calibration: the forecast's 5% tail is hit at the true rate

| true rate | n_eff needed | months (ρ = 0.10) |
|---|---|---|
| 7.5% | 539 | 57 |
| 10% | 149 | 16 |
| 15% | 43 | 4.6 |

Tail exceedances are assumed to share the same correlation as returns. Crashes hit many trades at once, so this is optimistic.

## 4. What this means
1. **The R1 baselines will not produce evidence of an edge within R1, or within a year.** That is expected, because they aren't designed to have one. Their job is to test the machinery and to set the reference that later engines must beat.
2. **The 5-day live run is a machinery test, not a performance test.** About 70 filled trades per baseline over 5 days are worth about 2 effective observations.
3. **What *can* be learned in months** is gross miscalibration: tail rates off by a factor of 3, or mean errors of 20% or more per trade. The sanity property of MKT-1 is also visible immediately, as a property of the forecasts themselves, with no outcomes needed.
4. **For later engines,** an edge of a few percent per trade on capital at risk can only be confirmed over years of prospective trading, unless correlation is reduced. That supports running replay and prospective trading side by side, and pre-registering a small number of primary tests (EVAL-1 §3).

## 5. Updating the estimate
- **Before the 5-day live run,** the one-month replay (R1.8) provides measured values: the fill rate, σ, and the design effect directly. The design effect is the ratio of the cluster-bootstrap variance to the iid variance of the mean return (EVAL-1 §4).
- **The update rule:** re-run the script with the measured values, record the result here as a dated addendum, and leave the original table unchanged.
- **A replay number is a planning input only.** Replay outcomes are never evidence of edge.

## 6. Questions for Josh (answered 2026-11-09)
1. Are the base assumptions acceptable as the recorded prior (fill rate 70%, σ = 0.7, ρ = 0.10)? Or should the range in §3 be the recorded estimate, with ρ = 0.10 as the central case? **The base case is the centre and the §3 ranges are part of the recorded estimate.**
2. Should the replay-month update (§5) happen before the live run starts, as proposed, or at Stop Point C? **Before the live run.**

---

## Addendum 2026-11-10: measured inputs from the August 2026 replay (§5)
**Source:** the replay month re-run with MOVEGEN-2 and MKT-1/RAND-1 v2 (DR-0037). 21 decision sessions (3–31 August 2026), every position closed by 30 October; 420 recommendations; reports `data/reports/power-inputs-2026-08-v2.md` and `replay-2026-08-v2-cumulative.md` on the Mac mini. **Replay data are planning inputs only, never evidence of edge.** The original estimate above is unchanged.

| Input | Prior | MKT-1 v2 measured | RAND-1 v2 measured | Used from here on |
|---|---|---|---|---|
| Recommendations per day | 10 | 10.0 | 10.0 | 10 (confirmed) |
| Fill rate | 70% | **98%** | **83%** | measured, per baseline |
| σ, the sd of net return on capital at risk | 0.7 | **0.91** | **1.08** | measured, per baseline |
| Design effect (clusters by expiration week) | — | 0.6 (11 clusters) | 2.1 (10 clusters) | **not used** |
| ρ | 0.10 | implied 0.00 | implied 0.006 | **prior 0.10 kept** |

**Why the measured design effect isn't used.** One month is a single market path. 10–11 clusters are far too few to estimate a variance ratio, and a mix of long calls and long puts makes the within-week correlation *negative*, which drives the estimate below 1. A design effect below 1 would claim that overlapping trades are *more* informative than independent ones; nothing supports that for planning. ρ is re-estimated once several months of prospective data exist, with clusters wide enough to capture shared monthly shocks.

**Recomputed with the measured fill rate and σ and the prior ρ = 0.10:**

| Case | Filled per month | Effective per month | Months to detect δ = 5% / 10% / 20% | Smallest δ detectable in 12 months |
|---|---|---|---|---|
| Prior | 147 | 9.4 | 129 / 32 / 8 | 16% |
| MKT-1 v2 | 206 | 9.6 | 215 / 54 / 13 | 21% |
| RAND-1 v2 | 175 | 9.5 | 300 / 75 / 19 | 25% |
| MKT-1 vs RAND-1, per arm (σ 1.0, fill 90%) | about 189 | about 9.5 | 518 / 130 / 32 | — |

**Sensitivity to ρ** (σ 1.0, fill 90%): ρ = 0.05 gives 136 / 34 / 9 months, and ρ = 0.20 gives 505 / 126 / 32 months.

**What changes.**
- The higher measured σ makes detection **slower** than the prior estimate: roughly 1.7–2.3× longer.
- Higher fill rates barely help, because effective observations stay capped near 1/ρ.
- The conclusions in §4 stand and are stronger. R1 can't detect an edge. The first confirmatory analysis at 12 months (EVAL-1 §5) can only detect edges of roughly 20–25% per trade, and smaller edges need years or lower correlation.

**Related findings (for the R1 memo, not power inputs).**
1. **Sanity property.** With the mid-anchored mean, every prediction on the 3 August board is ≤ −0.2%. MKT-1's picks predicted −0.1% on average and realized −18.8%. Long calls and puts realized −32% and −25%, which is consistent with a volatility risk premium that MKT-1 deliberately leaves out (VRP off in R1).
2. **Tail coverage looks worse than it is**, for two reasons: realized returns are measured against capital at risk at the *decision* price, so a lost premium filled at a worse execution price counts below −100%; and the forecast has no slippage model. Both are as specified by EVAL-1, and both go in the R1 memo.
3. **Coverage.** The arbitrage check rejected 250 of 376 expirations on 3 August ("call prices not decreasing with slope in [−DF, 0]"), so MKT-1 and RAND-1 scored only about 13% of candidates. That's an open item in DR-0037.
