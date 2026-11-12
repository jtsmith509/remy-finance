# R1 fixes verification: findings and resolution

- **Reviewer:** Codex (independent), following `docs/review/R1-fixes-review-brief.md`
- **Verified commit:** `aa5376237f3c7665f9bef04c834c04e016a3a83a` (the fixes `b38b9b1..aa53762`)
- **Report delivered:** 2026-11-11, as `REMY-R1-fixes-review-aa53762.zip`, with 25 new offline probes (17 pass, 8 fail).

## Result

- **Original findings:** 9 of 15 closed and 6 partly closed. R1.6 F09 stays deferred.
- **New findings:** V01–V07 (one high, five medium, one low; none critical).
- **Reproduced here:** all 8 failing probes fail on `aa53762` exactly as reported, and the 17 passing ones pass.

**A correction to our brief.** The brief said only two of Codex's original tests would still fail. That was wrong. The findings files already record that the original R1.1 F01 and R1.2 F01/F02 tests were *ported*: they patch `Registry.add_event` (no longer called one event at a time) or run a second exporter synchronously inside the first (it now waits on the lock). Unmodified, they give 12 passed, 6 failed and 1 bounded timeout, as Codex reports. None of these is a production defect. Codex's nine replacement crash probes (crash after real inserts, reopen, retry) all pass.

## Live-run impact (the run started 2026-11-11 on `aa53762`)

None of V01–V07 needs the live run to stop or the code to change mid-run.

| # | Severity | Can it affect the live run? |
|---|---|---|
| V01 | high | **No.** No pre-fix export exists. The `registry-export` branch was created on 2026-11-11, and the prospective registry was created by the first tick with the new code. Every exported line carries `kind`. |
| V02 | medium | **No.** One export path is configured, with no symlink alias. |
| V03 | medium | **Only as a transient false alarm.** A `doctor` or `registry verify` run at the moment of the 17:30 ET export could report missing rows. Re-running clears it, and no record is affected. |
| V04 | medium | **Marginal.** It affects only structures whose executable debit exceeds their width, which are guaranteed losses. MKT-1 never selects them, because their predictions are far below the top. RAND-1 could, and its recorded quantiles would then sit a few hundredths of a percent above the true maximum. If any appear in the live records, they will be listed in the R1 memo. |
| V05 | medium | **No.** It affects historical reports only. The EVAL-1 cohort is empty during the run. |
| V06 | medium | **Unlikely.** It needs a crash during the few-byte watermark write. Today's sessions would still run, but catch-up after a later outage would stop. |
| V07 | low | **No.** The returned row is only an API object; export and verify re-read stored rows. |

## Resolution

**Fixes are applied after the live run (last session Tuesday 17 November)** (Josh, 2026-11-11: no code changes during it), each with Codex's probe as its regression test.

| # | Finding | Planned fix | Commit |
|---|---|---|---|
| V01 | Pre-fix journal export lines (no `kind`) fail verification | Accept exactly the pre-fix line format (the current line minus `kind`) for journal rows, still checking the SQL kind against the event. Historical bytes are never rewritten. | after the run |
| V02 | Symlink aliases bypass the export lock | Name the lock from the resolved export directory | after the run |
| V03 | Verification's database snapshot races a newer export file | `verify(export_dir=…)` takes the export lock before its read snapshot; the caller can say it already holds it | after the run |
| V04 | `return_bounds` uses the clamped `max_gain` | Endpoints from signed `payoff_extremes` minus fees over capital at risk; an infeasible mean is then skipped and counted | after the run |
| V05 | The cohort doesn't require closure before the cutoff | Cohort = decided ≥ 61 days before the cutoff **and** closed at or before the report time | after the run |
| V06 | An interrupted watermark write stops reconciliation for good | Atomic write (temporary file, fsync, replace). A malformed watermark is recovered from the earliest scheduler marker, never by backfilling captures | after the run |
| V07 | The appended `Row` lacks its kind | `_insert` passes `kind` into the returned `Row` | after the run |

---

## The reviewer's report (as delivered)

# R1 fix verification: b38b9b1 → aa53762

**Repository:** `jtsmith509/remy-finance`  
**Branch:** `claude/cool-pascal-80kptn`  
**Verified commit:** `aa5376237f3c7665f9bef04c834c04e016a3a83a`  
**Original review:** `REMY-R1-review-b38b9b1.zip`

**Result: 9 of the 15 findings are closed; 6 are partly closed.** Seven new/residual defects are recorded below: one high, five medium, one low. None is classified critical. F09 remains owner-deferred and is not re-reported.

The fix brief was absent from the locally available commits. Josh supplied its full text during this review; that text governed the verification. The diff `b38b9b1..aa53762 -- src tests` and the resolution tables were reviewed, including **5d5d0f2 and 9e0a4fb together**, accepted DR-0039, and frozen EVAL-1. Findings only: no production/existing-test edits, commits, or pushes. All work used local source, synthetic fixtures, cached dependencies, and temporary local Git remotes. No network, real market data, secrets, or API keys were used.

## 1. Disposition of each original finding

“Partly closed” includes a residual failure or a regression introduced by the fix. It does not mean that the original happy-path reproduction still fails for the same reason.

| Original finding | Status | One-line reason |
|---|---|---|
| R1.1 F01 — export race | **Partly closed** | Same-path exporters serialize correctly, but symlink aliases use different lock files and can still erase published rows (V02). |
| R1.1 F02 — trigger definitions | **Closed** | Verification checks definitions and detects no-op and retargeted guards; no harmful normalization collision established. |
| R1.1 F03 — journal kind | **Partly closed** | Stored kinds are checked and new exports include them, but existing exports become incompatible and returned event rows omit the kind (V01, V07). |
| R1.1 F04 — consistent verification | **Partly closed** | Database SELECTs share a WAL snapshot; concurrent JSONL publication can still produce a false integrity failure (V03). |
| R1.2 F01 — entry cash after crash | **Closed** | All leg fills commit atomically; post-insert crash/reopen/retry probes preserve complete cash and avoid duplicates. |
| R1.2 F02 — missing terminal summary | **Closed** | Terminal results and summaries commit together; direct repair of resolved legacy histories is present and new-write crash paths recover. |
| R1.2 F03 — clipped forecast mean | **Partly closed** | Bisection fixes both original mean probes, but the new bounds admit impossible forecasts for guaranteed-loss structures (V04). |
| R1.6 F01 — export push retry | **Closed** | A clean checkout with an unpushed commit now retries; failed remote operations do not write the export completion marker. |
| R1.6 F02 — held names | **Closed** | DR-0039 adds held names to live/replay captures, preserves held-only provenance and stored IDs, and excludes them from both baselines and reruns. |
| R1.6 F03 — recommendation guard | **Closed** | Recommendation errors are recorded separately; execution capture, fills, and management continue for already recorded positions/orders. |
| R1.6 F04 — analytical cohort | **Partly closed** | Decision-age eligibility now applies, but closure before analysis cutoff is still unchecked (V05). |
| R1.6 F05 — missed-day reconciliation | **Partly closed** | Normal missed-day catch-up works; a partial watermark write can permanently disable subsequent reconciliation (V06). |
| R1.6 F06 — PIT knots | **Closed** | Interior singleton knots retain their probability; supported point masses and edge-tail handling follow the stated rules. |
| R1.6 F07 — engine commits | **Closed** | Headers retain every engine/version/code-commit combination with its record count. |
| R1.6 F08 — CRPS intervals | **Closed** | The pooled ISO-week bootstrap matches an independent scalar reference, including unequal cluster sizes, fixed PCG64 seed, 10,000 draws and insufficient-cluster output. |

**F09:** deferred by Josh until after the live run; outside this verification's findings. The original F09 test was still run unchanged, as requested, and its failure is reported only in the test accounting below.

## 2. New verification findings

### V01 — Legacy REGISTRY-1 journal exports now fail integrity verification

- **Severity:** high.
- **File and line:** `src/remy_finance/registry/store.py:497`.
- **What breaks:** Existing journal export lines written by b38b9b1 have no top-level `kind`. The new verifier compares those unchanged historical bytes against the new `Row.export_line()` format, which adds `kind`, and reports the row as “missing or changed.” A healthy registry and its already committed export therefore fail `verify --export-dir`; the CLI export preflight refuses further export, and doctor reports an integrity failure. The database schema remains `REGISTRY-1` and no legacy-line handling is supplied. This is a compatibility regression, not actual tampering with the old files.
- **Minimal reproduction:** `test_v_existing_unchanged_registry1_export_remains_verifiable` in `test_registry_verification.py`. Export one submitted trade, reproduce the exact old canonical line format by omitting the newly added top-level field, preserve its bytes, and verify. Actual: `exported ('journal', 1) is missing or changed`.
- **Suggested fix:** Accept and validate the exact legacy format without rewriting historical committed bytes. Continue checking the SQL kind against canonical event JSON. Explicitly version future export formats; do not “repair” old external evidence in place.

### V02 — Symlink aliases bypass the export lock and can still erase rows

- **Severity:** medium.
- **File and line:** `src/remy_finance/registry/store.py:128`–`131`.
- **What breaks:** The lock is named from the spelling of the destination. `export` and a directory symlink `export-link` write the same JSONL files but lock `.export.export.lock` and `.export-link.export.lock`. Concurrent exports through those paths therefore interleave: B publishes two rows, then A publishes its stale one-row version. The same-path race is fixed; destination identity is not.
- **Minimal reproduction:** `test_v_registry_export_alias_cannot_bypass_export_lock`. Two real registry handles and a temporary directory symlink force the read/publish interleaving with a bounded worker. Actual: the alias exporter bypasses exclusion, and the final file retains one row instead of two.
- **Suggested fix:** Derive the lock identity from a canonical resolved destination, consistently for direct exports and the scheduler's write/commit/push critical section. Ensure ordinary path aliases share one lock.

### V03 — Verification's database snapshot can race a newer export file

- **Severity:** medium.
- **File and line:** `src/remy_finance/registry/store.py:399`, `:490`–`:498`.
- **What breaks:** `verify(export_dir=...)` pins a database snapshot but does not coordinate its file reads with exporters. Another handle can append and export row 2 after that snapshot is established. Verification reads the newer JSONL against the older database snapshot and falsely reports missing/changed rows. A database-only consistent snapshot is not a consistent database-plus-files view.
- **Minimal reproduction:** `test_v_verify_export_is_consistent_with_concurrent_export`. After verification's first real recommendation read, a bounded worker appends and exports a second valid recommendation. Verification incorrectly reports the second exported row missing. The worker-based reproduction also allows a corrected implementation to block export until verification releases its lock.
- **Suggested fix:** Coordinate export-file inspection with the canonical export lock, acquired before establishing the database snapshot, or provide another consistent database/file snapshot protocol. Preserve lock ordering and support callers already holding the lock to avoid nested deadlocks.

### V04 — Attainable return bounds are wrong for guaranteed-loss structures

- **Severity:** medium.
- **File and line:** `src/remy_finance/baselines/mkt1.py:364`.
- **What breaks:** `return_bounds()` uses `Structure.max_gain`, which clamps a negative best contractual P&L to zero. For a spread whose executable debit exceeds its width, the helper therefore invents an upper return above the actual maximum. An infeasible anchored mean passes screening and the new shift solver manufactures quantiles beyond the structure's possible payoff.
- **Minimal reproduction:** `test_return_bounds_use_signed_payoff_extremes` and `test_infeasible_mean_does_not_generate_unattainable_quantiles` in `test_execution_verification.py`. The existing synthetic equity chain with 2¢ half-spreads and 1¢ ticks has a 75C/80C debit vertical with:
  - true maximum net return: **−0.010488818523649317**;
  - claimed upper bound: **−0.002572729071838512**;
  - retained anchored mean: **−0.010165428610790856**;
  - recorded quantiles around **−0.01014257**, exceeding the true maximum.
- **Suggested fix:** Derive endpoints from signed `payoff_extremes` minus fees, divided by capital at risk. Do not derive attainable endpoints from clamped nonnegative loss/gain summary fields. Then apply the existing infeasible-mean exclusion and count it once.

### V05 — Age-eligible outcomes closed after cutoff still enter historical reports

- **Severity:** medium.
- **File and line:** `src/remy_finance/reports/render.py:255`.
- **What breaks:** The analytical cohort checks decision age and current closed status, but not whether `closed_at` precedes the requested analysis cutoff. A historical report can therefore score an outcome that became available only later. Frozen EVAL-1 §1.4 requires both closure before cutoff and the 61-day decision-age rule.
- **Minimal reproduction:** `test_f04_cohort_excludes_a_trade_closed_after_analysis_cutoff` in `test_reports_verification.py`. Supply an age-eligible trade whose close is one day after the cutoff. Actual: it enters `stats.closed` and its forecast is scored; expected: no analytical outcome yet.
- **Suggested fix:** Apply both eligibility and closure-before-cutoff tests, with explicit date/time boundary semantics, and reuse the resulting cohort for returns, scores, structures, and ranks. Keep labelled operational reporting separate.

### V06 — An interrupted watermark update permanently breaks reconciliation

- **Severity:** medium.
- **File and line:** `src/remy_finance/scheduler/tick.py:204`, `:210`.
- **What breaks:** The mutable watermark is overwritten directly with `Path.write_text`. An interruption after truncation leaves an empty or partial date. Every later tick then raises during `date.fromisoformat`; the guard records the error and continues today's work, but never repairs the watermark or reconciles subsequent missed days. Slot finality remains broken after this crash.
- **Minimal reproduction:** `test_watermark_update_failure_does_not_permanently_disable_reconciliation` in `test_scheduler_verification.py`. Start with Monday's valid watermark; on Tuesday inject a write failure after truncation while advancing it; resume Wednesday. All four Tuesday slots remain neither stored nor gaps.
- **Suggested fix:** Publish watermark updates using a temporary file, flush/fsync, and atomic replacement, retaining the prior valid watermark until publication completes. Supply a safe recovery path for already malformed watermark files, using durable scheduler evidence without backfilling prospective captures.

### V07 — The event append API returns a Row without its stored kind

- **Severity:** low.
- **File and line:** `src/remy_finance/registry/store.py:557`.
- **What breaks:** `_insert` writes the supplied kind into SQL but constructs its returned `Row` without it. Thus `add_event()`/`add_events()` return `kind=None`, while `rows('journal')` returns the actual kind. The returned and reread rows, including their `export_line()` bytes, disagree. Normal `Registry.export()` rereads rows and includes the field correctly, so this is an API consistency defect rather than that normal export path dropping the field.
- **Minimal reproduction:** `test_f03_returned_event_row_matches_stored_export_row` in `test_registry_verification.py`. Append `ORDER_SUBMITTED`; compare the returned row with the stored row. They differ at `kind`, and their export lines differ.
- **Suggested fix:** Pass the supplied `kind` when constructing the `Row` in `_insert`.

## 3. Gate results

| Gate at aa53762 | Result |
|---|---|
| `uv sync --offline` with cached locked packages | Passed; Python 3.12.16 |
| Ruff lint | Passed |
| Ruff format check | Passed: 191 files |
| mypy | Passed: 70 source files |
| `uv run --offline pytest -q tests/unit/review` | **51 passed** |
| Full repository pytest | **515 passed** |
| Independent new verification tests | **17 passed, 8 failed**, supporting the seven findings above |
| New test-file lint and format | Passed |

The final gate rerun used `uv sync --offline` followed by `uv run --offline` for every listed check. No dependency or command fetched over the network.

The passing new cases include same-path export exclusion, retargeted-trigger detection, multi-trade rollback/cache safety, nine post-insert crash/reopen/retry cases, independent scalar bootstrap comparison, PIT mass boundaries, complete header provenance, and DR-0039 replay persistence/member-only selections/rerun idempotence. Existing adapted tests also exercise the scheduler's capture/held-name wiring. No new finding was made merely because `lock=False` can be deliberately invoked without its documented caller-held lock, because the remote rejects an unsafe push, or because the owner chose a particular bootstrap convention.

## 4. Original reproductions, unmodified

The four files were copied **byte for byte from the originally delivered ZIP**, not edited or ported. Their SHA-256 values and the captured commands/results are included in `reproduction-results.txt`; the files are under `original-tests/`.

| Original module/cases | Result at aa53762 | Interpretation |
|---|---|---|
| Registry F02–F04 | **3 passed** | Original intended assertions now succeed. |
| Registry F01 | **Timed out after 8 seconds** | The original injects a synchronous second exporter while the first holds the new non-reentrant lock; it blocks rather than completing its old race. New bounded-worker tests verify exclusion without this harness deadlock. |
| Execution/baselines | **2 passed, 4 failed** | Both forecast probes pass. Four crash probes fail at `pytest.raises(SimulatedCrash)` because they patch `Registry.add_event`, which the new atomic batch paths no longer call. They do not reach their old outcome assertions. Nine replacement probes crash after real inserts, reopen, retry, and pass. |
| Scheduler | **3 passed, 1 failed** | The sole failure is the original off-board held-name assertion superseded by DR-0039. The new DR-0039 persistence/filtering check passes. |
| Reports | **4 passed, 1 failed** | The sole failure is F09, explicitly deferred and not re-reported. |
| **Total original cases** | **12 passed, 6 failed, 1 bounded timeout** | Four failures and the timeout are obsolete injection assumptions; the other two failures are the owner-declared exceptions. |

Therefore the brief's literal expectation that only two unmodified tests fail is **not** the observed result. The implementation's own resolution notes already explain why the export and crash tests were ported. This review preserves the original results and distinguishes those test-harness limitations from production defects.

## 5. Delivery and limits

`reproduction-results.txt` contains the baseline gate summary, the complete original-test rerun output, and the complete new verification output. The ZIP includes this report, the four untouched original files, and four separate new verification files. No fix has been applied; remediation remains after the live run in accordance with the owner's instruction. F09 remains outside scope.
