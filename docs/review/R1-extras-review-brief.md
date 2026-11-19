# R1 extras: review brief

**For:** Codex, or another independent reviewer. You reviewed `b38b9b1` and then verified its fixes at `aa53762`.
**Scope:** the code added after the live run, from `aa53762` to `99c1a34` on branch `claude/cool-pascal-80kptn`:

| Commit | What |
|---|---|
| `a3aefe8` | Fixes for your V01–V03 and V07 (registry) |
| `a493978` | Fix for V04 (MKT-1 return bounds) |
| `e5b7146` | Fix for V05 (EVAL-1 cohort: closed by the analysis time) |
| `c42d650` | Fix for V06 (atomic watermark, recovery from markers) |
| `f1c98c2` | Known-defect annotations: `remy registry annotate`, report flags, a with/without sensitivity view (EVAL-1 §1.7) |
| `f6c94a6` | R1.6 F09: EVAL-1 §2.2 scores of MKT-1's S_T/F forecasts (**DR-0040**) |

```bash
git diff aa53762 99c1a34 -- src tests
```

The prospective scheduler on the Mac mini has run this code since the evening of 2026-11-18. Three live RAND-1 trades are annotated with defect V04. Stop Point C is closed, and anything you find is fixed as an R1 maintenance item with a regression test.

**Goal:**
1. Confirm V01–V07 are closed by these commits.
2. Find defects in the two new features: the annotations and F09.
3. Find anything these commits broke.

## 1. Read first
1. `CLAUDE.md`: the ground rules. The most important here are **no look-ahead**, **append-only records**, **Decimal-only cash** and **an offline test suite**.
2. `docs/review/R1-fixes-findings.md`: your V01–V07 and the resolution table, including the three probes adapted because the fix changed what they could observe (V04, V06).
3. `docs/evaluation/EVAL-1-scoring.md` §1.4, §1.7, §2.1, §2.2 and §4. It is frozen.
4. `docs/decisions/DR-0040-underlying-forecast-scores.md` (accepted). DR-0041 and DR-0042 are accepted for R2 and not built: don't review them as code.
5. `docs/memos/R1-result.md` §5, §6, §9 and §10: the live run, the V04 exposure, and the Stop Point C decisions.

## 2. Run (offline)
```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest -q tests/unit/review tests/unit/reports tests/integration/test_reports.py
uv run pytest -q   # 549 tests
```

Your four verification probe files are in the repository as `tests/unit/review/test_codex_r1_verify_*.py`. The three adapted cases say why in their comments.

## 3. What to attack (most important first)

### F09: underlying forecast scores (`reports/underlying.py`, `render._underlying_section`, CLI `report cumulative`)
1. **Look-ahead.**
   - Closes are read with `official_close_as_of(source, symbol, expiration, now)`.
   - Run logs decided after `now` are skipped.
   - Expirations after today count as not expired.
   - Questions:
     - Can a historical report (`now` in the past) ever score a close, or use a run log, that wasn't public at `now`?
     - Is an expiration-day report before 17:15 ET handled correctly?
2. **The realized value.** It is close ÷ the fit's recorded forward.
   - What happens when the underlying splits, pays a special dividend or changes ticker between the decision and the expiration?
   - Should such forecasts be excluded, adjusted or annotated?
   - Note that DR-0021 makes splits known only on their execution date.
3. **Which observations count.** Only `engine == "MKT-1"` run logs are counted, so RAND-1's copies aren't counted twice.
   - What if a session's MKT-1 run log is missing but RAND-1's exists? The guarded recommendation step (R1.6 F03) makes a partial run possible.
   - Are fits lost then, or could they be double-counted on a re-run?
4. **The close source.**
   - After the first exception, `_Closes` stops asking and counts the rest as unavailable. Is one transient error, or one bad symbol, enough to silence the whole section? Is that the right trade-off?
   - A close the source never has (a delisted name) is "waiting" forever. Should it be reported differently?
5. **Scores.**
   - The CRPS is the quantile form, in S_T/F units.
   - The log score is −log(Δlevel ÷ Δvalue) on the segment holding the value, floored at 10⁻⁴ outside the grid.
   - Check `density_at` at segment boundaries, with repeated quantile values, and at exactly the outermost quantiles.
   - Is anything mislabelled as comparable to the §2.1 strategy scores?
6. **An offline suite.** `report cumulative` now builds a `ThetaDataSource` by default, and tests pass `--no-closes`. Can any test path still reach a network or localhost socket?

### Known-defect annotations (`registry/notes.py`, `reports/outcomes.py`, `reports/render.py`, CLI `registry annotate`)
7. **Append-only and idempotent.**
   - `annotate` refuses an unknown trade and skips a note already present.
   - Two annotators racing could both pass the check. Does that matter?
   - Run the annotation while a scheduler tick writes the same registry. Is there any `database is locked` path, and is it handled?
8. **Lifecycle.**
   - ANNOTATION is allowed at any time, including after `TRADE_CLOSED`.
   - Can an annotation change a trade's status, `closed_at`, cash, P&L or EVAL-1 eligibility in `outcome_of`?
   - Does `verify` still pass, and is the annotation exported as an ordinary journal row?
9. **Parsing.**
   - `defect_ids` matches notes starting `DEFECT <id>:`. Can any engine-written ANNOTATION, such as "ASSIGN-1 not assessed…", be mistaken for a defect?
   - Does the id pattern accept or reject what it should?
10. **The sensitivity view.**
    - EVAL-1 §1.7 asks for results "with and without" annotated trades. Check `_sensitivity`'s scope counts and cohort.
    - Check that the "without" tables really drop the annotated trades.
    - Check that the section is absent when nothing is annotated.
    - Is a trade with several defects handled?

### The V01–V07 fixes
11. **V01, legacy export lines.**
    - A journal line without `kind` is accepted if it equals the current line minus `kind`.
    - Can a forger use the legacy form to hide a change, for example to the SQL `kind` column? Assume `_check_record` still compares the column with the event.
12. **V02 and V03, locks.**
    - The export lock is named from the resolved directory.
    - `verify(export_dir=…)` takes it before its read snapshot, unless `lock=False`.
    - `flock` on a second open file descriptor blocks even within one process. Is there any path that calls `verify(export_dir=…)` while already holding the export lock (the scheduler tick, `registry export`, `doctor`)? It would deadlock.
    - What does `resolve()` do with a relative path, a missing directory, or a symlink re-pointed between runs?
13. **V04, return bounds.**
    - The bounds now come from signed `payoff_extremes`.
    - The memo says engine versions can stay v3 because no *feasible* candidate's forecast changes. Is that true for every structure, including credit structures and those whose best case is exactly 0 after fees?
14. **V05, the cohort.** It now requires `closed_at <= now`. Check the boundary and time-zone semantics against EVAL-1 §1.4.
15. **V06, the watermark.**
    - It's replaced atomically, with a temporary file, `fsync` and `os.replace`.
    - An unreadable one is recovered as the earliest marker date.
    - Can recovery skip a missed day, record a gap over a stored snapshot, or reconcile days from before the scheduler first ran?
    - Is the temporary file name safe if two ticks ever overlap? The tick lock should prevent it.

## 4. Already settled (don't re-report unless the implementation is wrong)
- **DR-0040:** every fit on every board is an observation; a linear density with a 10⁻⁴ floor; closes fetched at report time and not stored.
- **DR-0041 and DR-0042:** these are R2 work and not built.
- **EVAL-1 is frozen.** The execution-price measure belongs to EVAL-2 (R2).
- **The 3 V04 trades stay in the record, annotated, never edited.**

## 5. How to report
- **For each of V01–V07:** "closed", "partly closed" or "not closed", with a one-line reason.
- **For each new finding,** one entry with:
  - **ID:** X01, X02, …
  - **Severity:** critical, high, medium or low.
  - **File and line.**
  - **What breaks.**
  - **A minimal reproduction:** ideally a failing pytest, in a separate file.
  - **Suggested fix.**

Deliver findings only: no edits to production code or existing tests, and no commits or pushes. Use no network and no real market data. The results will be recorded in `docs/review/R1-extras-findings.md`, with a regression test for each fix.
