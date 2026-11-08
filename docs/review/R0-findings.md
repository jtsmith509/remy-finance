# R0 independent review: findings and resolution

- **Reviewer:** Codex (independent), following `docs/review/R0-review-brief.md`
- **Reviewed commit:** `aeda0c27980a343664a3f1de4b1c5c4045d9a05e` (2026-11-08)
- **Report delivered:** to Josh, who passed it on for fixing. The reviewer's companion test file (`test_r0_review.py`) was not supplied, so equivalent regression tests were written from the report's reproductions.

## Resolution

Each finding was first **checked against the code** (and F04 was reproduced numerically) before it was fixed. Each fix has **regression tests**, in `tests/unit/review/test_r0_review_fixes.py` and `tests/integration/test_cli_snapshot.py`. **Every one of them was run against the reviewed commit, in a separate worktree, to show it fails there**, and then shown to pass on the fix. A few tests pass on both commits by design: they guard behaviour that was already correct, so a fix can't overshoot.

| # | Severity | Finding | Fix | Commit | Tests failing on `aeda0c2` |
|---|---|---|---|---|---|
| F01 | blocker | Snapshot and gap finality not atomic per slot | Per-slot `flock` held across re-check and publication, by both `write()` and `record_gap()`; gaps become per-slot files published with `os.link` (no overwrite). **DR-0024** amends DR-0022. | `55fefe3` | 3 of 4 (e.g. `['stored', 'stored']`) |
| F02 | blocker | The live quote handler swallowed `LookaheadError` | The prospective builder uses the same per-read guard as replay; `LookaheadError` always propagates | `059f987` | 2 of 2 |
| F03 | blocker | Structure factory bypassed float rejection | `core.types.to_decimal` rejects float and bool *before* conversion. It's applied to entry prices, strikes, S_T and the official close. The same defect in `OptionContract.build` (strike) was **found during the fix** and fixed too. | `6af0295` | 6 of 7 |
| F04 | blocker | Large terminal prices erased a bounded spread's payoff | `core.exact.exact_decimal`: precision computed from the operands' digit span plus headroom, with `Inexact` trapped. It wraps all payoff, P&L, settlement, fill and assignment maths. **DR-0025.** | `6af0295` | 6 of 9 (e.g. P&L −151.30 vs +348.70 at 1e29) |
| F05 | major | Missed prospective capture not recorded as a gap | `snapshot take` records gaps on a late start, a capture overrun (`LateSnapshotError`) or a capture failure; never over a stored snapshot, nor on non-session days or early starts | `aa66b44` | 4 of 5 |
| F06 | major | A dividend failure suppressed an independent corporate-action read | Each event read is guarded separately; `PolygonSource.corporate_actions` keeps splits when the dividend feed fails | `059f987` | 2 of 2 |
| F07 | major | An interrupted universe write left a partial, permanently blocking file | `fileio.publish_file`: temporary file + fsync, then `os.link` (write-once), shared with the gap files | `643236b` | 1 of 2 |
| F08 | minor | File fixtures couldn't reload `OptionVolume` | Registered in `FixtureSource._TYPES` | `643236b` | 1 of 1 |

**Gate after the fixes:** `ruff check`, `ruff format --check` and `mypy` (43 files) are clean; `pytest`: **289 passed**.

**New decision records awaiting Josh's confirmation:** DR-0024 (slot finality, amending DR-0022) and DR-0025 (exact Decimal arithmetic and strict inputs).

**Pattern behind the bugs:** F02 and F06 existed because the replay and prospective builders kept *separate* copies of the error-handling logic, and only one had been fixed after the first live run. Both builders now share one per-read guard (`_read`) and one assembly step (`_assemble`).

---

## The reviewer's report (as delivered)

> **Recommendation: resolve the blockers before R1 builds on R0.** Eight findings: four blockers, three major defects, and one minor defect. Nine failing reproduction tests accompany this report. Severity follows the review brief: wrong cash/payoff, broken immutability, and look-ahead safeguards are blockers. F04 is an extreme-input precision defect, not evidence of ordinary market-price failures.

- **F01, blocker:** snapshot and gap finality are not atomic per slot (`snapshots/store.py:267`, `:287`, `:328`; gap checks at `:229`). Two writers with different content both pass `find()`, so both renames succeed; a gap can commit between a write's checks and its rename.
- **F02, blocker:** the live quote handler swallows `LookaheadError` (`snapshots/builder.py:253–257`).
- **F03, blocker:** the structure factory bypasses float rejection for cash (`core/structure.py:117`): `0.1` became `0.1000000000000000055511151231257827021181583404541015625`.
- **F04, blocker:** valid large terminal prices erase a bounded spread's payoff (`core/payoff.py:28`, `:33`, `:38`): P&L −151.30 instead of +348.70 at `1e29`.
- **F05, major:** a missed prospective capture is not recorded as a gap (`cli.py:203–205`, `:231–240`).
- **F06, major:** a failed dividend read suppresses the independent corporate-action read (`snapshots/builder.py:265–271`).
- **F07, major:** interrupted universe writes permanently publish a partial decision (`universe/selection.py:188–190`).
- **F08, minor:** file-based fixtures cannot reload option-volume records (`data/fixture.py:30–39`).

**Reviewer's validation on the unmodified commit:** ruff and format passed, and mypy passed (41 files). pytest: 257 passed. The property suite passed with seeds 17, 42 and 2026. The 9 additional reproductions failed.

**Not covered by the review:** live ThetaData or Polygon validation, and the prospective trading-day capture. The review does not approve R1.
