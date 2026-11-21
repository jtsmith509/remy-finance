# R2 build brief: plan review findings and resolution

- **Reviewer:** Codex (independent), following `docs/review/R2-brief-review-brief.md`
- **Reviewed:** `docs/handoff/HANDOFF_R2.md` at `5d55337`
- **Verdict:** **approve with changes.** 14 findings: 12 major and 2 minor, none blockers.
- **Resolution:** all 14 are folded into the brief (2026-11-21). Josh decided the four that involved a choice; the other ten become required contracts in the brief (§12) or added tests (§5).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| B01 | major | EVAL-2 was frozen after empirical model output existed | **Josh:** EVAL-2 is approved first, as its own gate, before any empirical fit, forecast or replay. Until then, only data-coverage checks and synthetic tests (§0.3, R2.1). |
| B02 | major | VRP training labels could use post-cutoff returns | A row is admitted only once its whole realized window is mature and public; window, unit and diagnostic definitions; a post-cutoff invariance test (§12, §5) |
| B03 | major | MKTV-1 had no EXIT-1 path construction, and mid re-centring would erase the VRP | **Josh:** MKTV-1 simulates its own paths (VRP-scaled risk-neutral density, IV marked down by the same ratio), with no mid re-centring. A DR defines it before path work (P2). |
| B04 | major | FAC-1's conditional means and daily filter state were undefined | DR contract: means, centring, GARCH initialization, monthly fits with daily state filtering, the last usable observation per board (§12, §5) |
| B05 | major | Path option values lacked forwards and discounting | Frozen decision-board carry and rate curve rolled down; smile normalization per expiration; a repricing tolerance and reject flag (§12, §5) |
| B06 | major | Simulated and live EXIT-1 saw different stopping times | **Josh:** paths step at the live 10:00 and 15:45 cadence and sell at the next step. Entry-fill basis, bid valuation, the time-stop boundary and retries are defined (§1, §5). |
| B07 | major | "Paired" didn't define the comparison statistic | EVAL-2 freezes the contrasts, weights, opportunity set, null and fill handling, resampling units, multiplicity and dates. F is an operational, descriptive gate. Replay dates are fixed in advance (R2.1). |
| B08 | major | The common fixed-contract tournament (spec §11) was missing | Added: a point-in-time common long-contract panel per board, with every engine's forecasts recorded before outcomes (R2.1) |
| B09 | major | IV forecast scores had no stored predictions or outcomes | Added prospective IV forecast records, with the target construction defined in EVAL-2. Physical forecasts are labelled apart from risk-neutral ones (R2.1). |
| B10 | major | HIST-1 had no as-of corporate-action convention | Raw observations kept, with as-of adjustments that use only actions public at the fit date, plus their evidence; a post-cutoff action test (§12, §5) |
| B11 | major | The data spike didn't cover the replay | **Josh:** the replay runs November 2025 to October 2026, with history from November 2022, the full point-in-time member and held-name union, a coverage matrix, and a go/no-go gate (§3) |
| B12 | major | No frozen behaviour for coverage gaps and failed fits | Eligibility, missing-data, acceptance, fallback and maximum fit age are frozen before the replay, with counts recorded per board (§12, §5) |
| B13 | minor | The compute gate needed an operational boundary and an early check | An early synthetic profile in R2.0, the timed workload defined, and a full-pipeline rehearsal at E. Note that B06's twice-daily steps raise the compute cost (§12, §5). |
| B14 | minor | The cut-over didn't explicitly protect R1 trades or EVAL-1's clock | Cut-over invariants and a rehearsal with open v3 positions (§12, §5) |

**One detail I chose without asking:** B06 asked what price basis the profit target uses. I wrote the bid, consistent with FILL-1 selling at the bid. Change it if you prefer the mid.

---

## The reviewer's report (as delivered)

# R2 build brief — independent plan review

**Overall verdict: approve with changes.** The settled R2 scope and Josh's model choices can stand. Resolve the major findings below in the brief, or require the named definitions as approved prerequisites to dependent work. In particular, freeze EVAL-2 before inspecting empirical model outputs, and specify the missing benchmark, execution and forecast-record contracts before generating replay results.

Reviewed `docs/handoff/HANDOFF_R2.md` at **5d553375f9d42f9d7283669dae117a3245cef55a**, following `docs/review/R2-brief-review-brief.md`. References below are repository-relative documents at that commit. This is a plan review, not a claim about implemented R2 defects. No recommendation below reopens the accepted factor selection, GARCH/FHS family, Kaiser rule, three-year history, fixed smile dynamics, P7 spread approximation, ADJ-1, or v3-to-v4 replacement merely as a preference.

## B01 — EVAL-2 approval is scheduled after empirical R2 outputs become available

**Severity:** major.  
**Brief section:** §0.3; §4 Part 1, R2.1–R2.4; §7 Stop D; §9 D.

**Problem:** The ground rule requires freezing scoring before *any R2 result is examined*, but EVAL-2 is frozen at D, after the VRP estimator and factor fits have been built and their empirical diagnostics prepared for review. Listing R2.1 first does not impose an approval dependency. The owner could choose scoring rules after seeing those outputs while still apparently following the stop-point sequence. That would contradict spec §1 and the brief's own rule, even if the walk-forward replay has not started.

**Suggested change:** Make the approved EVAL-2 commit a hard prerequisite to inspecting R2.3/R2.4 empirical results. D can confirm that this earlier gate was satisfied. Explicitly distinguish permissible data-coverage checks and synthetic feasibility tests from empirical model diagnostics, candidate forecasts and realized performance. State which definitions must already be frozen, including forecast targets and the primary comparison population.

## B02 — The VRP regression does not specify when a training label becomes usable

**Severity:** major.  
**Brief section:** §2 P9; §0.4; §5 no-look-ahead and VRP tests.

**Problem:** A VRP row starts with IV and VIX at date s, but its realized-variance response uses the following 30 days. Restricting start-date observations to the rolling history does not prevent a regression row from using returns after its refit cutoff. For example, a row starting shortly before a monthly refit has historical predictors but a future response. A generic test that requests a future observation does not necessarily catch an estimator that constructs its labels from a larger preloaded array. Overlapping windows also make the daily row count a poor measure of information about the regime slope.

**Suggested change:** Admit a row only after its entire realized window has ended and all constituent observations are public at the fit cutoff. Define calendar versus trading-day windows, annualization and implied/realized variance units, and whether the three-year boundary applies to predictor dates or mature response dates. Require a synthetic test in which changing every post-cutoff return leaves the fit unchanged. Add mature-row counts, overlap-aware uncertainty and clipping frequency to D's diagnostics; keep the accepted linear-in-VIX and sector-shrinkage choices.

## B03 — MKTV-1 has no complete EXIT-1 forecast construction

**Severity:** major.  
**Brief section:** §2 P2, P9–P11; §4 R2.3, R2.5–R2.7.

**Problem:** Scaling a terminal risk-neutral distribution's variance around its forward does not define joint daily spot paths, intermediate IV or profit-target first passage. Processes with identical expiration distributions can have different EXIT-1 returns. There is also an unresolved inheritance issue: DR-0037 anchors MKT-1's mean to quoted-mid carry and shifts its return distribution to that mean. Reusing that step in MKTV-1 can erase the effect the VRP adjustment is supposed to have on expected returns and selection. HVOL-1 specifies a spot lognormal but not the IV process used to price its early exits.

**Suggested change:** Require an approved benchmark-construction DR before path-engine work depends on MKTV-1/HVOL-1. Specify their daily transition and intermediate-IV conventions, cross-horizon consistency, and how their EXIT-1 return distributions are computed. Define the VRP transformation precisely, including positive support, normalization, forward preservation, clipping and shrinkage/fallback rules. State that pure MKT-1 retains DR-0037, while the adjusted benchmark's mean comes from its specified policy valuation rather than an inherited risk-neutral recentering. Explicitly scope the inherited +5% bug exclusion to the engines for which it remains justified. Add analytical/synthetic checks of these distinctions.

## B04 — FAC-1's conditional return means and daily filter state are undefined

**Severity:** major.  
**Brief section:** §2 P3–P5; §4 R2.4–R2.5; §5 factor/path tests.

**Problem:** Loadings, PCA, conditional variances and bootstrapped shocks do not determine expected price returns. Zero log-return drift, historical sample means and a risk-free arithmetic drift produce materially different option rankings. “The drift is risk-free where it's meant to be” does not identify FAC-1's mean equations. Monthly parameter refits also do not say whether the GARCH state is updated with each newly public daily return or remains frozen for a month, nor how an OPEN versus CLOSE board initializes the path.

**Suggested change:** Make the factor-model DR specify factor/name intercepts and conditional means, shock centering, log-to-price conversion, GARCH initialization, and the distinction between monthly parameter fitting and daily state filtering. Name the latest usable observation at each decision cadence. Require known-process tests for conditional means and filter updates, not only loadings/covariance recovery. This fills in the chosen model; it need not impose any particular drift preference.

## B05 — Path option values lack a defined remaining-maturity forward and discount curve

**Severity:** major.  
**Brief section:** §2 P6; §5 option valuation on paths.

**Problem:** Black-76 needs a forward and discount factor for each remaining maturity at each future node. Simulated spot plus an ATM-IV level does not supply those quantities. The named ten-year yield shock does not itself define the short-rate curve or dividend/carry evolution. Choosing spot as forward, freezing the original expiration forward, and rolling a fixed carry curve yield different puts/calls and target-hit times. The single 30-day IV level also needs an explicit normalization against each expiration's frozen smile/term level to make the time-zero repricing test meaningful.

**Suggested change:** Specify the spot-to-forward mapping, curve/discounting convention, known-dividend treatment and fixed per-expiration smile normalization used along paths. A documented frozen decision-curve/carry approximation is sufficient for R2; this does not require full surface or rate-curve dynamics. Define the initial repricing tolerance and rejection/flag behavior, and test deterministic roll-down and known dividend dates as well as time zero.

## B06 — Simulated and live EXIT-1 observe different stopping opportunities

**Severity:** major.  
**Brief section:** §1 exit rule; §2 P7–P8; §4 R2.6; §5 EXIT-1 tests.

**Problem:** Paths check the profit target at each daily close. The inherited live process observes decision boards at 10:00 and 15:45 ET and uses later execution quotes (DR-0002/0003/0033); the brief adds no official-close decision capture. A target reached only in the morning can trigger live but not on paths, and a target reached only at the close can trigger on paths but not live. Even a trigger seen on a decision board need not survive until execution. The target's valuation basis and whether “premium paid” means the actual entry fill are also unspecified. These differences affect the forecast being scored, independently of the settled P7 half-spread approximation.

**Suggested change:** Require an explicit observation/submission/execution timing contract and explain how the daily path approximation represents it. Define target price basis, entry-cost basis, the seven-calendar-day boundary on non-sessions, behavior for entries already at that boundary, and unfilled-exit retries. Resolve any cadence amendment through the stated DR process without silently changing the existing scheduler or frozen fills. Add fixtures for a between-observation crossing, reversal before execution, a holiday/weekend time stop and an unfilled first exit. Label residual forecast/live timing approximations.

## B07 — “Paired, on the same boards” does not define the comparison statistic

**Severity:** major.  
**Brief section:** §2 P11, P14; §4 R2.1/R2.8/R2.9; §7–§9 E/F.

**Problem:** The engines can choose different contracts and expirations, have different fill rates, lack coverage or make null decisions. Thus their selected trades are not one-to-one pairs. Pooling filled trades, averaging board portfolios and restricting to jointly successful runs estimate different quantities; equal caps do not resolve this. Updating power from trade counts without specifying this statistic and shared-shock dependence is not meaningful. The F run is only 20 trading days: under inherited 61-day censoring it has no mature new-engine return cohort, so it cannot serve as a performance-validation gate.

**Suggested change:** Require EVAL-2 to freeze the primary contrasts, weights, comparison opportunity set, null/unfilled/failed-run treatment, coverage rules, dependence/resampling units, multiplicity family and analysis dates. Distinguish conditional-on-fill returns from any all-opportunity decision-value measure; do not invent trades for nulls. Update power for that actual statistic and disclose the replay's limited independent information. State that F is an operational gate with descriptive results, preserving the later confirmatory schedule. Prespecify replay dates and retain/label results and model revisions after those outcomes are inspected; rerunning a tuned replay does not create untouched validation.

## B08 — The common fixed-contract prediction tournament is absent

**Severity:** major.  
**Brief section:** §2 P11; §4 R2.1/R2.7/R2.9.

**Problem:** The draft covers independently selected portfolios but not forecasts for a common fixed set of contracts. Spec §11 and inherited EVAL-1 §4 explicitly require both tournament modes. Forecast scores on each engine's own selections confound prediction quality with contract selection. RAND-L1 sharing FAC-1 forecasts is a valid selection control, but it does not provide an independent forecasting benchmark or supply the missing common-contract arm.

**Suggested change:** Add a deterministic, point-in-time common long-contract panel, independent of each engine's realized success or chosen trades. Persist the relevant engines' policy-return predictions on that panel before outcomes, including on null recommendation boards, with defined missing-forecast/coverage handling. Separate this prediction comparison from own-portfolio performance, and label RAND-L1 as the selection control. Specify how common-panel outcomes are observed under the same frozen EXIT-1/FILL-1 conventions.

## B09 — IV forecast scores have neither stored predictions nor defined outcomes

**Severity:** major.  
**Brief section:** §4 R2.1 versus R2.7/R2.9; §2 P6/P13.

**Problem:** R2.1 promises IV-level forecast scores, but R2.7 records only strategy-return distributions and S_T/F quantiles. It does not preserve a predictive IV distribution. Nor does it define the target horizons, target IV derivation/availability, or missing-target policy. Reconstructing a forecast after the outcome from a later implementation or refit cannot substitute for its frozen original record.

**Suggested change:** Add prospective IV predictive records with decision and target timestamps, horizons, distribution/grid, fit/artifact/seed/code provenance, and units. Define target ATM-IV construction, public-time and gap rules, proper scores and calibration checks in EVAL-2. Require schema compatibility and replay reproducibility tests, preserving DR-0043's legacy handling. Label FAC-1's physical underlying forecasts separately from risk-neutral MKT-1 forecasts rather than treating their expected miscalibration identically.

## B10 — HIST-1 has no as-of price-return convention across corporate actions

**Severity:** major.  
**Brief section:** §2 P3/P5/P6/P9/P10/P13; §3 HIST-1; §5.

**Problem:** Raw official closes make a split look like a large economic return, distorting GARCH, realized variance, HVOL and the spot-vol fit. Conversely, a vendor's currently adjusted series can embed actions not yet known at a historical refit. Ordinary dividends, special dividends and ticker changes also affect return and identity continuity. Existing action sourcing and contract-exclusion rules do not define a three-year fitting series. DR-0021 specifically makes split information available only on execution day; write-once files alone do not establish an as-of adjustment convention.

**Suggested change:** Define raw versus derived price/return series, dividend treatment, symbol/entity mapping, and as-of corporate-action transformations. Preserve raw observations and the transformation's action evidence/version so a future action cannot rewrite a previously frozen fit. Keep forward/IV and realized-variance units consistent and specify supported versus rejected action intervals. Add synthetic split, dividend and ticker-change fixtures, including an action after the fit cutoff that cannot affect that fit. Preserve real public/ingestion provenance on backfills rather than equating write-once acquisition with historical availability.

## B11 — The data spike does not establish feasibility of the required replay

**Severity:** major.  
**Brief section:** §2 P12–P13; §3 verification spike; §4 R2.0/R2.8.

**Problem:** The spike lists EOD closes, ATM IV and factors. A 12-month full-pipeline replay additionally needs decision/execution option quotes with displayed sizes, historical universe-selection inputs, rates/actions and management/settlement data after the final decision month. The August R1 replay verifies neither access nor coverage for an unspecified year. The history population is the union of point-in-time monthly members and held names, not just today's approximately 50 names; otherwise coverage choices can introduce survivorship bias. Three years of prehistory are needed before the *first* replay fit.

**Suggested change:** Name the replay and tail intervals and extend R2.0 to a data-contract coverage matrix for their full historical membership union. Include quote/size availability, universe inputs, IV-bracketing expirations, factors, actions, rates and post-period outcomes. Record request/storage/runtime estimates and licensing/access constraints, with an explicit early go/no-go or owner-approved scope-change gate before dependent model construction. Separate lawful data verification at build time from this offline review; no current vendor capability is assumed here.

## B12 — Coverage gaps and failed monthly fits have no frozen behavior

**Severity:** major.  
**Brief section:** §2 P4–P6/P9/P13; §4 R2.4/R2.8; §7–§8.

**Problem:** “The verification spike decides which names FAC-1 can cover” is not a per-board eligibility rule. A rebalance entrant can lack the required history; gaps can break jointly dated shock vectors/PCA; a GARCH or IV fit can fail or become nonfinite. Skipping, retaining an old fit, substituting a model or aborting the engine changes forecasts and the paired population. Choosing among them after replay outcomes are visible permits outcome-dependent exclusions despite the generic no-look-ahead rule.

**Suggested change:** Freeze minimum history/common-date requirements, missing-data treatment, fit-acceptance checks, fallback hierarchy and maximum artifact age before replay. Any departure from the settled three-year model needs its own explicit approval, not a silent shortened-history fallback. Record exclusion/failure counts, fit vintages and effective comparison populations per board; never silently relabel one model's output as another. Test a new member, a missing-IV block, a failed refit and stale-fit expiry, with independent scheduler steps continuing safely.

## B13 — The compute gate needs an operational boundary and an earlier feasibility check

**Severity:** minor.  
**Brief section:** §2 P8; §3 R2.0 spike; §5 performance test; §7 E.

**Problem:** The brief sets the correct five-minute requirement, but does not define the timed work or demonstrate feasibility until E. A synthetic path-kernel benchmark can miss movegen, all engines, serialization/registry writes, held-name growth, monthly refits and archive work sharing the scheduler. R1 already spent roughly 40–60 seconds on recommendations and experienced capture latency (memo §5). This does not prove R2 is too slow, but it makes an isolated timing result insufficient evidence that execution captures and existing-position management remain timely.

**Suggested change:** Add an early synthetic operation-count/profile spike and define the complete timed recommendation workload and hard deadline on the Mac mini. State which fitting/archive work is outside the critical window and how execution capture retains priority. At E require a full-pipeline timing/failure-isolation rehearsal with realistic candidate and held-name counts, not only a numerical kernel. Keep P8's approved fallback choices, but record a changed observation/check cadence as a versioned policy change before affected forecasts, not as an invisible performance optimization.

## B14 — Cut-over acceptance does not explicitly protect outstanding R1 trades and its analysis clock

**Severity:** minor.  
**Brief section:** §0.5; §4 R2.2/R2.10; §7 F.

**Problem:** The announced cut-over is accepted, but no acceptance fixture covers old open trades alongside new engines or a partially completed slot. Hash-chain verification alone cannot establish that v3 positions retain HOLD-1, continue to receive held-name boards and settle correctly. Nor does the cut-over text explicitly preserve EVAL-1's original prospective start date and 12-month analysis schedule while reporting versions separately. A new-engine failure must not prevent legacy trade management.

**Suggested change:** Make these invariants explicit acceptance criteria for the cut-over plan: no policy/version mutation of existing trades, preserved registries/run logs/legacy provenance, separate version cohorts, unchanged EVAL-1 clock, continued management/settlement and per-engine idempotency/failure isolation. Rehearse cut-over and restart with open v3 positions, pending exits and held-only names. Stopping new v3 recommendations and starting v4 remains as approved; this does not require parallel new v3 issuance.
