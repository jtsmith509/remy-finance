# R1 review fixes: verification brief

**For:** Codex, or another independent reviewer, who reviewed `b38b9b1` and reported 16 findings (`REMY-R1-review-b38b9b1.zip`).
**Scope:** the fix commits on branch `claude/cool-pascal-80kptn`, from `b38b9b1` to `aa53762`:

| Commit | Fixes |
|---|---|
| `d935013` | R1.1 F01–F04 (registry), plus `Registry.add_events` |
| `341fac8` | R1.2 F01–F03 (engine, MKT-1) |
| `5d5d0f2` | R1.6 F01, F03, F05 (scheduler). It calls `Pipeline.held`, which arrives in `9e0a4fb`, so review the two together. |
| `9e0a4fb` | R1.6 F02: held names on boards (**DR-0039**) |
| `5bb6c72` | R1.7 F04, F06–F08 (reports) |
| `e73b65f`, `aa53762` | Findings files, runbook, DR-0039 accepted |

```bash
git diff b38b9b1 aa53762 -- src tests
```

**Goal:**
1. Confirm that each fix closes its finding.
2. Check that the fixes didn't break anything else.

The prospective live run started on 2026-11-11 with this code. Any defect you find now is fixed after the run, before Stop Point C, with a regression test.

**Not in scope:** F09 (EVAL-1 §2.2 underlying scores) is **deferred** by Josh's decision until after the live run. Don't re-report it.

## 1. Read first
1. `docs/review/R1.1-findings.md`, `R1.2-R1.5-findings.md` and `R1.6-R1.8-findings.md`. Each has a resolution table above your original report.
2. `docs/decisions/DR-0039-held-names-on-boards.md` (accepted). This is the chosen resolution of R1.6 F02, which differs from your suggested reproduction.
3. `docs/evaluation/EVAL-1-scoring.md` §1.4, §2.1 and §4. It is frozen, and it governs F04, F06 and F08.

## 2. Run (offline)
```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q tests/unit/review
uv run pytest -q   # 515 tests
```

Your original reproduction tests are worth re-running unmodified against `aa53762`. Expected: they all pass except two.
- **F09:** deferred.
- **R1.6 F02:** your test manages a board that lacks the held name. Under DR-0039 such a board isn't captured any more. The ported test, `test_f02_position_off_the_board_is_reported_not_silently_skipped`, checks that such a skip is logged.

## 3. What to attack, per fix

### Registry (`registry/store.py`)
1. **The export lock (F01).**
   - `export_lock` is an `flock` on `.<dir>.export.lock` beside the export directory.
   - The tick takes it once and calls `export(..., lock=False)` followed by `push_export`.
   - Questions:
     - Is there any path where two exporters still interleave?
     - Can `lock=False` be reached without the lock being held?
     - Can the lock deadlock with the tick lock or with SQLite?
2. **The read snapshot (F01, F04).**
   - `_read_snapshot` issues `BEGIN … COMMIT` unless the connection is already in a transaction.
   - Questions:
     - Is a WAL read transaction really consistent across all the `SELECT`s in `verify` and `export`?
     - What happens if `verify` is called inside a write?
3. **The trigger check (F02).**
   - Triggers are compared with their expected SQL after `_normalize_sql`.
   - Can a semantically different trigger normalise to the same text?
   - Can a correct trigger fail to match, for example after an SQLite version change?
4. **The kind column (F03).**
   - Is `kind` checked for every journal row?
   - Is it in the export line, and is that line format backward-compatible with exports already pushed?
5. **`add_events`.**
   - Each event is checked against the history plus the batch so far, all in one transaction.
   - Can a batch commit an event the single-event path would refuse, or the reverse?
   - Are the read caches correct after a batch that rolled back?

### Engine and MKT-1 (`execution/engine.py`, `baselines/mkt1.py`)
6. **Atomicity (F01, F02).**
   - Entry legs commit together.
   - A terminal event commits together with `TRADE_CLOSED` (`_with_close`).
   - Try a crash at every insert inside a batch. Then call `fill_entry`, `on_snapshot` and `settle` again. Is the outcome identical to a run with no crash?
   - Is any terminal path still committing alone? Look at exits, settlement, unfilled entries, assignment-forced closes, and exits retried to expiration.
7. **`repair_close`.**
   - It closes a flat, submitted, unclosed trade with no pending exit and a resolved entry.
   - Could it ever close a trade that isn't really finished? Consider stock held after assignment, a pending exit, or a partially recorded entry from an older registry.
8. **The clipped mean (F03).**
   - `shift_to_mean` bisects for the shift whose clipped mean equals the target, inside `return_bounds`.
   - Check bracket validity, monotonicity and the tolerance.
   - Check behaviour at a mean exactly at a bound.
   - Check that `INFEASIBLE_MEAN_SKIPPED:n` counts each skip once and that skipped candidates are never selected.
   - Does RAND-1 v3 still share MKT-1's forecasts exactly?

### Scheduler (`scheduler/`)
9. **Export retry (F01, `ops.push_export`).**
   - `_unpushed` compares HEAD with `git ls-remote origin refs/heads/<branch>`.
   - What if the remote is *ahead*, someone else pushed, or the branch is detached?
   - What if `ls-remote` fails transiently?
   - Is the `export` marker still written only after a successful push?
10. **The recommendation guard (F03, `tick._recommend`).**
    - After a failure, the slot's `-recommend` marker is `FAILED`. Any picks recorded before the failure are filled like any other.
    - Can a partial run (MKT-1 written, RAND-1 not, no run log) produce an inconsistent registry or run-log state?
    - Is "not retried" the right call given EVAL-1 §1.7 ("no exclusions after the fact")?
11. **Reconciliation (F05, `tick._reconcile` and `_late_session`).**
    - The watermark starts at the first tick's day and advances one session at a time.
    - Questions:
      - Can it skip a day, for example on holidays, half-days, a first tick on a non-session day, or a crash between sessions?
      - Can it capture or recommend late?
      - Can it record a gap over a stored snapshot, for example when a capture finished on time but its tick died before marking?
      - Is calling `fill_entries` for a late session with a stored execution board correct under the engine's window checks?
      - Can a late `fill_entries` record an entry using a clock far after the execution quote?
12. **Held names (F02, DR-0039; `scheduler/held.py`, `pipeline.py`, `live.py`, `replay.py`).**
    - Boards carry the members plus every held name. Held-only names are recorded in the provenance (`held_only`).
    - `recommend` filters candidates on `held_only` before scoring, and recomputes the list hash.
    - Questions:
      - Can a held-only name ever be recommended, through MKT-1, RAND-1 or a re-run that finds an existing run log?
      - Does adding provenance through `model_copy` in the replay keep the snapshot ID consistent with what is stored?
      - Is the per-day cap unaffected?
      - Does the execution board always cover the same held names as its decision board?

### Reports (`reports/`)
13. **The cohort (F04).**
    - Returns, scores, structure returns and ranks use `eval1_eligible(cutoff)`.
    - Activity, fills and P&L stay operational, and all-record results appear in "Operational view".
    - Is anything that EVAL-1 §1.4 restricts still computed on all records outside the labelled operational section?
    - Is the operational view clearly labelled everywhere it appears?
14. **PIT (F06).**
    - The draw is randomised only within a point mass (two or more equal quantiles), or within the unrecorded tail at or beyond the outermost quantile.
    - An interior singleton is a knot.
    - Is the bottom-tail rule right? A realized return equal to the lowest recorded quantile draws in [0, first level].
    - Are the mass boundaries right when equal quantiles sit at the edge of the grid?
15. **Engine commits (F07).** Does every report header list every (engine, version, code commit) combination with its count?
16. **The bootstrap (F08, `reports/bootstrap.py`).**
    - The method:
      - clusters by ISO week of expiration;
      - 10,000 resamples;
      - PCG64 seeded from the first 16 hex digits of `sha256("EVAL-1")`;
      - a pooled-mean ratio statistic;
      - a percentile interval;
      - "insufficient clusters" below 2.
    - Questions:
      - Is this a faithful reading of EVAL-1 §4?
      - Is the ratio statistic (pooled over resampled trades) right, as opposed to a mean of cluster means?
      - Is a minimum of 2 clusters defensible?
    - Is a p-value for P1 or P2 printed anywhere?

## 4. Already settled (don't re-report unless the implementation is wrong)
- Everything settled in the earlier briefs (DR-0028 to DR-0038).
- **DR-0039:** boards carry held names, and recommendations are member-only.
- **F09 deferred** until after the live run.
- **No code changes during the live run** (12–18 November). Fixes land afterwards.

## 5. How to report
- **For each original finding:** "closed", "partly closed" or "not closed", with the reason.
- **For each new finding,** one entry with:
  - **ID:** V01, V02, …
  - **Severity:** critical, high, medium or low.
  - **File and line.**
  - **What breaks.**
  - **A minimal reproduction:** ideally a failing pytest.
  - **Suggested fix.**

The results will be recorded in `docs/review/R1-fixes-findings.md`, with a regression test for each fix.
