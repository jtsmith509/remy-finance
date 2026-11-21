# REMY Finance — build brief: stage R2 (classical stochastic baseline)

**Status: APPROVED by Josh, 2026-11-21.** This is the governing brief for R2. It incorporates the scoping answers (§11), P1–P14 (§2, 2026-11-20) and Codex's plan review (B01–B14, §12). EXIT-1's target is valued at the bid.
**Owner:** Josh Smith · **Repo:** `github.com/jtsmith509/remy-finance` · **Drafted:** 2026-11-20 by Claude, from Josh's answers to the R2 scoping questions (§11)
**Governing spec:** `docs/spec/spec_v0.5.md` §13 (R2 is marked *proposed* there). Where this brief and the spec disagree, stop and ask.
**Builds on:** R0–R1 (complete; `docs/memos/R0-result.md`, `docs/memos/R1-result.md`). The R0–R1 brief's ground rules, testing standards and conventions continue to apply unless this brief changes them.

---

## 0. Ground rules for R2

1. **Scope is R2 only.** Do not build R3–R6: no causal search, event ordering or opening books (R3); no policy optimizer, conditional entries or MultiPV (R4); no neural or hybrid models (R5–R6). Leave clean seams for them; don't stub them out in detail.
2. **Stage process is unchanged.** Every material choice gets a DR (status *proposed* until Josh confirms). Each stop point gets a result memo, a Codex review brief, and Josh's explicit confirmation.
3. **Pre-registration comes first, as its own gate (B01).** Per spec §1, **EVAL-2** is written and **approved by Josh before any empirical R2 model output exists to look at**: no factor fit, VRP estimate, forecast or replay result. Until that approval, only data-coverage checks (R2.0) and synthetic-data tests are allowed. Stop Point D confirms the order was kept.
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
| Exit rule | **Profit target plus time stop:** exit when the position's value reaches 2× the premium paid, or with 7 calendar days left, whichever comes first; otherwise EXP-1. ASSIGN-1 still applies. **Timing (B06, Josh 2026-11-21):**<ul><li>Live: checked on the 10:00 and 15:45 ET decision boards, sold at the execution quote, unfilled exits retried at later boards.</li><li>Paths step at the same two times a day; a target seen at one step sells at the next step's price, standing in for the execution delay.</li><li>"Premium paid" is the actual entry fill including fees, and the target is valued at the bid.</li><li>The time stop fires at the first decision board with 7 or fewer calendar days left; an entry already inside 7 days is not eligible.</li></ul> |
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
| P2 | MKT-1 and RAND-1 in R2 | **v4** = DR-0041 eligibility + DR-0042 `ADJ-1` ranking, still risk-neutral and keeping DR-0037's mid-anchored mean. The VRP version is a *separate* engine (`MKTV-1`), so MKT-1 stays the pure market-implied reference. **`MKTV-1` (B03, Josh 2026-11-21):**<ul><li>It simulates its own paths. The terminal distribution is MKT-1's risk-neutral density with VRP-scaled variance (same forward, positive support), and IV is marked down by the same ratio along the path.</li><li>Its mean comes from valuing EXIT-1 on those paths, with **no** re-centring on the quoted mids.</li><li>A DR fixes the daily transition, the IV convention, normalization and the fallback before any path work depends on it.</li><li>The +5% bug-signal exclusion applies only where the DR justifies it.</li></ul> |
| P3 | Named factors | Market (SPY); the 11 sector SPDRs, as sector factors after removing the market; rates (daily change in the 10-year Treasury yield); volatility (daily log change in VIX). Each name loads on the market, its own sector, rates and volatility. |
| P4 | PCA residual | Components on the residual correlation after the named factors, chosen at each monthly refit by the **Kaiser rule** (eigenvalue above the average), **at most 5**. The count used is recorded with each fit. |
| P5 | Shock distribution | Filtered historical simulation: GARCH(1,1) per factor, with standardized residuals bootstrapped jointly by date, so cross-factor dependence and fat tails are kept. Idiosyncratic residuals are bootstrapped by name. |
| P6 | IV level | Each name's 30-day ATM IV log change = β_vol × VIX log change + β_spot × the name's own log return (the spot-vol, or leverage, effect) + its own residual, fitted on the same 3 years. Option values along a path use Black-76 at the simulated forward, with IV = simulated level × the decision board's fixed smile shape in log-moneyness ("sticky moneyness"). |
| P7 | Exit fills on paths | A simulated exit sells at the modelled mid minus the decision board's relative half-spread for that contract. Live exits use FILL-1 at the execution quote, as in R1. |
| P8 | Paths and time | 2,000 joint paths per decision board, stepping twice a day at the decision times (B06). **Budget:** all R2 engines must finish inside the 5-minute decision-to-execution window on the Mac mini. If not, a DR reduces paths (never below 500) or checks every few days instead, never the window. |
| P9 | VRP estimate | **Regime-dependent.** Per name, over the rolling 3 years: the ratio of realized 30-day variance to the 30-day implied variance at its start, modelled as *a + b × VIX at the start*, with a and b shrunk toward the sector's, and the ratio clipped to [0.3, 1.5]. `MKTV-1` scales the risk-neutral distribution's variance by that ratio around the same forward. |
| P10 | `HVOL-1` | A lognormal at each name's trailing 60-day realized volatility, drifting at the risk-free rate. It ranks long options by `ADJ-1` with `EXIT-1`, valued on its own paths. |
| P11 | Long-option comparison set | FAC-1, MKTV-1, HVOL-1 and **`RAND-L1`** (seeded random long options, its forecasts from FAC-1 as RAND-1's come from MKT-1) all select long options with `EXIT-1` under the same caps, so EVAL-2 compares them paired, on the same boards. RAND-L1 is the selection-skill control. MKT-1 v4 and RAND-1 v4 keep the full structure set with HOLD-1. |
| P12 | Close store | `CLOSE-1`: the nightly step saves the official close of every universe name, SPY, the sector SPDRs and VIX once it's public, write-once under `data/closes/`. **A one-off backfill** covers every past date the records need (live from 11 November 2026, plus the replay months), each close stamped with its real public time. F09 and R2 score only from the store; report-time fetching only fills gaps. This amends DR-0040. |
| P13 | History store | `HIST-1`: daily closes and 30-day ATM IV per name, plus the factor series, for at least 3 years before the replay's first month, stored write-once with manifests. **ATM IV is derived from ThetaData's end-of-day option quotes** with the same parity forward and Black-76 as MKT-1, interpolated to 30 days, not taken from a vendor IV field. |
| P14 | Live run for stop point F | 20 consecutive trading days with all R2 engines recording, with no manual intervention. |

---

## 3. Data (R2.0)

- **The replay window (B11, Josh 2026-11-21):** decisions from **November 2025 to October 2026**, with a tail until every position closes, and history back to **November 2022**. Coverage is for the **union of every point-in-time monthly member and held name** over that period, not today's ~50 names, so no survivorship bias.
- **Verification spike first, as R0.1 was.** Confirm ThetaData (and Massive where needed) can supply, point-in-time:
  - 3+ years of daily official closes for the universe candidates, SPY, the sector SPDRs and VIX;
  - daily 30-day ATM implied volatility per name, or EOD option quotes to derive it;
  - the 10-year Treasury yield.
  
  - For the replay itself (B11): option quotes **with displayed sizes** at both daily decision and execution times, UNIV-1's selection inputs, rates, corporate actions, and closes after the last month for settlement.

  Record a **coverage matrix**, gaps, storage and runtime estimates, and the license terms in a memo. **Josh decides go or rescope** before any model work depends on the data.
- **Build `HIST-1` and `CLOSE-1`** (P12, P13). Every series is stamped with its public time, so the view refuses anything not yet public.
- **Text archive (DR-0036):** start collection only (EDGAR, Massive ticker news, Fed/Treasury releases), write-once, with no model use in R2.

---

## 4. Tasks

### Part 1: foundations → **STOP POINT D**
- **R2.0:** the data verification spike and go/no-go (B11); `HIST-1`, `CLOSE-1` and the text-archive collector; an early synthetic compute profile (B13).
- **R2.1, EVAL-2 (approved by Josh before any empirical R2 output exists, B01):**
  - the execution-price measure alongside EVAL-1's;
  - the paired long-option comparison (FAC-1 vs MKTV-1 vs HVOL-1, with RAND-L1 as the selection control), with the statistic fully defined (B07): the primary contrasts and weights, the comparison opportunity set, null/unfilled/failed-run handling, coverage rules, resampling units, the multiplicity family and the analysis dates. Returns conditional on a fill are kept apart from any all-opportunity measure, and no trades are invented for nulls;
  - **the common-contract prediction tournament (B08; spec §11 and EVAL-1 §4):** a deterministic, point-in-time panel of long contracts per board, independent of any engine's picks. Each engine records its EXIT-1 return forecast for every panel contract before the outcome, including on null boards, and the panel's outcomes are observed under the same EXIT-1 and FILL-1 conventions;
  - S_T/F and IV-level forecast scores for FAC-1, labelled as physical forecasts, separate from MKT-1's risk-neutral ones. IV forecasts get their own prospective records (B09): decision and target times, horizon, distribution grid, units and provenance, with the target ATM-IV construction and gap rules defined here;
  - updated power estimates for the paired comparison;
  - the confirmatory schedule. The 20-day live run (F) is an **operational gate with descriptive results only**, because no new-engine trade is mature under 61-day censoring by then (B07). The replay's dates are fixed in advance, and any model change after its results are seen is a new version, labelled as such.
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
- **The performance budget (P8)** is a test that runs on a full-size synthetic board, marked slow. At E there is also a full-pipeline timing and failure-isolation rehearsal (B13), with realistic candidate and held-name counts.
- **VRP labels (B02):** changing every return after the fit cutoff leaves the fit unchanged.
- **Factor model (B04):** known-process tests for the conditional means and the daily GARCH filter updates, not just for loadings.
- **Path valuation (B05):** deterministic roll-down and known dividend dates, as well as time zero.
- **EXIT-1 (B06):** a crossing between observations, a reversal before execution, a holiday or weekend time stop, and an unfilled first exit.
- **Corporate actions (B10):** synthetic split, dividend and ticker-change fixtures, including an action after the fit cutoff that must not affect that fit.
- **Coverage and fits (B12):** a new member, a missing-IV block, a failed refit and an expired stale fit, with the other scheduler steps continuing.
- **Cut-over (B14):** a rehearsal with open v3 positions, pending exits and held-only names, including a restart mid-slot.

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

## 12. Contracts required by the plan review (Codex, 2026-11-21; `docs/review/R2-brief-findings.md`)

Each is written into the named task's DR, and approved, before anything that depends on it is built.

| Finding | Contract |
|---|---|
| B02, VRP labels (R2.3) | A regression row is admitted only once its whole realized-variance window has ended and is public at the fit cutoff. The 3-year window applies to mature response dates. Window lengths in trading days, annualization and variance units are defined. Mature-row counts, overlap-aware uncertainty and the clipping frequency are reported. |
| B04, factor means and state (R2.4–R2.5) | The DR states intercepts and conditional means, shock centring, log-to-price conversion and GARCH initialization. Parameters are refitted monthly, but the GARCH state is filtered daily with each newly public return. The latest observation usable at an OPEN and at a CLOSE board is named. |
| B05, forwards on paths (R2.5) | Each node's forward and discount come from the decision board's frozen carry and rate curve (the R1 rates, plus known dividends), rolled down to the remaining maturity. The simulated 30-day IV level is normalized to each expiration's frozen smile. Time-zero repricing has a stated tolerance, and an expiration that fails it is rejected and flagged. |
| B10, corporate actions (R2.0, HIST-1) | Raw observations are kept, and returns are derived from them with as-of adjustments that use only the corporate actions public at the fit date, each recorded with its evidence. Dividend treatment and symbol mapping are defined, and unsupported action intervals are rejected. Backfills keep their real public and ingestion times. |
| B12, coverage and failed fits (R2.4, R2.8) | Before the replay, freeze: minimum history and common-date rules, missing-data treatment, fit-acceptance checks, the fallback order, and the maximum age of a fit. There is no silent shortened history. Exclusion and failure counts, fit vintages and the effective comparison population are recorded per board. |
| B13, compute (R2.0, E) | The timed work is defined: movegen plus every engine plus the registry writes, inside 5 minutes. Monthly refits and archive collection run outside the window, and execution capture keeps priority. A cadence change made for speed is a versioned policy change, made before it affects any forecast. |
| B14, cut-over (R2.10) | Existing trades keep their versions and HOLD-1; registries, run logs and legacy provenance are untouched; versions are reported as separate cohorts. EVAL-1's prospective start date and 12-month schedule are unchanged. A new-engine failure never blocks managing or settling existing positions. |

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

**Plan-review decisions (2026-11-21):**
- B01: EVAL-2 approved first, as its own gate.
- B03: MKTV-1 on its own paths, with no mid re-centring.
- B06: the paths simulate the live twice-daily cadence.
- B11: the replay covers November 2025 to October 2026, with a go/no-go gate.
