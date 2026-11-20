# REMY Finance — build brief: stage R2 (classical stochastic baseline)

**Status: DRAFT, not approved.** Josh answered the scoping questions (§11) and P1–P14 (§2, 2026-11-20). Nothing in this brief is built until Josh approves it.
**Owner:** Josh Smith · **Repo:** `github.com/jtsmith509/remy-finance` · **Drafted:** 2026-11-20 by Claude, from Josh's answers to the R2 scoping questions (§11)
**Governing spec:** `docs/spec/spec_v0.5.md` §13 (R2 is marked *proposed* there). Where this brief and the spec disagree, stop and ask.
**Builds on:** R0–R1 (complete; `docs/memos/R0-result.md`, `docs/memos/R1-result.md`). The R0–R1 brief's ground rules, testing standards and conventions continue to apply unless this brief changes them.

---

## 0. Ground rules for R2

1. **Scope is R2 only.** Do not build R3–R6: no causal search, event ordering or opening books (R3); no policy optimizer, conditional entries or MultiPV (R4); no neural or hybrid models (R5–R6). Leave clean seams for them; don't stub them out in detail.
2. **Stage process is unchanged.** Every material choice gets a DR (status *proposed* until Josh confirms). Each stop point gets a result memo, a Codex review brief, and Josh's explicit confirmation.
3. **Pre-registration comes first.** Per spec §1, the scoring rules for R2 results (**EVAL-2**) are written and frozen **before any R2 result is examined**. That includes the walk-forward replay.
4. **No look-ahead, ever.** Every fit, estimate and path is computed only from data available at its decision time, through `PointInTimeView`. Rolling refits are dated and stored write-once.
5. **R1's record is never disturbed.** MKT-1 v3 and RAND-1 v3 keep running unchanged until their R2 versions replace them at an announced cut-over. Prospective and replay never mix.
6. Unchanged from R0–R1: no brokerage code; secrets only in environment variables or the Keychain; `Decimal` for all cash; an offline test suite.

---

## 1. Settled for R2 (Josh, 2026-11-20; reopen only through a DR)

| Area | Decision |
|---|---|
| Brief | Claude drafts, Josh approves (this document). |
| Carried in from R1 | DR-0041 (exclude candidates that can only lose), DR-0042 (rank on excess return, `ADJ-1`), **EVAL-2** (adds an execution-price measure), **VRP estimation**, the **text archive** (DR-0036, collection only), and a **write-once official-close store**. |
| Factor model | **Named market factors plus a PCA residual:** observable named factors first, then a few statistical factors on what's left. |
| Paths | Joint daily paths of **prices plus a per-name ATM implied-volatility level** driven by a volatility factor. Each name's smile *shape* stays fixed from the decision board. |
| Exit rule | **Profit target plus time stop:** exit when the position's value reaches 2× the premium paid, or with 7 calendar days left, whichever comes first; otherwise EXP-1. ASSIGN-1 still applies. |
| History | **3 years, rolling,** before each decision date, refitted monthly with the universe. |
| New engine | Recommends **long calls and puts only** (spec §13), ranked by `ADJ-1`, each with the exit rule. |
| Benchmarks | **MKT-1 with VRP**, and a **historical-volatility lognormal** baseline, alongside MKT-1 and RAND-1. |
| Validation | A **12-month walk-forward replay** before the new engine records live. |
| Stop points | **D, E and F** (§9). |

---

## 2. Design choices (Josh's answers, 2026-11-20; each becomes a DR at build time)

| # | Item | Decision |
|---|---|---|
| P1 | Engine names | `FAC-1` (factor engine), `HVOL-1` (historical-volatility lognormal), `MKTV-1` (MKT-1 with VRP), `RAND-L1` (random long options, P11), exit rule `EXIT-1`, history store `HIST-1`, close store `CLOSE-1`. |
| P2 | MKT-1 and RAND-1 in R2 | **v4** = DR-0041 eligibility + DR-0042 `ADJ-1` ranking, still risk-neutral. The VRP version is a *separate* engine (`MKTV-1`), so MKT-1 stays the pure market-implied reference. |
| P3 | Named factors | Market (SPY); the 11 sector SPDRs, as sector factors after removing the market; rates (daily change in the 10-year Treasury yield); volatility (daily log change in VIX). Each name loads on the market, its own sector, rates and volatility. |
| P4 | PCA residual | Components on the residual correlation after the named factors, chosen at each monthly refit by the **Kaiser rule** (eigenvalue above the average), **at most 5**. The count used is recorded with each fit. |
| P5 | Shock distribution | Filtered historical simulation: GARCH(1,1) per factor, with standardized residuals bootstrapped jointly by date, so cross-factor dependence and fat tails are kept. Idiosyncratic residuals are bootstrapped by name. |
| P6 | IV level | Each name's 30-day ATM IV log change = β_vol × VIX log change + β_spot × the name's own log return (the spot-vol, or leverage, effect) + its own residual, fitted on the same 3 years. Option values along a path use Black-76 at the simulated forward, with IV = simulated level × the decision board's fixed smile shape in log-moneyness ("sticky moneyness"). |
| P7 | Exit fills on paths | A simulated exit sells at the modelled mid minus the decision board's relative half-spread for that contract. Live exits use FILL-1 at the execution quote, as in R1. |
| P8 | Paths and time | 2,000 joint daily paths per decision board, with the profit target checked at each daily close. **Budget:** all R2 engines must finish inside the 5-minute decision-to-execution window on the Mac mini. If not, a DR reduces paths (never below 500) or checks every few days instead, never the window. |
| P9 | VRP estimate | **Regime-dependent.** Per name, over the rolling 3 years: the ratio of realized 30-day variance to the 30-day implied variance at its start, modelled as *a + b × VIX at the start*, with a and b shrunk toward the sector's, and the ratio clipped to [0.3, 1.5]. `MKTV-1` scales the risk-neutral distribution's variance by that ratio around the same forward. |
| P10 | `HVOL-1` | A lognormal at each name's trailing 60-day realized volatility, drifting at the risk-free rate. It ranks long options by `ADJ-1` with `EXIT-1`, valued on its own paths. |
| P11 | Long-option comparison set | FAC-1, MKTV-1, HVOL-1 and **`RAND-L1`** (seeded random long options, its forecasts from FAC-1 as RAND-1's come from MKT-1) all select long options with `EXIT-1` under the same caps, so EVAL-2 compares them paired, on the same boards. RAND-L1 is the selection-skill control. MKT-1 v4 and RAND-1 v4 keep the full structure set with HOLD-1. |
| P12 | Close store | `CLOSE-1`: the nightly step saves the official close of every universe name, SPY, the sector SPDRs and VIX once it's public, write-once under `data/closes/`. **A one-off backfill** covers every past date the records need (live from 11 November 2026, plus the replay months), each close stamped with its real public time. F09 and R2 score only from the store; report-time fetching only fills gaps. This amends DR-0040. |
| P13 | History store | `HIST-1`: daily closes and 30-day ATM IV per name, plus the factor series, for at least 3 years before the replay's first month, stored write-once with manifests. **ATM IV is derived from ThetaData's end-of-day option quotes** with the same parity forward and Black-76 as MKT-1, interpolated to 30 days, not taken from a vendor IV field. |
| P14 | Live run for stop point F | 20 consecutive trading days with all R2 engines recording, with no manual intervention. |

---

## 3. Data (R2.0)

- **Verification spike first, as R0.1 was.** Confirm ThetaData (and Massive where needed) can supply, point-in-time:
  - 3+ years of daily official closes for the universe candidates, SPY, the sector SPDRs and VIX;
  - daily 30-day ATM implied volatility per name, or EOD option quotes to derive it;
  - the 10-year Treasury yield.
  
  Record coverage, gaps and the license terms in a memo.
- **Build `HIST-1` and `CLOSE-1`** (P12, P13). Every series is stamped with its public time, so the view refuses anything not yet public.
- **Text archive (DR-0036):** start collection only (EDGAR, Massive ticker news, Fed/Treasury releases), write-once, with no model use in R2.

---

## 4. Tasks

### Part 1: foundations → **STOP POINT D**
- **R2.0:** data verification spike, `HIST-1`, `CLOSE-1` and the text-archive collector.
- **R2.1, EVAL-2 (pre-registered, frozen at D):**
  - the execution-price measure alongside EVAL-1's;
  - the paired long-option comparison (FAC-1 vs MKTV-1 vs HVOL-1, with RAND-L1 as the selection control);
  - S_T/F and IV-level forecast scores for FAC-1;
  - updated power estimates for the paired comparison;
  - the confirmatory schedule.
- **R2.2:** MKT-1 v4 and RAND-1 v4 (DR-0041, DR-0042), plus the cut-over plan.
- **R2.3:** VRP estimation (P9) and `MKTV-1`.
- **R2.4:** the factor model: named factors, loadings, PCA residual, GARCH filters and the IV-level model.
  - Point-in-time monthly fits, stored write-once.
  - In-sample diagnostics: variance explained, residual correlation, and the stability of the loadings.

### Part 2: engines and walk-forward → **STOP POINT E**
- **R2.5:** the path generator (P5–P8): joint daily paths of prices and IV levels, reproducible from a recorded seed.
- **R2.6:** `EXIT-1`, in the live engine (on real boards) and on simulated paths.
- **R2.7:** `FAC-1`, `HVOL-1` and `RAND-L1`: forecast distributions per candidate, recorded in the existing `Recommendation` schema, plus S_T/F quantiles in the run log, ranked by `ADJ-1`.
- **R2.8:** the 12-month walk-forward replay of every R2 engine, point-in-time with monthly refits, scored under the frozen EVAL-2 and labelled replay.
- **R2.9:** reports: EVAL-2 sections, the paired comparison, and the forecast scores for the factor model.

### Part 3: live → **STOP POINT F**
- **R2.10:** cut-over: MKT-1 v4, RAND-1 v4, FAC-1, MKTV-1, HVOL-1 and RAND-L1 start recording prospectively.
- **R2.11:** a live run (P14), then the R2 result memo.

---

## 5. Testing standards (additions to R0–R1 §7)

- **Factor model:** on synthetic data generated from a known factor model, the fit recovers the loadings and factor covariance within tolerance.
- **Paths:** same seed, same paths; the moments of the simulated factors match the fitted ones; the drift is risk-free where it's meant to be.
- **Option valuation on paths:** at time 0 it reproduces the decision board's mids within tolerance (smile fixed, IV level = the board's).
- **`EXIT-1`:** fixtures for the profit target hit, the time stop, and neither (EXP-1), on both real boards and paths.
- **No look-ahead:** every fit and estimate is asked for data after its date, and must raise.
- **VRP:** the estimator on synthetic series with a known premium.
- **The performance budget (P8)** is a test that runs on a full-size synthetic board, marked slow.

---

## 6. Explicitly out of scope for R2

Causal search, event ordering and opening books (R3); policy optimization, conditional entries, partial exits beyond `EXIT-1`, and MultiPV (R4); neural models (R5); hybrids (R6); spreads in FAC-1; full surface dynamics (skew and term structure); intraday data; any use of the text archive by a model; brokerage.

---

## 7. Definition of done

- **Stop point D:** `HIST-1`, `CLOSE-1` and the archive collector run. EVAL-2 is frozen. MKT-1 v4, RAND-1 v4 and MKTV-1 pass their tests. The factor model is fitted point-in-time, with its diagnostics in a memo.
- **Stop point E:** FAC-1, HVOL-1 and RAND-L1 pass their tests, including the performance budget. The 12-month walk-forward replay completes, with `registry verify` OK, and is scored under EVAL-2 as replay. Calibration findings are explained.
- **Stop point F:** the cut-over is done and the live run passes (P14). The R2 memo is written, and every DR is confirmed.

---

## 8. Risks to watch

- **Compute:** FAC-1 values every long option on every path at every daily close. If it can't meet the 5-minute budget, P8 changes, never the schedule.
- **Data:** 3-year IV history may be patchy for some names. The verification spike decides which names FAC-1 can cover.
- **Overfitting the replay:** EVAL-2 is frozen before the replay is looked at. Any model change after E is a new version, and the replay is re-run.

---

## 9. Stop points

| Stop | When | Josh confirms |
|---|---|---|
| **D** | After Part 1 | Data coverage, the frozen EVAL-2, the R2 baselines, and the factor-model fit |
| **E** | After Part 2 | The engines, the 12-month walk-forward results, and going live |
| **F** | End of R2 | The live run and the R2 memo |

At each stop: what was built, the test results, the DRs awaiting confirmation, and the specific questions. Then wait.

---

## 10. Remaining before approval

All of P1–P14 are answered. What's left is Josh's approval of the brief as a whole. Each P-choice is recorded as a DR when its task is built, and amended only through a new DR.

---

## 11. Josh's scoping answers (2026-11-20)

1. The brief: Claude drafts, Josh approves.
2. Carry-ins: DR-0041 + DR-0042 + EVAL-2; VRP estimation; the text archive; an underlying close store.
3. Factors: named plus a PCA residual.
4. Paths: prices plus an IV-level factor.
5. Exit rule: profit target plus time stop.
6. History: 3 years, rolling.
7. New engine: long options only.
8. Benchmarks: MKT-1 with VRP, and a historical-volatility lognormal.
9. Validation: a 12-month walk-forward replay.
10. Stop points: D, E and F.
