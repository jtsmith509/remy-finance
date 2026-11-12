# CLAUDE.md

Start here, then read the two governing documents in full before you change anything:

- **Build brief:** `docs/handoff/HANDOFF_R0_R1.md`. It gives scope, tasks, fixtures and stop points.
- **Spec:** `docs/spec/spec_v0.5.md`. It is an accepted-changes copy; the `.docx` beside it is the source of record.

If the brief and the spec disagree, **stop and ask Josh**. Don't resolve it yourself.

## Ground rules (handoff Section 0)

1. **Scope is R0 and R1 only.** Don't build R2–R6: no factor models, no causal search, no LSM policy optimizer, no neural or hybrid models, no volatility-risk-premium estimation. Leave clean seams such as interfaces and hooks, but don't stub those stages out in detail.
2. **Stage process.**
   - Every material design choice gets a decision record: `docs/decisions/DR-NNNN-short-title.md`. It covers context, options considered, decision and consequences. Status stays **proposed** until Josh confirms it. Settled decisions (handoff Section 1) are reopened only through a new, linked DR.
   - Each stage ends with a short result memo in `docs/memos/`: what was built, test results, known limitations and open questions.
   - **Stop points A, B and C (handoff Section 9) are hard stops.** Summarize what was built, the test results, the DRs awaiting confirmation and the specific questions for Josh. Then wait for his explicit confirmation.
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

- Current stage: **R1 in progress.** Stop Point B closed 2026-11-09 (`docs/memos/R0-result.md`). The next hard stop is **Stop Point C** at the end of R1.
- Agreed for R1: optimize the move generator early, keeping DR-0025's exactness (done). The spec §11 power estimate and the pre-registered scoring protocol **EVAL-1** were approved 2026-11-09 (`docs/evaluation/`). EVAL-1 is frozen, and changes go only through EVAL-2. Before the 5-day live run: (1) the EVAL-1 §6 record changes, done in DR-0032; (2) run the replay month (August 2026, DR-0034; runbook in `docs/runbook/scheduler.md`) and add a dated update to the power estimate. The first August run (MOVEGEN-1, MKT-1 v1) is archived; it was re-run with MKT-1/RAND-1 v2 (DR-0037), and the power addendum is written (2026-11-10). The third run with v3 (arbitrage-free SVI, DR-0038) is done and recorded in the addendum (2026-11-11). Both pre-live steps are complete.
- DR-0001 to DR-0027 are **accepted** (2026-11-08); DR-0028 to DR-0036 accepted 2026-11-09; DR-0037 and DR-0038 accepted 2026-11-10. Change them only through new, linked DRs. DR-0039 (held names on boards) accepted 2026-11-11.
- Codex R1 review (at `b38b9b1`): 16 findings, 15 fixed with regression tests (2026-11-11); see `docs/review/R1.1-findings.md`, `R1.2-R1.5-findings.md` and `R1.6-R1.8-findings.md`. **R1.6 F09** (EVAL-1 §2.2 scoring of the underlying S_T/F forecasts) is open, to be built after the 5-day live run. Codex's check of the fixes (2026-11-11, `docs/review/R1-fixes-findings.md`): V01–V07 are open, to be fixed after the live run. The R1 memo is drafted (`docs/memos/R1-result.md`), with the live-run section pending. The live run started 2026-11-11 (sessions 11, 12, 13, 16 and 17 November; day 1 clean); **no code changes on the Mac during it**.
- R0 amendment (2026-11-09): MOVEGEN-2 (the default) adds long straddles, strangles, long iron condors and long iron butterflies (DR-0035), and the replay month uses it. Still unchanged: MKT-1 stays risk-neutral with the VRP adjustment off, the exit policy stays HOLD-1, and option volume stays end-of-day, prior-session only.
- Text archive (DR-0036): the framework is accepted, but **don't build it before Stop Point C**. Sources: EDGAR, Massive ticker news and Fed/Treasury releases.
