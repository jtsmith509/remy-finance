# R2 build brief: plan review brief

**For:** Codex, or another independent reviewer.
**Under review:** `docs/handoff/HANDOFF_R2.md` (draft, commit `f3ccb03` or later). This is a review of a **plan**, not code. Nothing in it has been built.
**Context:** R0–R1 are complete (`docs/memos/R1-result.md`). The R2 brief was drafted by Claude from Josh's answers to 10 scoping questions and 14 design questions, all recorded in the brief. Josh asked for an independent critique before he approves it.

**Goal:** find what's wrong, missing, infeasible or internally inconsistent in the plan *before* any of it is built. Once EVAL-2 is frozen and engines record prospectively, design mistakes become permanent.

## 1. Read first
1. `docs/handoff/HANDOFF_R2.md`: the plan.
2. `docs/spec/spec_v0.5.md` §1, §5, §8, §9, §11 and §13. R2 is "proposed" in §13.
3. `docs/handoff/HANDOFF_R0_R1.md` §0, §7 and §9: the process and testing standards R2 inherits.
4. `docs/evaluation/EVAL-1-scoring.md` and `docs/evaluation/power-estimate.md`: what EVAL-2 extends.
5. `docs/memos/R1-result.md` §3–§5: the replay and live findings that motivated R2's choices (the VRP, carry, slippage and fill rates).
6. DR-0037, DR-0040, DR-0041 and DR-0042: the accepted decisions R2 builds on.

## 2. What to attack (most important first)
1. **Consistency with the spec.**
   - Does the brief do what spec §13 says R2 is, and nothing that belongs to R3 or R4? Examples: `EXIT-1` versus R4's policy optimization; the IV dynamics; "continuous shared economic paths".
   - Is anything the spec requires of R2 missing?
2. **Look-ahead and point-in-time risks.**
   - The rolling 3-year fits, the monthly refits, the GARCH filtering, the bootstrap of standardized residuals "jointly by date", the Kaiser rule, the VRP regression on VIX, and the `CLOSE-1` backfill.
   - Where could data from after a decision leak in? Consider the realized variance that follows each IV observation in the VRP estimate, and whether its window can overlap the decision date.
3. **Statistical validity.**
   - Is filtered historical simulation with GARCH(1,1) on 3 years reasonable for 60-day option horizons?
   - Is the Kaiser rule on residual correlation sensible for ~50 names?
   - Is a linear-in-VIX variance ratio, clipped to [0.3, 1.5] and sector-shrunk, identifiable from 3 years of overlapping 30-day windows?
   - Is a fixed smile shape with a VIX-plus-spot-driven level a defensible pricing model for early exits?
   - Is "exit at mid minus the board's half-spread" a fair counterpart to live FILL-1 exits?
4. **Evaluation design.**
   - EVAL-2 must be frozen before any R2 result is examined. Is freezing it at Stop Point D early enough?
   - Is the paired comparison (FAC-1, MKTV-1, HVOL-1, RAND-L1 on the same boards and caps) well-posed?
   - RAND-L1 takes its forecasts from FAC-1: does that confound anything?
   - What power can a 20-day live run and a 12-month replay have for the paired comparison?
5. **Feasibility.**
   - Compute: 2,000 paths × every long option × a daily profit-target check, inside 5 minutes alongside movegen and MKT-1, on one Mac mini.
   - Data: 3 years of EOD option quotes for ~50 names to derive 30-day ATM IV. Consider volume, ThetaData license limits and gaps.
   - Is the R2.0 verification spike scoped to answer these before anything depends on them?
6. **Continuity of the R1 record.**
   - The cut-over moves MKT-1 and RAND-1 to v4 (DR-0041, DR-0042), and adds MKTV-1, FAC-1, HVOL-1 and RAND-L1.
   - Is anything about the prospective record, the registry, the scheduler window, EVAL-1's 12-month confirmatory analysis or the existing v3 records put at risk?
7. **Missing pieces.** Are there definitions, tests, stop-point criteria, failure modes or DRs the brief should name but doesn't? Examples: the universe rebalance during the live run; refits failing; a name lacking IV history; a corporate action in the path history.

## 3. Already settled (don't re-argue unless it's internally inconsistent or infeasible)
- **Josh's answers:** the 10 scoping answers (brief §11) and P1–P14 (brief §2).
- **Accepted DRs:** DR-0001 to DR-0043.
- **EVAL-1 is frozen.**

## 4. How to report
- **For each finding,** one entry with:
  - **ID:** B01, B02, …
  - **Severity:**
    - blocker: the plan can't work as written;
    - major: the plan works but gives misleading results;
    - minor;
    - suggestion.
  - **Brief section.**
  - **The problem.**
  - **The suggested change to the brief.**
- **A short overall verdict:** approve as is, approve with changes, or rework.

Deliver findings only: no edits to the repository, and no commits or pushes. Use no network and no market data. The results will be recorded in `docs/review/R2-brief-findings.md`, and accepted changes folded into the brief before Josh approves it.
