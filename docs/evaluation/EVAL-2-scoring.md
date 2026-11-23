# EVAL-2: pre-registered scoring protocol for R2

- **Status:** **revised draft for Josh's approval** (revision 3, 2026-11-23).
  - It folds in Codex's review (E01–E12) and a pre-freeze adversarial check (36 verified issues), both recorded in `docs/review/EVAL-2-findings.md`, plus Josh's decisions.
  - It is frozen in git at approval, *before any empirical R2 model output exists* (R2 brief §0.3, B01).
- **Extends:** EVAL-1 (`docs/evaluation/EVAL-1-scoring.md`, frozen). Everything in EVAL-1 still applies to MKT-1 and RAND-1, including P1 and P2 on their own clock (EVAL-1 §5), except as amended here.
- **P1 and P2 cohort (Josh):** at every EVAL-1 anniversary, P1 and P2 are tested only on the **v3 cohort**, meaning decisions before the R2 cut-over slot (§4.1), under EVAL-1 §1.4 censoring.
  - MKT-1 v4 and RAND-1 v4 get EVAL-1's metrics descriptively, as a separate cohort, never pooled with v3 (EVAL-1 §7).
  - A v4 P1/P2 is confirmatory only if an EVAL revision registers it before the cut-over.
- **Decisions:** Josh, 2026-11-23 (§11).

## 1. Scope, eligibility and version tuple

| Engine | Population | Exit | Role |
|---|---|---|---|
| MKT-1 v4, RAND-1 v4 | all MOVEGEN-2 structures (DR-0041 eligibility) | HOLD-1 | R1's baselines continued, scored descriptively by EVAL-1's metrics |
| **FAC-1** | long calls and puts | EXIT-1 | the R2 factor engine; ranks by `ADJ-1` |
| **MKTV-1** | long calls and puts | EXIT-1 | VRP-adjusted market benchmark; ranks by `ADJ-1` |
| **HVOL-1** | long calls and puts | EXIT-1 | historical-volatility benchmark; ranks by `ADJ-1` |
| **RAND-L1** | long calls and puts | EXIT-1 | selection control: FAC-1's forecasts, random picks (§3.1) |

- **The eligible set,** identical for all four long-option engines on a board, is built **once, before any engine-specific filtering**.
  - **Base population:** the single-leg long-call and long-put candidates that MOVEGEN-2 (at its cut-over version) generates from the decision board, after movegen's own strike band and priceability filter.
  - Candidates on held-only or tracked-only underlyings (DR-0039, §6.3) are removed first.
  - It keeps candidates that satisfy **all** of:
    - DR-0041's rule;
    - DR-0044's 3-year minimum history;
    - EXIT-1's entry boundary: more than 7 and at most 60 calendar days to expiry, counted from the decision session's ET date.
- **Eligible names and expirations** (used in §6 and §7): on a board, an *eligible name* is an underlying with at least one contract in the eligible set. An *eligible expiration* is an expiration of an eligible name with at least one contract in that set. The panel (§6), S_T/F (§7.1) and IV (§7.2) coverage sets all use this one population per board.
- **Caps:** SELECT-1 (5 picks per board, one per underlying per day).
- **The tested version tuple** (E05): P3 and P4 test **FAC-1, MKTV-1 and RAND-L1 at the versions active at the cut-over**.
  - **What's a new version:** a change of method, of any parameter outside the scheduled monthly refit, or of code, if it can alter that engine's forecasts, eligible set, ranking, selection, caps, fill, exit or settlement behaviour, or its recorded returns. This includes shared code: eligibility, `ADJ-1`, SELECT-1, FILL-1 and EXIT-1.
  - **Deciding it:** whether a change is of this kind is decided **before deployment**, in a DR recorded before the change's first prospective record. The DR names its evidence, for example identical recorded outputs from old and new code on a stated set of stored boards. A change without such a DR is treated as a new version.
  - **Not a new version:** scheduled monthly refits under unchanged code, and changes to logging or reporting that can't affect any recorded decision or outcome.
- **A version change during a 12-month window:** if an engine in a contrast changes version before an anniversary, that contrast's enrolment window ends at the last scheduled slot before the new version's effective slot (recorded in its DR), or at the §4.1 enrolment end if earlier.
  - Everything in §4–§5 is computed only on that truncated window: W, K, S_t and n_t, the §4.3 denominator, reason counts and the coverage rule. Later weeks are excluded, not kept as empty weeks. A week the truncation splits keeps only its in-window slots, and still counts as one week.
  - The contrast is tested if the truncated cohort meets §5's minimum-information rule; otherwise it is inconclusive (p = 1 in Holm).
  - Later anniversaries use the same truncated window, gaining only boards whose picks have since closed.
  - Other version pairs are reported descriptively unless registered in an EVAL revision **before** their first record. A replacement pair is never chosen after results are seen.
- **Inherited from EVAL-1 unchanged:**
  - §1.4: censoring at 61 days;
  - §1.7: no exclusions after the fact, and known defects annotated and reported with and without (§4.4);
  - §7: integrity and no silent pooling across versions.

## 2. Return measures

- **Primary:** EVAL-1's net return on capital at risk at the **decision price**, so slippage between decision and fill counts as a cost (Josh, question 3).
- **Secondary (descriptive):** the same net P&L over capital at risk at the **execution price**, the entry actually paid. It is labelled "execution-price" wherever it appears.
- **Values:** both use the cutoff state's **corrected** values (§4.1), with original values reported as a sensitivity (§4.4).

## 3. Confirmatory hypotheses (R2 family)

| ID | Null hypothesis | Statistic (§4) |
|---|---|---|
| **P3** | The board-weighted mean difference in decision-price returns, FAC-1 minus MKTV-1, over the contrast's mature shared boards, is 0 | T̂ (§4.2) |
| **P4** | The same for FAC-1 minus RAND-L1 | T̂ (§4.2) |

- **What P3 and P4 measure (E02).** They test returns **conditional on inclusion**: boards where both arms filled at least one pick, and every filled pick of both arms is closed. They do **not** establish superiority over all decision opportunities, and they don't isolate the forecast model from execution. Each arm's recommendations, fills, shortfalls, nulls, failed runs and net dollar P&L over every scheduled slot are reported descriptively beside them (§4.3). Unfilled opportunities contribute no trade return.
- **Two-sided tests,** with **Holm–Bonferroni across P3 and P4** at a family-wise **α = 0.05 at each anniversary**. This family is separate from EVAL-1's P1–P2 (Josh, question 7).
- **Across anniversaries (E06; Josh):** each anniversary's analysis stands alone. Holm controls the family *at that look*; there's **no across-anniversary error guarantee**, and the chance of ever declaring a false difference grows with repeated looks. Every report shows all earlier looks. An inconclusive contrast (§4.3, §5, §1) takes its Holm slot as a non-rejection with p = 1. A two-sided rejection in the negative direction is evidence in the comparator's favour.
- **Descriptive only:** every HVOL-1 comparison, every forecast score (§6, §7), the execution-price measure, and every comparison involving MKT-1 v4 or RAND-1 v4, including EVAL-1's metrics on the v4 cohort.
- **Amends EVAL-1 §4, the tournament for later engines** (R2 brief P2, P11):
  - For R2's long-option engines, **MKTV-1 and RAND-L1 take the roles of MKT-1 and RAND-1** in both tournament modes: (a) the own-portfolio comparison is P3/P4 (§3, §4); (b) the common-contract comparison is §6.
  - FAC-1's own-portfolio returns are also reported against MKT-1 v4 and RAND-1 v4 on identical snapshots, **descriptively only**, because those baselines trade a different population with HOLD-1.
  - MKT-1 v4 and RAND-1 v4 are not in the §6 panel (they record no EXIT-1 forecasts). MKT-1's fit is used there only to choose delta cells.

### 3.1 RAND-L1's sampling rule (E03; Josh)
- **The pool:** the §1 eligible set, restricted to contracts with a **valid FAC-1 forecast**, after SELECT-1's same-day exclusion of underlyings RAND-L1 itself picked earlier that day.
  - **A valid FAC-1 forecast** is one recorded in FAC-1's run log for that board, frozen before the execution quote, with no missing or failure flag. All 15 EVAL-1 quantile levels must be present, finite and non-decreasing, and the bin probabilities finite, non-negative and summing to 1 within the recorded tolerance.
- **The draw:** **uniform over contracts, without replacement.** Once a contract is picked, every other contract on its underlying leaves the pool. It draws up to 5, or until the pool is empty.
  - Seed: `sha256("<snapshot_id>\nRAND-L1")`.
  - Order: the `sample_key` order over candidate content hashes, as RAND-1 (DR-0031).
  - The draw and any shortfall are recorded in the run log.
- **Failure and null:**
  1. If FAC-1's run on the board is failed or unavailable (§4.3 reason 2), RAND-L1's run is also **failed** for P4. A RAND-L1 failure that only mirrors FAC-1's is counted once in the coverage share.
  2. If FAC-1's run qualifies but RAND-L1's pool is empty, RAND-L1 records a **null decision** (reason 3), with its cause.
  3. Otherwise RAND-L1 draws as above.
- P4 compares two **selection policies** on the same forecasts and pool, including their own cap histories and fill behaviour.

## 4. The paired statistic

### 4.1 Cut-over, anniversaries and cutoff state (E04, E05)
- **The cut-over** is the approved ET session and first eligible R2 decision slot, published in the cut-over DR **before activation**. The clock starts at that slot even if recording is partial.
- **Anniversary k:** cutoff session C_k is the cut-over session's calendar date plus k × 12 months. If that day doesn't exist in the month, use the month's last day. If it isn't an XNYS session, use the next XNYS session.
  - The cutoff timestamp is 17:30 ET on C_k.
- **The cutoff state:** for each registry and correction chain, the longest prefix whose records were recorded (`recorded_at`, or `receipt_time` for recommendations) at or before the cutoff timestamp. It is reconstructed from the append-only registry and identified by those prefixes' chain heads.
  - It's verified against the first nightly export at or after the cutoff: every row up to the cutoff must match, and later rows are ignored.
- **Enrolment is cumulative:** every scheduled R2 decision slot whose ET decision-session date is on or after the cut-over slot's date and on or before C_k − 61 calendar days. Truncation per §1 applies if a tuple engine changes version. Decisions too recent for one anniversary enter the next.

### 4.2 Statistic (Josh, questions 2 and 9)
- **A board enters a contrast** only if:
  - both arms' runs qualify (§4.3);
  - each arm has at least one *filled* pick;
  - **every** filled pick of **those two arms** is closed in the cutoff state.
  
  A board is never kept with only its closed picks.
- **Per board:** each arm's mean realized return (primary measure) over its filled picks. The board difference is FAC-1 minus the comparator.
- **Per ISO decision week t:** S_t is the sum of that week's board differences, and n_t is its number of entering boards. Weeks with none are kept, with S_t = n_t = 0.
- **The statistic:** T̂ = Σ_t S_t / Σ_t n_t, the mean over entering boards, each counting once.

### 4.3 Slots, exclusions, failures and coverage (E04; Josh)
- **A scheduled slot** is an OPEN or CLOSE decision time under SNAPTIMES-1 (DR-0002, including its half-day close) on an XNYS session, from the cut-over slot onward.
  - Every engine in a contrast counts as scheduled on every such slot until a §1 version change ends that contrast's window, whether or not it actually ran.
  - A disabled, paused or unstarted engine counts as failed or unavailable (reason 2).
- **The denominator:** every scheduled slot in the contrast's enrolment window.
- **Reasons,** each slot assigned the first that applies, in this order:
  1. **The decision board wasn't captured** (a gap).
  2. **Either arm's run failed or was unavailable.** That's when any of these holds:
     - (a) it has no run-status record, or status `failed`, `skipped` or `not_scheduled`;
     - (b) the slot was SKIPPED under DR-0033 §4, or has a failure record;
     - (c) under the frozen B12 rules, no accepted, unexpired fit was available, so the run issued no forecasts;
     - (d) for RAND-L1, §3.1 item 1;
     - (e) **the run's recommendations were recorded after the execution quote was taken** (late receipt under DR-0033 §5; Josh). Late-receipt counts are also reported on their own.
     
     If B12 fallbacks or the bug-signal exclusion remove only some names, the run hasn't failed: those names leave its pool and are counted by reason. A run that hasn't failed **qualifies**.
  3. **Either arm made a null decision.** That's a qualifying run that recorded no recommendation because nothing in its pool passed its own rules. A pool emptied by per-name fallbacks or exclusions is recorded with that sub-reason.
  4. **Either arm filled nothing.**
  5. **Either arm has a filled pick not yet closed in the cutoff state.**
  
  Classification under reasons 1–3 uses only run-status records, run logs, failure records and fit-vintage records. It is fixed **before any pick or fill is examined**. Counts are reported per reason and per arm, with overlaps, along with the total retained fraction.
- **The run-status record (required before cut-over; Codex I03):**
  - For every scheduled R2 slot and every R2 engine, a status record goes into the registry or run-log store and into the cutoff export. The statuses are `ok`, `null`, `failed` (with the exception type), `skipped` (with the reason) and `not_scheduled` (with the reason).
  - Each engine runs and records its status independently, so one engine's failure never stops another engine running or recording.
  - This is recorded in a DR and tested in the B13 failure-isolation rehearsal.
- **The coverage rule:** before examining any pick or fill, compute F/D.
  - D is the number of age-eligible *captured* slots (reasons 2–5) in the contrast's window.
  - F is the number of those on which at least one of the two engines' runs failed or was unavailable (reason 2).
  - If **F/D exceeds 10%**, the contrast is **inconclusive** at that anniversary, whatever its p-value, and takes its Holm slot as p = 1. At or below 10%, the result stands, with F, D and all counts reported.
- **No trades are invented** for null or unfilled picks.

### 4.4 Known defects and corrections (E11)
- **The primary** includes annotated trades, and uses corrected values from the cutoff state.
- **Sensitivities (labelled, never confirmatory):**
  - **original values:** without corrections;
  - **annotated trades removed:** each arm's board mean is recomputed, and the board is excluded only if an arm then has no fill. Both the changed board means and the changed board membership are reported.
  - A defect annotated against a whole board gets its own board-level sensitivity.

## 5. Statistical method (E01)

- **Why not the bootstrap.**
  - In synthetic nulls where boards in overlapping holds share a common loading, a naive independent-week bootstrap rejected a true null **12–36%** of the time at a nominal 5%. Without a common loading it stayed near nominal, at about 5–6%.
  - Real trades from adjacent weeks are open together for up to about 9 weeks, and plausibly share exposure, so the naive bootstrap can't be the primary method.
- **Primary inference: an equal-weighted-cosine (EWC) HAC t-test** on the linearized ratio.
  - **The weeks:** a board's decision week is the ISO 8601 week (Monday to Sunday) of its ET decision session. t = 0 is the ISO week containing the contrast's first enrolled slot, and t = W − 1 the week containing its last (§4.1, truncated per §1). Every ISO week between them counts, including partial first and last weeks and weeks with no entering board. W is that count.
  - u_t = S_t − T̂·n_t.
  - **K = ⌊W/12⌋** cosine terms, fixed by W alone (Josh).
  - Λ_j = √(2/W) · Σ_t cos(π·j·(t + ½)/W) · u_t, for j = 1 … K.
  - Ω̂ = (1/K) · Σ_j Λ_j².
  - se = √(W·Ω̂) / Σ_t n_t; t = T̂ / se.
  - **p-value:** two-sided, from Student-t with K degrees of freedom. The **95% interval** is T̂ ± t_{K,0.975}·se. It inverts the same test, so its coverage at the true value is 1 − size.
  - **Implementation:** the project implements the Student-t CDF and quantile itself (regularized incomplete beta by continued fraction), tested against standard tables. No resampling and no random seed are used.
- **Minimum information (Josh):** a contrast is testable only if all of these hold. Otherwise it is **inconclusive** (p = 1 in Holm).
  - **K ≥ 3** (W ≥ 36);
  - at least **30 weeks** contain an entering board;
  - Σ n_t > 0 and se > 0.
- **Validation** (`docs/evaluation/evidence/eval2-ewc/`, with a README):
  - **Method:** three blind independent implementations, all archived, plus a 100,000-replication reference.
  - **Synthetic nulls:**
    - overlapping holds of up to 4, 8 or 12 weeks;
    - common loadings μ ∈ {0, 0.5, 1, 2};
    - AR(1) week effects;
    - t(3) shocks;
    - Poisson board counts, with up to 35% empty weeks.
  - **Size of K = ⌊W/12⌋, reference values:**

    | Holds | At a nominal 5% | At a nominal 2.5% |
    |---|---|---|
    | up to 8 weeks | 4.3–6.0% at W = 43; 4.7–6.0% at W = 95 (5.8% if μ ≤ 1) | 2.0–3.1% |
    | up to 12 weeks | at most 7.0% (6.6% if μ ≤ 1) | at most 3.7% (3.3% if μ ≤ 1) |

  - **The blind reproductions** agree within Monte Carlo error (about ±0.3 points at R = 4,000).
  - **Scope:** the synthetic null models a single board-weighted mean with cross-week dependence. It doesn't model the two-arm fill selection, which §3 states as a limitation of the estimand.
- **Sensitivity (labelled, never confirmatory):** the naive independent-week bootstrap is also reported.
  - **The draw:** B = 10,000 resamples of W weeks with replacement, moving (S_t, n_t) together and recomputing T̂.
  - **Seed:** `seq = numpy.random.SeedSequence(int(sha256(b"EVAL-2").hexdigest()[:16], 16))`; `children = seq.spawn(2)`, in the order P3, P4.
  - **Generator:** each contrast uses `numpy.random.Generator(numpy.random.PCG64(child))`, and each resample is one call to `integers(0, W, size=W)`, taken in sequence.
  - **Redraws:** a draw with Σ n* = 0 is discarded and redrawn from the same stream (not counted toward B), and the number of redraws is reported.
  - **Reported with:** the numpy version, the percentile interval, the naive standard error, and the design effect (EWC variance over naive variance).

## 6. The common-contract prediction tournament (B08; spec §11; EVAL-1 §4 as amended in §3)

### 6.1 Panel (E07; Josh, question 5)
- **When it's built:** once per OPEN and CLOSE board, **from the board alone**, before any engine runs, over the §1 eligible set.
- **The cells:** for each eligible name, 8 cells: {call, put} × {target |delta| 0.25, 0.50} × {target 21, 42 calendar days}.
  - **Delta:** Black-76 forward delta, N(d1) for calls and N(d1) − 1 for puts, with targets matched in absolute value. It uses MKT-1's fitted forward for that expiration and the contract's mid implied volatility. The MKT-1 fit is computed for the panel even on boards where MKT-1 recommends nothing.
- **Choosing a contract:**
  1. Count days to expiry in calendar days from the decision session's ET date.
  2. Among the name's expirations with at least one §1-eligible contract of the cell's right, take the one nearest the target, with ties going to the earlier expiration. Forward and IV validity play no part in this step.
  3. If MKT-1 has no valid fitted forward for that expiration, the cell is unavailable (reason: no forward).
  4. Otherwise, among that expiration's eligible contracts of that right with a valid mid IV, take the strike whose |delta| is nearest the target, with ties going to the lower strike, then the contract ID. If none has a valid mid IV, the cell is unavailable (reason: no mid IV).
  
  A cell never moves to another expiration.
- **Unavailable cells** are recorded with their reason, and are **never replaced**, either by an engine-specific choice or by another expiration.
- **Duplicates:** a contract filling two cells is one panel contract, recorded once with both cell labels.

### 6.2 Records (E08)
- **What each engine records:** every long-option engine (FAC-1, MKTV-1, HVOL-1) records its predicted EXIT-1 net-return distribution (EVAL-1's 15 quantile levels and bins) for **every** panel contract, including on boards where it picks nothing.
- **Timing:** each prediction, with its receipt time, is frozen **before the execution quote**, on the decision information set.
  - As DR-0033 §5 does for recommendations, a prediction whose receipt time is after the execution snapshot's as-of time is recorded as **missing (reason "late")**. In replay, the simulated receipt time of DR-0034 applies. A late prediction is never scored.
- **RAND-L1** has no forecasts of its own, so it is not a separate forecaster.
- **Missing forecasts** are recorded as missing, with their reason, and never filled in later.

### 6.3 Shadow outcomes (E08) and capture coverage (Josh)
- **The rules:** each panel contract is evaluated as a hypothetical position, **independently** of every other, as follows.
  - Quantity: the board-known SIZE-1 rule.
  - Return denominator: the decision-price capital at risk.
  - Entry: FILL-1 at the execution quote.
  - Exits and settlement: EXIT-1's exit, retry and settlement rules, against the stored later boards.
- **Scoring:** a return is scored only for a **filled, closed and mature** shadow entry (censored as in §1, in the cutoff state). Unfilled, still-open and missing outcomes are counted separately, never scored, and never given an invented return.
- **Capture coverage (in force from the cut-over; recorded in a new DR; rehearsed at B14):**
  - (a) Every decision and execution board also captures, as **tracked-only** names, every underlying with an open shadow panel position. These are marked in the board provenance and are never eligible or recommended, as with DR-0039's held-only names.
  - (b) The nightly `CLOSE-1` and `HIST-1` steps also store, write-once and stamped with public time, the official close and 30-day ATM IV of every name with an open shadow position, an unexpired FAC-1 S_T/F target or an unresolved IV target.
  - (c) An outcome still missing despite this is counted as missing, with reason "not captured", and is never backfilled from outside the stores. Missing outcomes on names no longer in the universe are reported by horizon.

### 6.4 Scores (descriptive)
- **Per engine:**
  - CRPS, the binned log score, PIT and coverage (EVAL-1 §2.1), over its scoreable contracts;
  - expected panel IDs, forecast coverage and scoreable-outcome coverage, by board session and horizon.
- **The common-set comparison:** paired CRPS differences FAC-1 − MKTV-1 and FAC-1 − HVOL-1, on scoreable contracts where both models in the pair forecast.
  - The retained count is reported against the full panel.
  - Intervals use §5's EWC method on weekly sums and counts of the retained differences. An interval is shown only when §5's minimum-information rule holds (with "retained contract" in place of "entering board"). Otherwise the report gives the point estimate and count, and says "interval not estimable". This applies in monthly and anniversary reports alike.
  - These scores describe model quality **only on the retained contracts**.

## 7. Underlying and implied-volatility forecasts

### 7.1 S_T/F forecasts (E12)
- **MKT-1's risk-neutral S_T/F** is scored per DR-0040, as amended by R2 brief P12: closes are read only from `CLOSE-1`, through the point-in-time rule. Report-time fetching only fills store gaps, which are counted and reported.
- **FAC-1** freezes, for **every eligible name and eligible expiration** on each board, irrespective of picks, one physical S_T/F forecast from its paths. Each records:
  - the decision-time forward;
  - the target session (the expiration);
  - EVAL-1's 17-level grid;
  - its provenance;
  - the terminal time used.
- **The terminal state (Josh):** S_T is each path's simulated price at the expiration session's official close (16:00 ET, or 13:00 ET on an XNYS half day). It is reached by **one extra path step** from that session's last decision step (15:45, or 12:45) to the close, and the R2.5 path DR defines that step. The same state is used for EXIT-1 settlement on paths.
- **Scoring:** the realized official close, read from `CLOSE-1` under the same rule, ÷ the recorded forward, once the close is public, on §4.1's cohort and cutoff.
  - It's labelled **physical**, apart from the risk-neutral scores.
  - Intervals use DR-0040's cluster bootstrap by the expiration's ISO week, as for MKT-1.
- **Exclusions:** missing fits and closes are counted by reason. Where a corporate action between decision and target breaks price-unit compatibility (B10's as-of rules), the forecast is flagged, excluded from the score, and counted.

### 7.2 IV forecasts (B09, E09; Josh)
- **Records:** at each OPEN board (10:00 ET), for each eligible name and horizon h ∈ {5, 21}, FAC-1 and MKTV-1 each record:
  - the decision time;
  - the **target session**, the h-th XNYS session after the decision session (which counts as session 0);
  - h;
  - the name's 30-day ATM IV at **EVAL-1's 15 quantile levels** (1%, 2.5%, 5%, 10%, 20%, …, 80%, 90%, 95%, 97.5%, 99%; EVAL-1 §6 item 1), in annualized volatility units;
  - provenance.
- **FAC-1's quantiles** come from its paths' simulated 30-day ATM IV at the target session's **close step** (§7.1), matching `HIST-1`'s end-of-day value.
- **MKTV-1's quantiles** follow a median rule:
  - **The median** is the name's `HIST-1` 30-day ATM IV for the XNYS session immediately before the decision session, provided it is public at the OPEN board. Otherwise the forecast is missing (reason "base IV not public"), and no older value is used.
  - That IV is multiplied by the **VRP ratio MKTV-1 itself applies** to that name on that board, as recorded in its run log (P9, with its `SPYIV30` regressor taken at the previous session's public EOD value under DR-0044). If there's no ratio, the forecast is missing (reason "no VRP ratio").
  - **The log-spread** sd_h is the sample standard deviation (ddof = 1) of overlapping **(h+1)-session** log changes ln(IV_{s+h+1}/IV_s) in `HIST-1` 30-day ATM IV. That matches the base-to-target span of h + 1 sessions (Josh).
    - It uses every XNYS session pair with both values present and public before the board, within the 756 XNYS sessions ending at the latest public session.
    - A change never bridges a missing value. If fewer than 250 changes qualify, the forecast is missing (reason "insufficient IV history").
  - **Quantile p** = median × exp(sd_h × z_p), for each of the 15 levels p.
- **Outcome:** the name's `HIST-1` 30-day ATM IV on the exact target session, derived with the identical method, scoreable once public. It is scored on §4.1's cohort and cutoff. Missing forecasts and missing outcomes are counted separately, by horizon, with §6.3's capture coverage applying.
- **Scores (descriptive):** CRPS, PIT and coverage by horizon.

## 8. Schedule (no optional stopping)

- **The walk-forward replay** (November 2025 to October 2026; R2.8) is **descriptive and labelled replay**. Its dates are fixed now. A model changed after its results are seen is a new version, and the replay is re-run and labelled as such. Replay results are never evidence for P3 or P4.
- **The 20-day live run** (Stop Point F) is an **operational gate, descriptive only**. Under 61-day censoring, no R2 trade is mature by then.
- **Confirmatory analyses** run at each anniversary of the cut-over (§4.1).
- **Monthly reports:** descriptive, labelled "interim; not a test", with no p-values for P3 or P4.

## 9. Planning power (prior; updated at Stop Point E with replay-measured inputs)

- **Benchmark assumptions:** about 10 boards a week, up to 5 picks per board, so n ≈ 50 trades a week per engine; σ ≈ 1.0 per trade; within-week correlation ρ = 0.10; cross-arm weekly correlation 0.5. This gives a weekly paired-difference sd of about 0.34.
- **The first anniversary** has about **44** enrolled ISO weeks (the window from the cut-over to C_1 − 61 days covers about 305 days), so K = 3. Two-sided at Holm's stricter α = 0.025, with 80% power and Student-t_3 critical values, the detectable mean difference is about **(4.18 + 0.98) × 0.34 / √44 ≈ 27 points** of return on capital at risk.
- **The second anniversary:** about 96 weeks, so K = 8, giving about **(2.75 + 0.89) × 0.34 / √96 ≈ 13 points**.
- **The §5 evidence runs** used W = 43 and W = 95, close to these counts. The realized W and K at each anniversary follow §5.
- **Simulated power** of the actual test (K = ⌊W/12⌋, holds of up to 8 weeks), at a difference of 0.15 standard deviations of the per-board difference *in the §5 synthetic design* (sd ≈ 0.91, with within-week shared shocks):
  - These units aren't the benchmark above, so the two sets of numbers aren't directly comparable.
  - Two-sided at 0.05, and [at Holm's 0.025]:
    - about **18% [10%]** at W = 43 and **42% [30%]** at W = 95 when weeks are only weakly dependent;
    - about **7% [3.5%]** and **10% [5.4%]** under strong cross-week dependence.
- **These are optimistic benchmarks, not fitted design effects (E10).** Fewer fills, unequal weeks, coverage losses and cross-week dependence all lower power.
- **The update rule at E:**
  - recompute the above with the replay's measured fill rates, board counts, σ and an estimated long-run variance, for P3 and P4 separately;
  - record it as a dated addendum;
  - never change §3–§8 because of it.

## 10. Integrity

- **Freezing:** EVAL-2 is frozen at its approval commit, which every R2 report prints next to EVAL-1's. Changes go only through EVAL-3, before the records they affect.
- **Post-hoc analyses** are labelled post hoc wherever they appear.
- **The validation evidence** in `docs/evaluation/evidence/eval2-ewc/` (scripts, results and README) is part of the frozen record.
- **Prerequisites before the cut-over,** each through a DR:
  - the run-status record (§4.3);
  - tracked-only capture coverage (§6.3);
  - the R2.5 path DR's close step (§7.1);
  - the project's Student-t implementation (§5).

## 11. Josh's answers

**First round (2026-11-23):**
1. Confirmatory hypotheses: **two**, P3 (FAC-1 vs MKTV-1) and P4 (FAC-1 vs RAND-L1).
2. Statistic: **the per-board mean difference over shared boards.**
3. Measure: **decision-price primary**; execution-price descriptive.
4. Clusters: **ISO decision weeks.** The inference method was revised under E01; see §5.
5. Common panel: **a fixed delta grid.**
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

**After the pre-freeze check (2026-11-23):**
- **P1 and P2 test the v3 cohort only.**
- **FAC-1's forecasts use one extra path step to the close.**
- **sd_h uses overlapping (h+1)-session changes.**
- **Names with open shadow positions or targets keep being captured** (tracked-only).
- **A late-receipt run counts as a failure** (reason 2).
