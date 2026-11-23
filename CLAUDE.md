# CLAUDE.md

Start here, then read the governing documents in full before you change anything:

- **Build brief (current stage): `docs/handoff/HANDOFF_R2.md`** (R2, approved 2026-11-21). It gives scope, tasks, contracts, tests and stop points D, E and F.
- **Earlier brief:** `docs/handoff/HANDOFF_R0_R1.md` (R0–R1, complete). Its conventions and testing standards still apply unless the R2 brief changes them.
- **Spec:** `docs/spec/spec_v0.5.md`. It is an accepted-changes copy; the `.docx` beside it is the source of record.

If the brief and the spec disagree, **stop and ask Josh**. Don't resolve it yourself.

## Ground rules (handoff Section 0)

1. **Scope is R2 only** (R2 brief §0). Don't build R3–R6: no causal search, event ordering or opening books; no policy optimizer, conditional entries or MultiPV; no neural or hybrid models. Leave clean seams, but don't stub those stages out in detail. **EVAL-2 is approved by Josh before any empirical R2 model output exists** (R2 brief §0.3).
2. **Stage process.**
   - Every material design choice gets a decision record: `docs/decisions/DR-NNNN-short-title.md`. It covers context, options considered, decision and consequences. Status stays **proposed** until Josh confirms it. Settled decisions (handoff Section 1) are reopened only through a new, linked DR.
   - Each stage ends with a short result memo in `docs/memos/`: what was built, test results, known limitations and open questions.
   - **Stop points are hard stops:** A, B and C (R0–R1, done), then **D, E and F** (R2 brief §9). Summarize what was built, the test results, the DRs awaiting confirmation and the specific questions for Josh. Then wait for his explicit confirmation.
3. **Correctness before breadth.** Test payoff math, point-in-time hygiene and registry immutability before building anything that depends on them.
4. **No look-ahead, ever.** Any code path that can read data stamped after the decision time is a defect, not an approximation. Models, movegen and baselines read data only through `PointInTimeView`.
5. **No brokerage code.** No order routing and no broker SDKs. "Orders" are paper records only.
6. **Secrets** (ThetaData credentials, API keys) live in environment variables or the macOS keychain, never in the repo. `.env.example` lists names only.

## Working conventions

- Python 3.12 via `uv`. Before any stage memo, run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest`. All must pass.
- `mypy --strict` applies to `remy_finance.core` and `remy_finance.registry`.
- Anything touching cash flows uses `Decimal`, never float. Records are frozen pydantic models with canonical JSON and a SHA-256 hash.
- The test suite is offline: it runs against `FixtureSource` with no network.
- `data/` is git-ignored: snapshots, registry DBs and logs. Never commit market data.
- Commit in small, reviewable steps with descriptive messages.
- After each major milestone, write a review brief in `docs/review/` for Josh to hand to Codex as an independent reviewer. Record each finding and its resolution in a findings file, with a regression test for every fix.

## Status

- Current stage: **R2, Part 1.** The R2 brief was approved 2026-11-21, after Codex's plan review (`docs/review/R2-brief-findings.md`). **R2.0 is done: GO** (2026-11-23, `docs/memos/R2.0-data-verification.md`), with DR-0044 (SPY 30-day IV replaces VIX; Massive rate history; a 3-year minimum history). **EVAL-2 is frozen** (approved 2026-11-23 at `dd5e6e7`; `docs/evaluation/EVAL-2-scoring.md`), after Codex's review and a pre-freeze check (`docs/review/EVAL-2-findings.md`). Empirical model work may now begin. Next: build Part 1 (HIST-1, CLOSE-1, MKT-1/RAND-1 v4, VRP, the factor model) toward **Stop Point D**. HIST-1 is built (DR-0045, proposed; internal review fixed, `docs/review/HIST-1-findings.md`). Its first backfill ran on the Mac on 2026-11-23, and Josh amended the method the same day: undeclared dividends are kept, public at the ex-date open, and a nearest-expiry IV fallback covers 21–45 days. A second `hist build` applies both. CLOSE-1 and the nightly HIST-1 update are built (**DR-0046 accepted** 2026-11-23; `docs/review/CLOSE-1-findings.md`). The run-status record is built (DR-0047, proposed, REGISTRY-2; `docs/review/run-status-findings.md`); the Mac needs a one-time `remy registry migrate`. Next in Part 1: MKT-1/RAND-1 v4, VRP and MKTV-1, then the factor model. EVAL-2 §10 lists the DRs needed before the cut-over. R1 is complete: Stop Point B closed 2026-11-09 (`docs/memos/R0-result.md`); **Stop Point C closed 2026-11-18** (`docs/memos/R1-result.md`). The prospective scheduler keeps running on the Mac mini.
- Agreed for R1: optimize the move generator early, keeping DR-0025's exactness (done). The spec §11 power estimate and the pre-registered scoring protocol **EVAL-1** were approved 2026-11-09 (`docs/evaluation/`). EVAL-1 is frozen, and changes go only through EVAL-2. Before the 5-day live run: (1) the EVAL-1 §6 record changes, done in DR-0032; (2) run the replay month (August 2026, DR-0034; runbook in `docs/runbook/scheduler.md`) and add a dated update to the power estimate. The first August run (MOVEGEN-1, MKT-1 v1) is archived; it was re-run with MKT-1/RAND-1 v2 (DR-0037), and the power addendum is written (2026-11-10). The third run with v3 (arbitrage-free SVI, DR-0038) is done and recorded in the addendum (2026-11-11). Both pre-live steps are complete.
- DR-0001 to DR-0027 are **accepted** (2026-11-08); DR-0028 to DR-0036 accepted 2026-11-09; DR-0037 and DR-0038 accepted 2026-11-10. Change them only through new, linked DRs. DR-0039 (held names on boards) accepted 2026-11-11. DR-0040 (F09 scoring), DR-0041 (exclude candidates that can only lose, from R2) and DR-0042 (rank on excess return, from R2) accepted 2026-11-18.
- Codex R1 review (at `b38b9b1`): 16 findings, 15 fixed with regression tests (2026-11-11); see `docs/review/R1.1-findings.md`, `R1.2-R1.5-findings.md` and `R1.6-R1.8-findings.md`. **R1.6 F09** (EVAL-1 §2.2 scoring of the underlying S_T/F forecasts) was built 2026-11-18 (DR-0040). Codex's check of the fixes (2026-11-11, `docs/review/R1-fixes-findings.md`): V01–V07 fixed 2026-11-18. The 5-day live run (11, 12, 13, 16 and 17 November) passed: 20 of 20 slots, 0 gaps, 0 failures, no manual intervention. The R1 memo is final (`docs/memos/R1-result.md`). Josh's Stop Point C decisions (2026-11-18) are in memo §9. The R1 extras are built (annotate command; F09, DR-0040), and the Mac is on the fixed code with the 3 V04 annotations written. **Carried into R2:** EVAL-2's execution-price measure, DR-0041 (eligibility) and DR-0042 (ADJ-1 ranking). Codex's review of the R1 extras (2026-11-19, `docs/review/R1-extras-findings.md`): X01–X06 fixed; DR-0043 (run-log code commit, BaselineRun schema 2) accepted 2026-11-20. The Mac pulls these fixes outside session hours.
- R0 amendment (2026-11-09): MOVEGEN-2 (the default) adds long straddles, strangles, long iron condors and long iron butterflies (DR-0035), and the replay month uses it. Still unchanged: MKT-1 stays risk-neutral with the VRP adjustment off, the exit policy stays HOLD-1, and option volume stays end-of-day, prior-session only.
- Text archive (DR-0036): the framework is accepted, but **don't build it before Stop Point C**. Sources: EDGAR, Massive ticker news and Fed/Treasury releases.
