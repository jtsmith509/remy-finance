# EVAL-2: pre-registered scoring protocol for R2

- **Status:** **draft for Josh's approval.** It is frozen in git at approval, *before any empirical R2 model output exists* (R2 brief §0.3, B01).
- **Extends:** EVAL-1 (`docs/evaluation/EVAL-1-scoring.md`, frozen). Everything in EVAL-1 still applies to MKT-1 and RAND-1, including P1 and P2 on their own clock. EVAL-2 adds R2's engines, measures, panels and tests.
- **Decisions:** Josh, 2026-11-23 (§10).

## 1. Scope

| Engine | Population | Exit | Role |
|---|---|---|---|
| MKT-1 v4, RAND-1 v4 | all MOVEGEN-2 structures (DR-0041 eligibility) | HOLD-1 | R1's baselines continued, scored by EVAL-1 |
| **FAC-1** | long calls and puts | EXIT-1 | the R2 factor engine |
| **MKTV-1** | long calls and puts | EXIT-1 | VRP-adjusted market benchmark |
| **HVOL-1** | long calls and puts | EXIT-1 | historical-volatility benchmark |
| **RAND-L1** | long calls and puts | EXIT-1 | selection control (FAC-1's forecasts, random picks) |

- **Shared rules:** all four long-option engines use the same eligible set on each board, DR-0041's rule plus DR-0044's 3-year minimum history; the same caps (SELECT-1: 5 picks, one per underlying per day); and `ADJ-1` ranking (DR-0042).
- **Inherited from EVAL-1 unchanged:**
  - §1.4: censoring at 61 days, plus closure by the cutoff (Codex V05);
  - §1.7: no exclusions after the fact, and known defects annotated and reported with and without;
  - §7: integrity.

## 2. Return measures

- **Primary:** EVAL-1's net return on capital at risk at the **decision price**, so slippage between decision and fill counts as a cost (Josh, question 3).
- **Secondary (descriptive):** the same net P&L over capital at risk at the **execution price**, the entry actually paid. This is the measure Josh asked for at Stop Point C, question 2. It is reported beside the primary measure everywhere returns appear, labelled "execution-price".

## 3. Confirmatory hypotheses (R2 family)

| ID | Null hypothesis | Statistic (§4) |
|---|---|---|
| **P3** | FAC-1's realized returns equal MKTV-1's: E[R_FAC-1 − R_MKTV-1] = 0 | the mean over shared boards of the per-board difference in mean realized return |
| **P4** | FAC-1's selection adds nothing over random selection with the same forecasts: E[R_FAC-1 − R_RAND-L1] = 0 | the same, against RAND-L1 |

- **Two-sided tests,** with **Holm–Bonferroni across P3 and P4** at a family-wise **α = 0.05**. This family is separate from EVAL-1's P1–P2 (Josh, question 7).
- **Descriptive only:** every HVOL-1 comparison, every forecast score (§5, §6), the execution-price measure, and every comparison involving MKT-1 v4 or RAND-1 v4 beyond EVAL-1's own P1 and P2.

## 4. The paired statistic (Josh, questions 2 and 9)

- **A shared board** is a decision board on which both engines of the contrast produced recommendations, and each has at least one *filled* pick.
- **Per board:** take each engine's mean realized return (primary measure) over its filled picks. The board's value is the difference, FAC-1 minus its comparator. Every shared board counts once, however many picks it had.
- **The statistic** is the mean of the board differences over all shared boards in the analysis cohort. A board's trades enter only when **all** of its filled picks satisfy §1's censoring and closure rule.
- **Exclusions:**
  - A board where either engine failed, made a null decision or filled nothing is excluded from that contrast, never imputed. Counts are reported per reason.
  - If more than **10%** of otherwise eligible boards are excluded **for engine failure** (not for filling nothing), the confirmatory result is flagged **"coverage-limited"**.
- **No trades are invented** for null or unfilled picks.

## 5. Statistical method

- **Clusters:** the **ISO week of the decision date** (Josh, question 4). Boards in one week share the market moves that follow them, and early exits under EXIT-1 don't wait for expiration.
- **Inference:** a cluster bootstrap over weeks, with 10,000 resamples, a fixed seed from `sha256("EVAL-2")`, percentile intervals, and two-sided p-values from the bootstrap distribution, centred under the null.
- **Also reported:** the naive iid standard error and the design effect, as in EVAL-1 §4.

## 6. The common-contract prediction tournament (B08; spec §11; EVAL-1 §4)

- **The panel, per eligible name per OPEN and CLOSE board:**
  - the call and the put nearest to **25 delta** and to **50 delta**,
  - in the expirations nearest to **21 days** and **42 days** (DTE 7–60),
  - which gives up to 8 contracts per name.
  
  Delta is Black-76 at the board's MKT-1 forward and mid-IV. If two contracts are equally near, the lower strike is chosen. The panel depends only on the board, never on any engine's picks or outcomes (Josh, question 5).
- **Records:** every long-option engine records, before the outcome, its predicted EXIT-1 net-return distribution (EVAL-1's 15 quantile levels and bins) for **every** panel contract, including on boards where it picks nothing. A missing forecast is recorded as missing, with its reason, and never filled in later.
- **The outcome** is the contract's realized EXIT-1 net return under FILL-1 at the later execution quotes, computed from the stored boards exactly as for a trade, including unfilled entry and exits. It's a shadow outcome, not a registry trade.
- **Scores (descriptive):**
  - CRPS, the binned log score, PIT and coverage (EVAL-1 §2.1), per engine;
  - paired **CRPS differences** FAC-1 − MKTV-1 and FAC-1 − HVOL-1 on contracts every engine covered, with cluster-bootstrap intervals by decision week.

## 7. Underlying and implied-volatility forecasts

- **S_T/F forecasts (EVAL-1 §2.2, DR-0040):**
  - MKT-1's risk-neutral S_T/F is scored as now.
  - FAC-1's physical S_T/F forecasts, from its paths, are scored the same way but **labelled physical**, apart from the risk-neutral ones: risk premia make their expected miscalibration different.
- **IV forecasts (B09; Josh, question 6):**
  - **Records:** at each OPEN board, FAC-1 records each eligible name's 30-day ATM IV quantiles for **5 and 21 sessions ahead**. MKTV-1 records the same, as today's IV times its VRP ratio, with a lognormal spread from the fitted IV residual. Each record holds the decision and target dates, the horizon, the 15 quantile levels, the units (annualized volatility) and its provenance.
  - **Outcome:** the same name's 30-day ATM IV on the target session, derived in `HIST-1` from that session's EOD chain with the identical method. If the target session's IV can't be derived, the forecast is unscored and counted.
  - **Scores (descriptive):** CRPS, PIT and coverage by horizon.

## 8. Schedule (no optional stopping)

- **The walk-forward replay** (November 2025 to October 2026; R2.8) is **descriptive and labelled replay**. Its dates are fixed now. A model changed after its results are seen is a new version, and the replay is re-run and labelled as such. Replay results are never evidence for P3 or P4.
- **The 20-day live run** (Stop Point F) is an **operational gate, descriptive only**. Under 61-day censoring, no R2 trade is mature by then.
- **Confirmatory analyses:** **12 months after the R2 cut-over** (the first prospective R2 record), then every 12 months (Josh, question 8). Each uses §1's censoring and closure rule, and is recorded with its code commits and registry heads.
- **Monthly reports:** descriptive, labelled "interim; not a test", with no p-values for P3 or P4.

## 9. Planning power (prior; updated with replay-measured inputs at Stop Point E)

- **Assumptions:**
  - about 10 boards a week, with up to 5 picks each, so n ≈ 50 trades a week per engine;
  - σ ≈ 1.0 per trade (R1 replay: 0.96);
  - within-week correlation ρ = 0.10 (EVAL-1 prior);
  - a 0.5 correlation between two engines' weekly means.
- **Result:** the weekly difference has sd ≈ √(2 × σ²(ρ + (1−ρ)/n) × 0.5) ≈ 0.34. With about 52 weekly clusters a year, a two-sided test at Holm's stricter level (α = 0.025) with 80% power detects a mean difference of about **3.1 × 0.34 / √52 ≈ 15 percentage points** of return on capital at risk after 12 months.
- **What this means:** like R1, R2 can detect only large differences in its first year. The replay-measured σ, fill rates and correlations replace these priors at E, as a dated addendum, without changing §3–§8.

## 10. Josh's answers (2026-11-23)

1. Confirmatory hypotheses: **two**, P3 (FAC-1 vs MKTV-1) and P4 (FAC-1 vs RAND-L1).
2. Statistic: **the per-board mean difference over shared boards.**
3. Measure: **decision-price primary**; execution-price descriptive.
4. Clusters: **the ISO week of the decision date.**
5. Common panel: **a fixed delta grid** (25 and 50 delta, calls and puts, about 21 and 42 days).
6. IV forecasts: **5 and 21 sessions ahead**, descriptive.
7. Multiplicity: **a separate R2 family**, Holm, α = 0.05.
8. Schedule: **12 months after the cut-over, then yearly.**
9. Failures: **dropped from the pair and counted**, flagged "coverage-limited" above 10%.
