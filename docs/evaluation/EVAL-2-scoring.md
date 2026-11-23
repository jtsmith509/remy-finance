# EVAL-2: pre-registered scoring protocol for R2

- **Status:** **revised draft for Josh's approval** (revision 2, 2026-11-23). It folds in Codex's review (E01–E12, `docs/review/EVAL-2-findings.md`) and Josh's decisions. It is frozen in git at approval, *before any empirical R2 model output exists* (R2 brief §0.3, B01).
- **Extends:** EVAL-1 (`docs/evaluation/EVAL-1-scoring.md`, frozen). Everything in EVAL-1 still applies to MKT-1 and RAND-1, including P1 and P2 on their own clock. EVAL-2 adds R2's engines, measures, panels and tests.
- **Decisions:** Josh, 2026-11-23 (§11).

## 1. Scope and version tuple

| Engine | Population | Exit | Role |
|---|---|---|---|
| MKT-1 v4, RAND-1 v4 | all MOVEGEN-2 structures (DR-0041 eligibility) | HOLD-1 | R1's baselines continued, scored by EVAL-1 |
| **FAC-1** | long calls and puts | EXIT-1 | the R2 factor engine; ranks by `ADJ-1` |
| **MKTV-1** | long calls and puts | EXIT-1 | VRP-adjusted market benchmark; ranks by `ADJ-1` |
| **HVOL-1** | long calls and puts | EXIT-1 | historical-volatility benchmark; ranks by `ADJ-1` |
| **RAND-L1** | long calls and puts | EXIT-1 | selection control: FAC-1's forecasts, random picks (§3.1) |

- **The eligible set,** identical for all four long-option engines on a board, is built **once, before any engine-specific filtering**. It contains:
  - DR-0041's rule;
  - DR-0044's 3-year minimum history;
  - EXIT-1's entry boundary: more than 7 calendar days to expiry, and at most 60.
- **Caps:** SELECT-1 (5 picks per board, one per underlying per day).
- **The tested version tuple** (E05): P3 and P4 test **FAC-1, MKTV-1 and RAND-L1 at the versions active at the cut-over**.
  - Scheduled monthly refits of their parameters, under unchanged code and method, are part of the tuple. A change of method or code that alters forecasts is a new version.
  - If any engine in a contrast changes version before an anniversary, that contrast is tested only on boards decided under the original tuple, provided the cohort meets §5's minimum-information rule. Otherwise it is inconclusive.
  - Other version pairs are reported descriptively unless registered in an EVAL revision **before** their first record. A replacement pair is never chosen after results are seen.
- **Inherited from EVAL-1 unchanged:**
  - §1.4: censoring at 61 days;
  - §1.7: no exclusions after the fact, and known defects annotated and reported with and without (§4.4);
  - §7: integrity and no silent pooling across versions.

## 2. Return measures

- **Primary:** EVAL-1's net return on capital at risk at the **decision price**, so slippage between decision and fill counts as a cost (Josh, question 3).
- **Secondary (descriptive):** the same net P&L over capital at risk at the **execution price**, the entry actually paid. It is reported beside the primary measure everywhere returns appear, labelled "execution-price".
- **Values:** both use the cutoff snapshot's **corrected** values (CORRECTION events included), with original values reported as a sensitivity (§4.4).

## 3. Confirmatory hypotheses (R2 family)

| ID | Null hypothesis | Statistic (§4) |
|---|---|---|
| **P3** | The board-weighted mean difference in decision-price returns, FAC-1 minus MKTV-1, over the contrast's mature shared boards, is 0 | T̂ (§4.2) |
| **P4** | The same for FAC-1 minus RAND-L1 | T̂ (§4.2) |

- **What P3 and P4 measure (E02).** P3 and P4 test returns **conditional on inclusion**: boards where both arms filled at least one pick, and every filled pick of both arms is closed. They do **not** establish superiority over all decision opportunities, and they don't isolate the forecast model from execution. Each arm's recommendations, fills, shortfalls, nulls, failed runs and net dollar P&L over every scheduled board are reported descriptively beside them (§4.3). Unfilled opportunities contribute no trade return.
- **Two-sided tests,** with **Holm–Bonferroni across P3 and P4** at a family-wise **α = 0.05 at each anniversary**. This family is separate from EVAL-1's P1–P2 (Josh, question 7).
- **Across anniversaries (E06; Josh):** each anniversary's analysis stands alone. Holm controls the P3/P4 family *at that look*; there is **no across-anniversary error guarantee**, and the probability of ever declaring a false difference grows with repeated looks. Every report shows all earlier looks. A contrast that is inconclusive at a look (§4.3, §5) takes its Holm slot as a non-rejection with p = 1. A two-sided rejection in the negative direction is evidence in the comparator's favour.
- **Descriptive only:** every HVOL-1 comparison, every forecast score (§6, §7), the execution-price measure, and every comparison involving MKT-1 v4 or RAND-1 v4 beyond EVAL-1's own P1 and P2.

### 3.1 RAND-L1's sampling rule (E03; Josh)
On each board, RAND-L1 draws from **exactly the candidates FAC-1 could pick**: the §1 eligible set, restricted to contracts with a valid FAC-1 forecast. The rules:
- Draws are **uniform over contracts, without replacement.**
- Once a contract is picked, every other contract on the same underlying leaves the pool.
- Underlyings RAND-L1 itself picked earlier the same day are excluded (SELECT-1).
- It draws up to 5, or until the pool is empty.
- The seed is `sha256("<snapshot_id>\nRAND-L1")`. The draw order is the `sample_key` order over candidate content hashes, as RAND-1 (DR-0031).
- The draw and any shortfall are recorded in the run log.
- If FAC-1's forecasts are unavailable on a board, RAND-L1 has no pool, and the board is a failed run for P4 (§4.3).

P4 compares two **selection policies** on the same forecasts and pool, including their own cap histories and fill behaviour.

## 4. The paired statistic

### 4.1 Cohort and enrolment (E04, E05)
- **The cut-over** is the approved ET session and first eligible R2 decision slot, published in the cut-over DR **before activation**. The clock starts at that slot even if recording is partial.
- **Anniversary k** has cutoff = the cut-over session + k × 12 months, at 17:30 ET. It uses the registry and correction snapshot exported at that cutoff, identified by its chain heads.
- **Enrolment is cumulative:** every scheduled R2 decision slot from the first eligible slot through (cutoff − 61 calendar days). Decisions too recent for one anniversary enter the next.

### 4.2 Statistic (Josh, questions 2 and 9)
- **A board enters a contrast** only if both arms' runs qualify, each arm has at least one *filled* pick, and **every** filled pick of **those two arms** is closed in the cutoff snapshot. A board is never kept with only its closed picks.
- **Per board:** each arm's mean realized return (primary measure) over its filled picks. The board difference is FAC-1 minus the comparator.
- **Per ISO decision week t:** S_t = the sum of that week's board differences, and n_t = its number of entering boards. Weeks with none are kept, with S_t = n_t = 0.
- **The statistic:** T̂ = Σ_t S_t / Σ_t n_t, the mean over entering boards, each board counting once.

### 4.3 Exclusions, failures and coverage (E04; Josh)
- **Denominator:** every scheduled, age-eligible decision slot in the enrolment window for the contrast.
- **Reasons,** each slot assigned the first that applies, in this order:
  1. decision board not captured (a gap);
  2. either arm's run failed or was unavailable;
  3. either arm made a null decision;
  4. either arm filled nothing;
  5. either arm has a filled pick not yet closed at the cutoff.
  
  Counts are reported per reason and per arm, with overlaps shown, and the total retained fraction is reported.
- **Coverage rule:** compute, **before examining any pick or fill**, the share of age-eligible *captured* boards on which both engines were scheduled and at least one engine's run failed (reason 2). If it **exceeds 10%**, the contrast is **inconclusive** at that anniversary, whatever its p-value, and takes its Holm slot as p = 1. At or below 10%, the result stands, with all counts reported.
- **No trades are invented** for null or unfilled picks.

### 4.4 Known defects and corrections (E11)
- **The primary** includes annotated trades and uses corrected values.
- **Sensitivities (labelled, never confirmatory):**
  - **original values:** without corrections;
  - **annotated trades removed:** each arm's board mean is recomputed, and the board is excluded only if an arm then has no fill. Both the changed board means and the changed board membership are reported.
  - A defect annotated against a **whole board** gets its own board-level sensitivity.
  
  These never replace the primary tests.

## 5. Statistical method (E01)

- **Why not the bootstrap.** Independent resampling of decision weeks is invalid here. Trades from different weeks are open together for up to about 9 weeks and share market moves. In synthetic nulls with that overlap, a naive week bootstrap rejected a true null **12–33%** of the time at a nominal 5% (`docs/evaluation/evidence/eval2-ewc/`).
- **Primary inference: an equal-weighted-cosine (EWC) HAC t-test** on the linearized ratio.
  - u_t = S_t − T̂·n_t, for t = 0 … W−1, where W is the number of ISO decision weeks in the enrolment window, empty weeks included.
  - **K = ⌊W/12⌋** cosine terms, fixed by W alone and never by the data (Josh).
  - Λ_j = √(2/W) · Σ_t cos(π·j·(t + ½)/W) · u_t, for j = 1 … K.
  - Ω̂ = (1/K) · Σ_j Λ_j².
  - se = √(W·Ω̂) / Σ_t n_t; t = T̂ / se.
  - **p-value:** two-sided, from Student-t with K degrees of freedom. The **95% interval** is T̂ ± t_{K,0.975}·se. It inverts the same test, so its coverage at the true value equals 1 − size.
  - **Implementation:** the Student-t CDF and quantile are implemented in the project (regularized incomplete beta by continued fraction) and tested against standard tables. No resampling and no random seed are used.
- **Minimum information (E01; Josh):** a contrast is testable only if **K ≥ 3** (W ≥ 36), at least **30 weeks contain an entering board**, Σ n_t > 0 and se > 0. Otherwise it is **inconclusive** (p = 1 in Holm).
- **Validation, recorded in `docs/evaluation/evidence/eval2-ewc/`:**
  - **Method:** three blind independent implementations plus a 100,000-replication reference.
  - **Synthetic nulls:** overlapping holds of up to 8 or 12 weeks, persistent common loadings, AR(1) week effects, t(3) shocks, Poisson board counts and up to 35% empty weeks.
  - **Size of K = ⌊W/12⌋ at a nominal 5%:**
    - 4.3–5.8% at W = 43 and 4.7–5.8% at W = 95, for holds of up to 8 weeks;
    - at most 6.6% for holds of up to 12 weeks.
  - **At a nominal 2.5%:** 2.0–3.0%.
  - **Scope:** the synthetic null models a single board-weighted mean with cross-week dependence. It doesn't model the two-arm fill selection, which §3 states as a limitation of the estimand.
- **Sensitivity (labelled, never confirmatory):** the naive independent-week bootstrap is also reported.
  - B = 10,000 resamples of W weeks with replacement, moving (S_t, n_t) together and recomputing T̂.
  - Seed: `numpy.random.SeedSequence(int(sha256(b"EVAL-2").hexdigest()[:16], 16))`, with one child stream per contrast in the order P3, P4. A draw with Σ n* = 0 is redrawn, and redraws are counted.
  - It's reported with its percentile interval, plus the naive standard error and the design effect (EWC variance over naive variance).

## 6. The common-contract prediction tournament (B08; spec §11; EVAL-1 §4)

### 6.1 Panel (E07; Josh, question 5)
- **When it's built:** once per OPEN and CLOSE board, **from the board alone**, before any engine runs, over the §1 eligible set.
- **The cells:** for each eligible name, 8 cells: {call, put} × {target |delta| 0.25, 0.50} × {target 21, 42 calendar days}.
  - **Delta:** Black-76 forward delta, N(d1) for calls and N(d1) − 1 for puts, with targets matched in absolute value. It uses MKT-1's fitted forward for that expiration and the contract's mid implied volatility. The MKT-1 fit is computed for the panel even on boards where MKT-1 recommends nothing.
  - **Choosing a contract:** first the expiration nearest the target days, with ties going to the earlier expiration. Then the strike whose |delta| is nearest the target, with ties going to the lower strike, then the contract ID.
- **Unavailable cells:** a cell without a valid forward, mid-IV or contract is recorded as unavailable, with its reason, and is **never replaced** by an engine-specific choice.
- **Duplicates:** a contract filling two cells is one panel contract, recorded once with both cell labels.

### 6.2 Records (E08)
- **What each engine records:** every long-option engine (FAC-1, MKTV-1, HVOL-1) records its predicted EXIT-1 net-return distribution (EVAL-1's 15 quantile levels and bins) for **every** panel contract, including on boards where it picks nothing.
- **Timing:** each prediction, with its receipt time, is frozen **before the execution quote**, on the decision information set.
- **RAND-L1** has no forecasts of its own, so it is not a separate forecaster.
- **Missing forecasts:** recorded as missing, with their reason, and never filled in later.

### 6.3 Shadow outcomes (E08)
- **The rules:** each panel contract is evaluated as a hypothetical position, **independently** of every other, as follows.
  - Quantity: the board-known SIZE-1 rule.
  - Return denominator: the decision-price capital at risk.
  - Entry: FILL-1 at the execution quote.
  - Exits and settlement: EXIT-1's exit, retry and settlement rules, against the stored later boards.
- **Scoring:** a return is scored only for a **filled, closed and mature** shadow entry (censored as in §1). Unfilled, still-open and missing outcomes are counted separately. They're never scored, and never given an invented return.

### 6.4 Scores (descriptive)
- **Per engine:**
  - CRPS, the binned log score, PIT and coverage (EVAL-1 §2.1), over its scoreable contracts;
  - expected panel IDs, forecast coverage and scoreable-outcome coverage, by board session and horizon.
- **The common-set comparison:** paired CRPS differences FAC-1 − MKTV-1 and FAC-1 − HVOL-1, on scoreable contracts where **both** models in the pair forecast.
  - The retained count is reported against the full panel.
  - Intervals use §5's EWC method on weekly sums and counts.
  - These scores describe model quality **only on the retained contracts**, never on absent ones.

## 7. Underlying and implied-volatility forecasts

### 7.1 S_T/F forecasts (E12)
- **MKT-1's risk-neutral S_T/F** is scored as now (DR-0040).
- **FAC-1** freezes, for **every eligible name and expiration** on each board, irrespective of picks, one physical S_T/F forecast from its paths. Each records:
  - the decision-time forward;
  - the target session (the expiration);
  - EVAL-1's 17-level grid;
  - its provenance.
- **Scoring:** realized official close ÷ the recorded forward, once the target-session close is public, using §4.1's cohort and cutoff. It's labelled **physical**, apart from the risk-neutral scores.
- **Exclusions:** missing fits and closes are counted by reason. Where a corporate action between decision and target breaks price-unit compatibility (B10's as-of rules), the forecast is flagged and excluded from the score, and counted.

### 7.2 IV forecasts (B09, E09; Josh, questions 6 and E09)
- **Records:** at each OPEN board, for each eligible name and horizon h ∈ {5, 21} sessions (counted on the XNYS calendar from the decision session), FAC-1 and MKTV-1 each record 15 quantiles of the name's 30-day ATM IV, in annualized volatility units, with provenance.
- **FAC-1's quantiles** come from its paths.
- **MKTV-1's quantiles** follow a median rule:
  - **The median** is the **previous session's** `HIST-1` 30-day ATM IV (the latest public value at the OPEN board) × the VRP ratio available at the board (P9, regressed on `SPYIV30` per DR-0044).
  - **The log-spread** sd_h is the standard deviation of that name's realized h-session log changes in 30-day ATM IV over its rolling 3-year window, using only changes fully observed and public before the board.
  - **Quantile p** = median × exp(sd_h × z_p).
- **Outcome:** the same name's `HIST-1` 30-day ATM IV on the **exact target session**, derived with the identical method, scoreable once public by the analysis cutoff. Missing forecasts and missing outcomes are counted separately, by horizon.
- **Scores (descriptive):** CRPS, PIT and coverage by horizon.

## 8. Schedule (no optional stopping)

- **The walk-forward replay** (November 2025 to October 2026; R2.8) is **descriptive and labelled replay**. Its dates are fixed now. A model changed after its results are seen is a new version, and the replay is re-run and labelled as such. Replay results are never evidence for P3 or P4.
- **The 20-day live run** (Stop Point F) is an **operational gate, descriptive only**. Under 61-day censoring, no R2 trade is mature by then.
- **Confirmatory analyses** run at each anniversary of the cut-over (§4.1): 12 months after it, then every 12 months.
- **Monthly reports:** descriptive, labelled "interim; not a test", with no p-values for P3 or P4.

## 9. Planning power (prior; updated at Stop Point E with replay-measured inputs)

- **Benchmark assumptions:** about 10 boards a week, up to 5 picks per board, so n ≈ 50 trades a week per engine; σ ≈ 1.0 per trade; within-week correlation ρ = 0.10; a cross-arm weekly correlation of 0.5. This gives a weekly paired-difference sd of about 0.34.
- **The first anniversary** has about **43** enrolled weeks (because of the 61-day lag), so K = 3. Two-sided at Holm's stricter α = 0.025, with 80% power and Student-t_3 critical values, the detectable mean difference is about **(4.18 + 0.98) × 0.34 / √43 ≈ 27 points** of return on capital at risk.
- **The second anniversary:** about 95 weeks, K = 7, about **13 points**.
- **Simulated power** of the actual test at a difference of 0.15 board-sd, two-sided at 0.05:
  - about **18%** at W = 43 and **42%** at W = 95 when weeks are only weakly dependent;
  - about **7%** and **10%** under strong cross-week dependence.
- **These are optimistic benchmarks, not fitted design effects (E10).** Fewer fills, unequal weeks, coverage losses and cross-week dependence all lower power.
- **The update rule at E:**
  - recompute the above with the replay's measured fill rates, board counts, σ and an estimated long-run variance, for P3 and P4 separately;
  - record it as a dated addendum;
  - never change §3–§8 because of it.

## 10. Integrity

- **Freezing:** EVAL-2 is frozen at its approval commit, which every R2 report prints next to EVAL-1's. Changes go only through EVAL-3, before the records they affect.
- **Post-hoc analyses** are labelled post hoc wherever they appear.
- **The validation scripts and results** in `docs/evaluation/evidence/eval2-ewc/` are part of the frozen record.

## 11. Josh's answers

**First round (2026-11-23):**
1. Confirmatory hypotheses: **two**, P3 (FAC-1 vs MKTV-1) and P4 (FAC-1 vs RAND-L1).
2. Statistic: **the per-board mean difference over shared boards.**
3. Measure: **decision-price primary**; execution-price descriptive.
4. Clusters: **ISO decision weeks.** The inference method was revised under E01; see §5.
5. Common panel: **a fixed delta grid** (25 and 50 delta, calls and puts, about 21 and 42 days).
6. IV forecasts: **5 and 21 sessions ahead**, descriptive.
7. Multiplicity: **a separate R2 family**, Holm, α = 0.05.
8. Schedule: **12 months after the cut-over, then yearly.**
9. Failures: **dropped from the pair and counted.**

**After Codex's review (2026-11-23):**
- E01: **EWC HAC t-test, K = ⌊W/12⌋, minimum K = 3.**
- E03: **RAND-L1 uniform over contracts.**
- E04: **above 10% engine failure, the contrast is inconclusive.**
- E06: **each anniversary stands alone.**
- E09: **MKTV-1's IV forecast uses the median rule, with the spread fitted per horizon.**
