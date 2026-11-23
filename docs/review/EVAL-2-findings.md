# EVAL-2: protocol review findings and resolution

- **Reviewer:** Codex (independent), following `docs/review/EVAL-2-review-brief.md`
- **Reviewed:** `docs/evaluation/EVAL-2-scoring.md` at `24c384e`
- **Verdict:** **freeze with changes.** 12 findings: 1 blocker, 8 major and 3 minor.
- **Resolution (2026-11-23):** all 12 are folded into EVAL-2 revision 2. Josh decided E01, E03, E04, E06 and E09; the rest are wording that closes forking paths.
- **Codex's numbers, checked independently:**
  - the 7.51 variance ratio (E01);
  - the 9.75% across two looks (E06);
  - the detectable differences of 16.1, 17.3, 22.8 and 32.3 points (E10).

## E01: how the replacement test was chosen
1. **The first study:** three candidate procedures (a moving block bootstrap, a stationary bootstrap, and a Bartlett HAC test with fixed-b critical values), simulated by independent agents.
   - **A flaw in my design:** an audit found that the synthetic design I specified had **no cross-week covariance** (the week loading had mean zero), so it couldn't test E01.
   - **What the stress design showed:** with a persistent common loading added, the naive week bootstrap rejected a true null 12–36% of the time at a nominal 5%.
   - **The candidates also failed:** both block bootstraps were liberal (10–25%), and so was the Bartlett test (up to 11%).
2. **The auditor proposed an EWC HAC t-test.** I then had it **blind-reproduced** by three independent implementations, plus a 100,000-replication reference. All agreed within Monte Carlo error. Size at a nominal 5%:

   | K | Holds of up to 8 weeks | Holds of up to 12 weeks |
   |---|---|---|
   | ⌊W/10⌋ | ≤ 6.3% | up to 7.7% |
   | ⌊W/12⌋ | 4.3–5.8% | ≤ 6.6% |
   | ⌊W/8⌋ | worse everywhere | worse everywhere |

3. **Josh chose K = ⌊W/12⌋ with a minimum K of 3,** because an unfilled exit can retry to expiry, up to 60 days.
4. **The scripts and results are archived** in `docs/evaluation/evidence/eval2-ewc/`.

| # | Severity | Finding | Resolution (EVAL-2 rev. 2) |
|---|---|---|---|
| E01 | blocker | Independent week resampling invalid under overlapping holds | EWC HAC t-test, K = ⌊W/12⌋, Student-t_K, minimum K = 3 and 30 weeks with boards; validated (§5) |
| E02 | major | The estimand is conditional on joint fills | Stated explicitly as conditional; opportunity-frame descriptives added (§3) |
| E03 | major | RAND-L1's randomization undefined | **Josh:** uniform over contracts, from FAC-1's forecastable pool, with the seed and order fixed (§3.1) |
| E04 | major | No fixed cohort or flag denominator | Enrolment window, reason order, pre-pick denominator. **Josh:** over 10% engine failure is inconclusive (§4.1, §4.3) |
| E05 | major | Clock and version tuple not fixed | Cut-over slot published before activation; tuple and refit rules; cumulative cutoffs pinned to snapshots (§1, §4.1) |
| E06 | major | No across-look interpretation | **Josh:** each anniversary stands alone, with no across-look guarantee; inconclusive counts as p = 1 (§3) |
| E07 | minor | Panel not unique | Built once before filtering; delta convention, tie rules, duplicates, unavailable cells (§6.1) |
| E08 | major | Shadow outcomes and score denominators | Frozen before the execution quote; independent FILL-1; only filled, closed, mature outcomes scored; coverage reported (§6.2–§6.4) |
| E09 | major | MKTV-1's IV forecast underspecified | **Josh:** median = the previous session's IV × the VRP ratio; log-spread fitted per horizon (§7.2) |
| E10 | major | Power optimistic | Redone with t_K and W = 43 (about 27 points at year 1, about 13 at year 2), plus simulated power and dependence scenarios (§9) |
| E11 | minor | Defect sensitivities underspecified | Trade-level and board-level sensitivity rules; corrected values primary (§4.4) |
| E12 | minor | FAC-1 S_T/F observation unit | Every eligible name and expiration per board, with forward, target and units rules (§7.1) |

---

## The reviewer's report (as delivered)

# EVAL-2 independent pre-registration review

**Overall verdict — freeze with changes.** Preserve the two contrasts, equal board weights, decision-price primary measure, separate Holm family, and annual schedule. Resolve E01 before freezing. Independent resampling of decision weeks does not support the stated error rate when adjacent weeks share holding-period shocks. Also fix the analysis cohort and interpretation before the first prospective record.

Reviewed the draft in `docs/evaluation/EVAL-2-scoring.md` at `24c384e506be84574def83ba49b2708bfbd509f9`, following `docs/review/EVAL-2-review-brief.md`. References are to repository documents at that commit. Numerical examples below use the draft's assumptions or synthetic outcomes. Suggested wording is for owner approval before freezing and preserves frozen EVAL-1.

## E01 — Independent resampling of decision weeks can invalidate P3/P4 inference

**Severity** — blocker  
**EVAL-2 section** — §5, with consequences for §§3, 6 and 9.

**Problem.** Pairing removes exposure shared by the two arms only to the extent that their picks load on the same shocks. Trades originating in different decision weeks can remain open together for weeks, and EXIT-1 does not guarantee short holds. Independently drawing weeks suppresses cross-week covariance. Null centering and Holm cannot repair miscalibrated individual p-values. For example, if each weekly paired difference is the mean of eight consecutive independent weekly shocks, adjacent differences share seven shocks. With 43 weeks, the variance of their grand mean is about 7.51 times its value under independent weeks of the same marginal variance. This is a counterexample, not an R2 variance estimate. Even if 52 calendar weeks were available, they would not necessarily be 52 independent clusters.

**Suggested wording change.** “The ISO decision week is the grouping unit. Primary inference preserves dependence across adjacent weeks. Before freezing, specify one dependence-aware procedure, its fixed tuning parameters and minimum information requirements. Validate its size and interval coverage on synthetic nulls with overlapping holds, unequal board counts and coverage gaps. If it cannot support inference at the intended cohort length, report the contrast as inconclusive.” For example, preregister a block bootstrap over contiguous calendar weeks with block lengths justified by overlap and synthetic checks. Move weekly sums and counts together, including empty weeks, and recompute the board-weighted ratio in every draw. Pin the null statistic, two-sided p-value and interval rules, seed conversion, and treatment of empty or degenerate resamples. Use the same dependence treatment for §6 intervals or omit intervals there. Keep an independent-week calculation as a labelled sensitivity if desired.

## E02 — The primary contrast is conditional on joint fills and does not measure policy value

**Severity** — major  
**EVAL-2 section** — §§3–4.

**Problem.** The shared-board mean defines a conditional estimand. The two arms' own choices determine which boards have joint fills and which trades contribute on those boards. This can reverse the conclusion relative to all opportunities. In a synthetic example with 100 equally sized opportunities, both engines fill on 50 and FAC-1 earns 10 percentage points more on each. On the other 50, FAC-1 has no fill and MKTV-1 earns 30 points on the same dollar stake. The primary estimate is +10 points while FAC-1 earns less aggregate P&L. Differential execution is plausible. R1 replay fill rates were 100% and 79% for MKT-1 v3 and RAND-1 v3, which informs the risk without estimating the R2 difference.

**Suggested wording change.** “P3 and P4 test zero expected board-weighted difference in decision-price returns among the respective contrast's mature boards with fills in both arms and complete closure. They describe returns conditional on inclusion. They do not establish superiority over all decision opportunities or isolate the effect of the forecast model independently of execution.” Keep the chosen statistic. Report each arm's recommendations, fills, shortfalls, nulls, failed runs and net dollar P&L over the prespecified opportunity frame descriptively, including boards retained in only one contrast. Unfilled opportunities contribute no trade return.

## E03 — RAND-L1's randomization law leaves P4's control unspecified

**Severity** — major  
**EVAL-2 section** — §1 shared rules, §3 P4 and §4.

**Problem.** §1 says all four engines use ADJ-1 ranking, while §1 and §3 require RAND-L1 to pick randomly. The sampling law is material. Uniform over contracts gives more weight to names with more strikes, whereas uniform over names then contracts does not. Removing names after a pick, excluding names an engine used at OPEN, and handling a failed FAC-1 forecast all change the control. A seeded draw and identical numeric caps do not identify those choices. Holding FAC-1's forecasts fixed isolates a selection-policy comparison only if the other rules and pre-cap forecastable opportunity set are shared. The two engines can still have different remaining opportunities at CLOSE because their own OPEN picks differ.

**Suggested wording change.** “RAND-L1 uses the same pre-cap candidate IDs with valid FAC-1 forecasts as FAC-1, including the same non-ranking filters. FAC-1 applies ADJ-1. RAND-L1 draws uniformly from the remaining eligible contracts, without replacement, removing the selected underlying from its remaining pool and respecting its own earlier same-day picks. Draw up to five or until the pool is exhausted. Freeze the seed derivation and candidate order, record the draw and shortfalls, and treat an unavailable shared forecast set under the prespecified failure rule.” If a different uniform sampling unit is intended, name it before freeze. Interpret P4 as comparing these two selection policies, including their cap histories and fill behavior. Fix the primary seed rule without picking among outcomes from alternative seeds.

## E04 — Cohort maturity and the coverage flag lack a fixed denominator

**Severity** — major  
**EVAL-2 section** — §1 censoring and §4 shared boards, exclusions and 10% flag.

**Problem.** The 61-day age condition applies to the decision board as a whole, while closure can fail for an individual filled pick. “All of its filled picks” could include engines outside the contrast. The flag's “otherwise eligible” denominator is also ambiguous. If a failed run never produces picks, the analyst cannot know whether it would have filled, so the joint-fill sample cannot serve as the failure denominator. Failure coverage may look good even if unfilled entries or missing settlements remove most planned boards. A flag without a decision rule leaves room to interpret the same p-value differently after seeing its sign.

**Suggested wording change.** “For each contrast, begin with all scheduled eligible R2 decision slots on or before cutoff date minus 61 calendar days, within its prespecified enrollment window. Account separately for missing decision capture, unavailable or failed runs, nulls, no fills and incomplete closure. A board contributes only if both runs qualify, both have at least one fill, and every filled pick in those two arms is closed in the cutoff snapshot. Do not retain only the closed picks. Fix a reason precedence for totals and report overlapping reasons by arm. Compute the >10% engine-failure flag among age-eligible captured boards on which both engines were scheduled to run, before examining picks or fills.” Explicitly decide whether that flag changes inferential status or just labels the conditional result. Report the total fraction retained and delayed-closure counts separately. Use a frozen minimum-information rule for whether the test is estimable.

## E05 — The clock and tested version tuple are not fixed across cut-over or upgrades

**Severity** — major  
**EVAL-2 section** — §1 version integrity and §8 confirmatory schedule.

**Problem.** “First prospective R2 record” could be a v4 baseline record, a null run log or the first recommendation during a partially successful launch. These clocks differ. The protocol also does not name the analysis window, whether later looks are cumulative, or what happens if FAC-1, MKTV-1 or RAND-L1 changes before the anniversary. EVAL-1 §7 bars silent interversion pooling. Choosing the most favorable version pair or letting a comparator upgrade reset the clock would change the test without a new registration. Scheduled monthly refits need to be distinguished from method changes.

**Suggested wording change.** “Before activation, publish the approved ET cut-over session and first eligible slot, the locked method/version tuple for P3 and P4, and the exact anniversary cutoff timestamp rule. The clock starts at that slot even if recording is partial. Specify the enrolled decision-date interval for each anniversary and how decisions too recent at one cutoff are considered later. Pin the analysis to the data and correction snapshot available at that cutoff.” Define which monthly refits and non-method fixes belong to the tuple. If either arm changes materially, test only the prespecified compatible tuple if its cohort remains estimable. Report other version pairs descriptively unless registered separately in advance, and do not select a replacement pair after seeing results. Keep EVAL-1's original clock.

## E06 — Annual Holm tests need an explicit interpretation across looks

**Severity** — major  
**EVAL-2 section** — §3 Holm family and §8 annual analyses.

**Problem.** Holm at 0.05 controls the two tests at a given valid annual look. It does not control a 0.05 chance of ever declaring success across repeated looks. With two independent looks each at family-wise 0.05, the probability of at least one false rejection under the full null is 9.75%. Cumulative looks are dependent but still do not inherit across-look control from Holm alone. A missing or untestable contrast also needs an explicit rule so annual coverage cannot silently relax the other contrast's threshold.

**Suggested wording change.** “Holm controls the P3/P4 family at each prespecified anniversary. Each claim is specific to that analysis, with no across-anniversary error guarantee. A contrast that is untestable is marked inconclusive and occupies its Holm slot as a non-rejection, using p = 1 for adjustment only. A two-sided rejection in the negative direction is evidence in the comparator's favor.” If an eventual success claim across years is intended, preregister an across-look error budget before the first look. Keep P1/P2 separate.

## E07 — The common panel grid does not uniquely determine contract IDs

**Severity** — minor  
**EVAL-2 section** — §6 panel construction and §1 eligibility.

**Problem.** The lower-strike tie break does not settle an equal-distance expiry tie, duplicate cells that map to the same contract, or missing forward/mid-IV. Put delta is signed and Black-76 delta can mean discounted forward delta or an undiscounted hedge convention. Also, §6 lists DTE 7–60, but the approved EXIT-1 timing excludes entry inside the seven-day time-stop boundary. A panel chosen from valid successors after individual engines drop forecasts would no longer be common.

**Suggested wording change.** “Build the candidate panel once from the decision board before engine-specific filtering. Use the eligible long-contract set, including the approved EXIT-1 entry boundary, and name the exact Black-76 delta convention with negative put targets or absolute magnitude. Break equal expiry distances by the earlier expiry, then strike ties by lower strike and contract ID. Define whether duplicate grid cells count once per contract or once per cell. Record unavailable cells and their reasons. No engine-specific replacement.” Pin these rules to the approved panel constructor, including how the board-common MKT-1 fit is obtained on null recommendation boards.

## E08 — Shadow returns and forecast-score denominators can select favorable cases

**Severity** — major  
**EVAL-2 section** — §6 records, outcomes and scores.

**Problem.** An unfilled FILL-1 entry has no strategy return. Scoring an invented zero against a conditional-on-fill forecast would score a different target. The draft also leaves open which quantity the shadow uses and whether the available market size is tested for each shadow independently. Meanwhile, per-engine scores on available forecasts can improve by omitting hard contracts, and “every engine covered” lets unrelated missing forecasts determine the FAC-1–MKTV-1 pairing. Declaring missingness without reporting the denominator cannot distinguish better scores from narrower coverage. Freezing a forecast merely “before the outcome” would also allow receipt after the entry quote if the outcome means final return.

**Suggested wording change.** “Freeze every prediction and its receipt time before the execution quote, on the decision information set. Use the same board-known sizing rule and decision-price denominator for all shadow contracts. Apply FILL-1 independently to each hypothetical position at that quantity, use the approved EXIT-1 retry and settlement rules, and define cutoff maturity as in §1. Score a conditional return distribution only for a filled, closed, mature shadow entry. Count unfilled, open and missing outcomes separately. Report expected panel IDs, forecast coverage and scoreable-outcome coverage by arm and horizon. For the common-set comparison, specify which independent model forecasts must be present and report the retained count against that set's full panel. Do not treat RAND-L1's FAC-1 copy as a separate forecast.” If conditional and common-set scores are both reported, label their populations and do not interpret the latter as model quality on absent cases.

## E09 — MKTV-1's IV forecast is not uniquely specified at either horizon

**Severity** — major  
**EVAL-2 section** — §7 IV forecasts.

**Problem.** A lognormal with “today's IV × the VRP ratio” needs a location convention and a horizon-specific dispersion. Matching that product to the mean or median gives different quantiles. A one-session log-IV residual spread used unchanged at 5 and 21 sessions gives different scores from a scaled or simulated residual spread. “Today's IV” at OPEN could also mean the latest public EOD value or the current OPEN chain estimate. Using that day’s later EOD value would leak data. These ambiguities are independent of the accepted choice to mark down the IV level by the ratio. DR-0044 substitutes SPYIV30 for VIX in the VRP regime input.

**Suggested wording change.** “For each OPEN board, define the point-in-time IV base from a named source and the VRP estimate available at the board cutoff, using SPYIV30 under DR-0044. Specify whether their product is the lognormal median or mean and freeze the formula or versioned DR for log-scale dispersion at 5 and 21 sessions before empirical outputs. Count horizons from the decision session on the exchange calendar. The verifying IV is the same-method HIST-1 EOD value on that exact session, in annualized volatility units, scoreable only when public by the analysis cutoff. Record missing forecasts separately from missing verifying outcomes and report coverage by horizon.” For example, with a median convention, quantile p is the IV base times the ratio times exp of the horizon log-scale standard deviation times the standard normal quantile at p. The dispersion rule still needs a decision.

## E10 — The 15-point power estimate is an optimistic scenario, with the wrong first-look count

**Severity** — major  
**EVAL-2 section** — §9, using §§4, 5 and 8.

**Problem.** The arithmetic is consistent with the draft's assumptions. With 50 trades, within-week correlation 0.10 and cross-arm weekly correlation 0.50, the paired weekly SD is 0.344. A two-sided normal approximation at 0.025 and 80% power gives 14.7 percentage points with 52 independent weeks. But the first anniversary with a 61-day lag has roughly 43 weeks of decision dates before any missingness. Fifty picks per week is a ceiling for a normal week. Available fills can be fewer, and unequal board counts mean a pooled trade average need not have the variance of the primary board-weighted statistic. Neither the R1 power estimate nor the memo validates 0.50 cross-arm correlation or independence of R2 weekly differences.

**Suggested wording change.** “The 15-point figure is an optimistic independent-week benchmark. Using approximately 43 weeks, the same calculation gives 16.2 points. With 25 fills in the same toy variance formula it gives 17.3 points. With zero cross-arm weekly correlation, or with a twofold increase in variance from cross-week dependence, it gives 22.8 points at 50 fills. With both it gives 32.3 points. These are sensitivity scenarios, not fitted design effects.” At E, update planning separately for P3/P4 using the actual board-weighted statistic, fill and coverage rates, unequal weeks and a range of long-run variances consistent with E01. Fix the update rule in advance. Treat replay estimates as uncertain planning inputs and do not alter the test or its schedule based on them.

## E11 — Known defects can change the paired population through multiple routes

**Severity** — minor  
**EVAL-2 section** — §1 inherited rules and §4 paired statistic.

**Problem.** “Reported with and without” does not specify whether a defect-specific sensitivity drops an annotated trade and recomputes the board mean, or drops the whole board. If the annotated trade is the only fill in one arm, a trade-level removal itself drops the board. Corrections recorded after the cutoff can also change historical results if the analysis has no pinned snapshot. None of these choices should change the planned primary Holm decision.

**Suggested wording change.** “The primary uses the cutoff snapshot's corrected values and includes annotated trades. Show an original-values sensitivity and a labelled sensitivity that removes annotated trades, recomputes each arm's board mean, and excludes the board only if a required arm then has no fill. Report both changed board means and changed board membership. If a defect applies to a whole board, identify it as such and report that board-level sensitivity separately. These sensitivities do not replace the primary tests.”

## E12 — FAC-1 underlying-score records need a defined observation unit

**Severity** — minor  
**EVAL-2 section** — §7 S_T/F forecasts, with §8 analysis cutoffs.

**Problem.** “Scored the same way” clearly points to DR-0040's scores and outcome, but it does not identify which FAC-1 paths are converted to expiration forecasts or which forward fixes the scale. An analyst could score every eligible board/name/expiration or only expirations on selected contracts. Because both option selection and missing fits depend on the board, these sets can produce different calibration conclusions. The same shares and price units must be used on both sides of the ratio, including when a corporate action intervenes.

**Suggested wording change.** “For each board, freeze one FAC-1 S_T/F forecast for every eligible name and expiration in the specified forecast coverage set, irrespective of picks. Record the decision-time forward, target session, forecast grid, and provenance. Score realized official close divided by that recorded forward only once the exact target-session close is public, using the stated cohort and cutoff. Count missing fits and closes by reason. Define price-unit compatibility for the FAC-1 target and separately flag cases where it cannot be established.” Keep the existing DR-0040 risk-neutral scoring rule for MKT-1.
