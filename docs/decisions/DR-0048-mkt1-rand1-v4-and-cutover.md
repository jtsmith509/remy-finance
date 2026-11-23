# DR-0048: MKT-1 v4, RAND-1 v4 and the cut-over mechanism

- **Status:** accepted (Josh, 2026-11-23). No 3-year history rule for v4; the cut-over is a date constant in code, set by the cut-over DR's own commit; replays name their version (`--baselines`, default v3).
- **Date:** 2026-11-23
- **Source:** R2 brief P2 and R2.2; [DR-0041](DR-0041-exclude-candidates-that-cannot-win.md) (eligibility) and [DR-0042](DR-0042-rank-on-excess-return.md) (ADJ-1), both accepted 2026-11-18 "from R2"; EVAL-2 §1 (the v4 population) and §4.1 (the cut-over slot); [DR-0044](DR-0044-r2-data-substitutions.md).
- **Stage:** R2, Part 1 (R2.2)

## Context
- **What P2 asks for:** MKT-1 and RAND-1 continue in R2 as **v4**: DR-0041 eligibility plus DR-0042 ADJ-1 ranking. They stay risk-neutral and keep DR-0037's mid-anchored mean. The VRP version is a separate engine, MKTV-1.
- **What R1 requires:** v3 runs unchanged until an announced cut-over (R2 brief §0.5). Its configs, artifact hashes, eligible set and picks can't change.
- **What EVAL-2 requires:**
  - §1 defines the v4 population as all MOVEGEN-2 structures with DR-0041 eligibility;
  - §4.1 requires the cut-over slot to be published in a DR before activation.

## Options considered
1. **How v4 lives in the code:**
   - **Side by side** (chosen). `MKT_1` and `RAND_1` (v3) are untouched. `MKT_1_V4` and `RAND_1_V4` are new configs, and the v4 rules switch on from engine version 4.
   - Replace v3, which would change the live baselines before the cut-over.
   - Add fields to the shared config. That would change v3's artifact hash.
2. **The 3-year history rule (DR-0044) for v4 (Josh: no).** EVAL-2 §1 registers it for the four long-option engines only. MKT-1 reads the board and needs no history, and v4 stays R1's baseline continued.
3. **The switch (Josh: a date constant in code).**
   - **`CUTOVER` in code** (chosen): `None` until the cut-over DR, whose own commit sets it.
   - A setting on the Mac, which isn't in git, so the DR and the machine could disagree.
4. **Replays (Josh: an explicit option, default v3).** `remy replay run --baselines v4` runs v4; without it, v3. A re-run of an old replay month keeps its meaning.

## Decision
- **MKT-1 v4** (`MKT_1_V4`, engine version 4):
  - **Eligibility (DR-0041):** a recommendable candidate whose best attainable net return is not above 0 is dropped before fitting. That return is the signed payoff maximum minus premium minus fees, over capital at risk: `return_bounds`, after the V04/X01 fixes.
    - Movegen's list and its hash are unchanged.
    - The run log's flags carry `CANNOT_WIN_EXCLUDED:<n>`, even when n is 0.
  - **Ranking (DR-0042, ADJ-1):** the key is the predicted mean minus `(1 / DF − 1)`, with DF the expiration's discount factor from MKT-1's fit.
    - The recorded forecast (mean, quantiles, bins, tails) is exactly v3's net return on capital at risk. Only the key changes.
    - Each recommendation records `ranking_adjustment = ADJ-1` and `("eligibility", "DR-0041")` in its search config.
  - **Unchanged:** the fits, the mid-anchored mean, the +5% bug-signal exclusion (on the mean), SELECT-1, HOLD-1 and FILL-1.
- **RAND-1 v4** (`RAND_1_V4`):
  - It samples from MKT-1 v4's eligible set and takes its forecasts, sharing one scoring pass with MKT-1 v4.
  - It doesn't rank, so its records keep `ranking_adjustment = ADJ-0`. Its search config names `("eligibility", "DR-0041")`.
  - Its seed is unchanged (`sha256(snapshot_id, "RAND-1")`). No board is ever run by both versions.
- **The 3-year history rule doesn't apply to v4** (Josh).
- **The cut-over:**
  - `remy_finance.scheduler.cutover.CUTOVER` is `(ET session date, session) | None`, and is `None` today.
  - The prospective scheduler runs v3 on every decision slot before it, and v4 from it, inclusive.
  - The cut-over DR's own commit sets it, so the switch is in git, reviewed and pulled before it takes effect.
  - The run-status record (DR-0047) carries each slot's engine version, so the switch is visible per slot.
- **Replays:** `remy replay run --baselines v3|v4`, default v3. A replay doesn't follow `CUTOVER`.
  - A replay registry holds one MKT-1/RAND-1 version: a replay refuses to start over records of the other version ("archive it first").
- **Versions never mix:**
  - A stored run log is reused only if its engine version is the slot's. Otherwise the engine fails with `VersionMismatchError`.
  - SELECT-1's per-day cap counts only the same version's picks, so on a CLOSE cut-over day v3's OPEN picks don't cap v4.
- **Not in this DR:**
  - the cut-over slot itself (the cut-over DR, R2.10);
  - MKTV-1, FAC-1, HVOL-1 and RAND-L1, which join the same switch when they exist.

## Evidence that v3 is unchanged
- **Artifact hashes:** `MKT_1` and `RAND_1` hash exactly as before this change: `af271cc7…` and `08923e90…`. A test pins both.
- **Behaviour:** every existing baseline, scheduler and replay test passes unchanged. That's 666 tests before this change; the suite is now 681.
- **The switch:** with `CUTOVER = None`, every slot resolves to v3.

## Review (2026-11-23)
An internal adversarial review found 3 defects and 3 missing tests, all fixed with regression tests: see `docs/review/v4-findings.md`.

## Consequences
- **v4 is ready for the cut-over and for the R2 replay year** (`--baselines v4`). Its records are a separate cohort, scored descriptively by EVAL-1's metrics (EVAL-2 §1).
- **v3's live record continues untouched until the cut-over DR.**
- **A regression test** shows that a debit above the spread's width is never eligible in v4, as DR-0041 requires.
