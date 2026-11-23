# EVAL-2: protocol review brief

**For:** Codex, or another independent reviewer.
**Under review:** `docs/evaluation/EVAL-2-scoring.md` (draft, commit `c612fd0` or later). This is a **pre-registration document**, not code.
**Why now:** EVAL-2 is frozen before any empirical R2 model output exists (R2 brief §0.3). After freezing, it changes only through EVAL-3, and every change is labelled post hoc. Josh wants an independent statistical check before he freezes it.

**Goal:** find anything that would make a confirmatory result misleading, untestable or open to choice after the fact.

## 1. Read first
1. `docs/evaluation/EVAL-2-scoring.md`: the draft.
2. `docs/evaluation/EVAL-1-scoring.md`: it is frozen, and EVAL-2 extends it.
3. `docs/handoff/HANDOFF_R2.md`: especially §1, §2 (P1–P14), §12 (B01–B14), and DR-0044's amendment in the status line.
4. `docs/review/R2-brief-findings.md`: your plan review. EVAL-2 is meant to resolve B07, B08 and B09.
5. `docs/memos/R1-result.md` §3–§5 and `docs/evaluation/power-estimate.md`: the measured R1 inputs.

## 2. What to attack (most important first)
1. **P3 and P4.**
   - Is the per-board mean difference over shared boards an unbiased, well-defined estimand for "FAC-1 beats MKTV-1", and for "FAC-1's selection beats random"?
   - Does dropping boards where one engine filled nothing introduce selection bias? The fill rate is plausibly correlated with the engine's choices.
   - Is the censoring rule, "a board enters only when all its filled picks are mature and closed", well-posed?
2. **P4's control.** RAND-L1 uses FAC-1's forecasts and the same caps. Does P4 isolate selection skill?
3. **Inference.**
   - The cluster bootstrap by decision week, with null-centred p-values: are the clusters adequate for holds of up to 60 days that overlap adjacent weeks?
   - Are 52 clusters a year enough for percentile intervals?
   - Is the "coverage-limited" flag at 10% sensible, and should it affect the decision?
4. **The common panel.**
   - The 25/50-delta, 21/42-day grid, with delta from the MKT-1 forward and mid-IV: deterministic, and free of any engine's influence?
   - Shadow outcomes under EXIT-1 and FILL-1: what if the panel contract's entry wouldn't have filled?
   - Missing-forecast handling: can an engine improve its scores by failing to forecast hard contracts?
5. **IV forecasts.** Are the targets, units and missing-outcome rule well-defined? Is the MKTV-1 IV forecast ("today's IV × the VRP ratio, with a lognormal spread") specified enough to be scored fairly?
6. **Power** (§9). Check the arithmetic and the assumptions, especially the 0.5 cross-engine correlation and n ≈ 50 trades a week. Is the "about 15 points" claim honest about what R2 can detect?
7. **Gaps.** Anything EVAL-2 must fix now that's left open, so it could be chosen later with results in view? Consider:
   - the treatment of annotated defects in P3/P4;
   - version changes during the 12 months;
   - the R2 cut-over date's definition;
   - what happens if MKTV-1 or RAND-L1 is changed mid-period.

## 3. Already settled (don't re-argue unless it's internally inconsistent or untestable)
- **Josh's nine EVAL-2 answers** (§10).
- **The R2 brief, DR-0040 to DR-0044, and frozen EVAL-1.**

## 4. How to report
- **For each finding,** one entry with:
  - **ID:** E01, E02, …
  - **Severity:**
    - blocker: the test is invalid or untestable;
    - major: the test is valid but misleading, or leaves a forking path;
    - minor;
    - suggestion.
  - **EVAL-2 section.**
  - **The problem.**
  - **The suggested wording change.**
- **An overall verdict:** freeze as is, freeze with changes, or rework.

Deliver findings only: no repository edits, no commits or pushes, no network, and no market data. The results will be recorded in `docs/review/EVAL-2-findings.md`, and accepted changes folded in before Josh freezes EVAL-2.
