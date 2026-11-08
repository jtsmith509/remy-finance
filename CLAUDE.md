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

## Status

- Current stage: **R0 code complete; at Stop Point B pending the live runs** listed in `docs/memos/R0-result.md`. Don't start R1 until Josh confirms.
- DR-0001 to DR-0025 are **accepted** (2026-11-08). Change them only through new, linked DRs.
