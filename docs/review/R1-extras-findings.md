# R1 extras: findings and resolution

- **Reviewer:** Codex (independent), following `docs/review/R1-extras-review-brief.md`
- **Reviewed source:** `fc8b424`, the Mac's checkout. Its `src/` and `tests/` are identical to `99c1a34`, which Josh confirmed.
- **Report delivered:** 2026-11-19, as `REMY-R1-extras-review-99c1a34.zip`, with 15 offline probes (8 pass, 7 fail) and a socket guard showing 0 connection attempts.

## Result

- **Earlier findings:** V01, V05 and V07 are closed. V02, V03, V04 and V06 were partly closed, by X05, X04, X01 and X06 respectively.
- **New findings:** X01–X06, all medium.
- **Reproduced here:** all 7 failing probes fail on HEAD exactly as reported.
- **Fixed:** all six on 2026-11-19, one commit per area.
- **Regression tests:** Codex's probes, in `tests/unit/review/test_codex_r1_extras_*.py`. **8 fail on the pre-fix code (`6393ed2`) and all 16 pass on the fix**: 15 probes plus one added test that schema-1 run logs still load.
- **Gate:** ruff, format and mypy clean; pytest **565 passed**.
- **Offline guard:** Codex's socket guard is adopted suite-wide (`tests/conftest.py`). Any connection attempt now fails the test that makes it.

| # | Finding | Fix | Commit |
|---|---|---|---|
| X01 | The lower return bound was hardcoded to −1 | `return_bounds` uses the signed minimum P&L minus fees, over capital at risk. It is exactly −1 for every structure that can lose its capital at risk, and above −1 only for structures that can only win (inconsistent quotes), whose impossible means are now skipped. The probe was adapted the same way as V04: the candidate is now skipped, so it's found among the generated candidates. | `87de9e3` |
| X02 | Scored null-run forecasts lacked code provenance | **DR-0043 (proposed):** `BaselineRun` schema 2 records `code_commit`, and schema-1 logs still load as "not recorded". Report headers include the scored run logs' engine/version/commit with counts. | `5007860` |
| X03 | Sensitivity omitted P&L and other results | The sensitivity view repeats every result of its scope: returns and forecast scores always; P&L, drawdown and events for the operational scope; the structure and rank tables for the EVAL-1 cohort | `5007860` |
| X04 | Verify skipped the lock before the first export | `verify(export_dir=…)` takes the export lock whenever a directory is given, before its read snapshot | `4fee3a3` |
| X05 | A repointed alias redirected I/O outside the lock | `export` and `verify` resolve the destination once and use that path for the lock and every file operation | `4fee3a3` |
| X06 | Watermark recovery invented pre-deployment gaps | Recovery uses only session markers (open/close), which exist only from the first tick on, never the nightly export/backup markers dated for the previous session | `7f8a2c3` |

## Codex's qualification on engine versions

The R1 memo (§9, question 4) said the V01–V07 fixes could keep engine version v3 "because no feasible forecast changes". **That was wrong as stated.** Codex showed a feasible 85C/87.5C debit spread whose 1% quantile changed (−0.222 to −0.092).

The precise statement is narrower. I checked it: that spread's best P&L is −$3.00, so it can only lose. The V04 and X01 fixes change forecasts **only for structures whose best case is a loss, or whose worst case is a gain**. MKT-1 never selects either kind, and from R2, DR-0041 excludes the first. Every live RAND-1 pick of that kind recorded before the fix was found by the V04 exposure check: 4 trades, of which 3 are annotated, while the 4th's forecast stayed within its bounds. Whether that justifies keeping v3 is put to Josh.

---

## The reviewer's report (as delivered)

# R1 extras: independent review findings

**Repository:** `jtsmith509/remy-finance`  
**Branch:** `claude/cool-pascal-80kptn`  
**Requested scope:** `aa53762..99c1a34 -- src tests`  
**Source actually inspected:** `fc8b4245aa059c5adea983fda1a1aa8398db2c8c`, in an isolated local clone  
**Scope confirmation:** Josh confirmed that `fc8b424..99c1a34` and `fc8b424..6393ed2` contain no `src/` or `tests/` changes. The corresponding source/test diff is identical. The extras brief was supplied in chat and followed as given.

**Result:** V01, V05 and V07 are **closed**. V02, V03, V04 and V06 are **partly closed**, with the residual cases below. Six new/residual findings are reported, all **medium** severity. No critical or high-severity finding was established.

All review files, environments and output are outside the production repository. The production checkout was only read. No production code, existing test, tracked file, staging state, commit or remote was changed. No network, localhost connection, secret or API key was used. Tests used synthetic fixtures and temporary local Git repositories; the requested source documents and memo were read for context, not used as live test data.

## 1. V01–V07 dispositions

| Finding | Status | One-line reason |
|---|---|---|
| V01 — legacy export compatibility | **Closed** | Exact legacy lines remain byte-compatible without rewriting history, while the SQL kind is still checked against canonical event JSON. |
| V02 — destination aliases | **Partly closed** | Stable aliases now share a resolved-path lock, but an alias repointed during an export can redirect I/O outside the held lock (X05). |
| V03 — database/export consistency | **Partly closed** | Existing destinations are locked before the read snapshot; a concurrent first export can still race verification when the directory does not yet exist (X04). |
| V04 — attainable return bounds | **Partly closed** | Signed upper endpoints and the original infeasible-mean cases are fixed, but the lower endpoint is still hardcoded to −1 and can admit impossible forecasts (X01). |
| V05 — report closure cutoff | **Closed** | The cohort now checks both decision age and `closed_at <= now`; the independent future-close test passes. |
| V06 — watermark crash/recovery | **Partly closed** | Atomic replacement fixes the partial-write path, but recovery from marker dates can invent gaps before the scheduler first ran (X06). |
| V07 — returned Row kind | **Closed** | The append API returns the actual SQL kind, matching reread rows and their export bytes. |

The adapted verification probes in `tests/unit/review/test_codex_r1_verify_*.py` were inspected. Moving V04 probes from scored to generated candidates is appropriate when infeasible forecasts are now skipped. Moving the watermark injection to `os.replace` checks the new publication boundary; the additional malformed-watermark case checks legacy recovery. These adaptations do not by themselves weaken the original intended assertions.

## 2. New findings

### X01 — The attainable lower return endpoint remains incorrect

- **Severity:** medium.
- **File and line:** `src/remy_finance/baselines/mkt1.py:369`–`371`.
- **What breaks:** The fix obtains signed payoff extrema but discards `_low`, returning `−1` unconditionally. When a generated structure has a strictly positive minimum contractual P&L, its attainable return floor is above −1. An anchored mean below that real floor is consequently retained and the shifted forecast can describe impossible losses.
- **Minimal reproduction:** `test_signed_lower_bound_is_used_for_generated_guaranteed_profit_candidate` and `test_infeasible_below_minimum_mean_is_not_scored` in `test_execution_annotation_extras.py`. Starting from the existing synthetic chain, set public rates to zero and give the deep-ITM 75C/80C zero-spread quotes of 20/21. These deliberately stale/nonmonotone cross-strike quotes leave the near-ATM parity and OTM smile usable. Movegen produces long 75C / short 80C with a $100 entry credit, minimum net P&L $98.70 and capital at risk $1.30. Its true minimum return is **+75.923076923**; the anchored mean near **−1** is nevertheless scored and the forecast quantiles are **−1**.
- **Suggested fix:** Use `(low − entry_fees − exit_fees) / capital_at_risk` for the lower endpoint as well as the corrected signed upper endpoint. The existing feasibility check should then skip/count an impossible mean. This concerns robustness to admitted inconsistent quotes; it is not a request to implement R2's DR-0041 eligibility policy.

### X02 — Newly scored null-run underlying forecasts lack producing-code provenance

- **Severity:** medium.
- **File and line:** `src/remy_finance/reports/render.py:68`–`74`, called at `:488`; missing data field in `baselines/common.py:111` (`BaselineRun`).
- **What breaks:** The report header derives engine/version/code-commit combinations exclusively from recommendations. DR-0040 now scores every fitted expiration from MKT-1 run logs, including null decisions that recommend no trade. Those observations can be scored while the header says `Engines (code commits): none`, or lists only commits for other recommended trades. `BaselineRun` has no producing-code-commit field, so the missing provenance cannot be recovered reliably from the null run itself. EVAL-1 §7 requires the code commit of every scored engine.
- **Minimal reproduction:** `test_underlying_null_decision_engine_is_in_report_provenance` in `test_reports_extras.py`. Supply a legitimate MKT-1 null-run log with a valid eligible fit and a public fixture close to an empty replay registry. One underlying forecast is scored, but the producing engine is absent from the provenance header.
- **Suggested fix:** Preserve producing-code provenance on run-log forecasts and include the scored runs in report provenance, independently of recommendations. Label unavailable historical provenance explicitly instead of guessing a commit from another board or substituting the evaluation/reporting commit. Version schema changes and preserve existing logs.

### X03 — Known-defect sensitivity omits P&L and other displayed results

- **Severity:** medium.
- **File and line:** `src/remy_finance/reports/render.py:420`–`424`.
- **What breaks:** `_sensitivity` filters the affected trades correctly but renders only realized-return and forecast-score tables. The primary P&L/drawdown, structure-level and selection-by-rank results still include annotated trades without corresponding “without” results. EVAL-1 §1.7 calls for results with and without affected trades, and the R1 memo says cohort and operational results are repeated. The current financial sensitivity cannot show how removing a known-defect trade changes the displayed dollar outcome.
- **Minimal reproduction:** `test_known_defect_sensitivity_repeats_pnl_results` in `test_reports_extras.py`. Annotate one closed MKT-1 trade in the existing offline integration fixture, then generate the **all-closed operational sensitivity**, matching the primary operational P&L table's scope. Remaining trades have **$453.05** realized P&L; no P&L result is emitted for them. Only returns and forecast scores appear.
- **Suggested fix:** Render corresponding financial and selection sensitivity results from the filtered groups, using the same scope/cutoff and aggregation conventions as each primary result. Retain the full primary analysis and clear sensitivity labels; do not exclude annotated trades from the primary record.

### X04 — Verification can still race the first export

- **Severity:** medium.
- **File and line:** `src/remy_finance/registry/store.py:413`.
- **What breaks:** `verify(export_dir=OUT)` skips the export lock if `OUT` does not yet exist. Once its database snapshot is established, another handle can append a recommendation and perform the first export, creating OUT. Verification then reads newer JSONL against its older snapshot and falsely reports valid exported rows as missing or changed. The fix covers existing directories but leaves this first-publication edge.
- **Minimal reproduction:** `test_x_verify_locks_before_snapshot_even_before_first_export` in `test_registry_extras.py`. A bounded worker appends recommendation 2 and creates the first export after verification's first real recommendation read. Both writes succeed; verification incorrectly reports `exported ('recommendations', 2) is missing or changed`.
- **Suggested fix:** Acquire the canonical destination lock whenever an export directory is supplied, even before it exists, unless a caller explicitly already holds that lock. Establish the read snapshot only afterward. This is a false integrity alarm, not demonstrated data loss.

### X05 — A repointed alias redirects export I/O outside its destination lock

- **Severity:** medium, conditional on changing an alias during an active operation.
- **File and line:** `src/remy_finance/registry/store.py:130`, `:535`–`:544`.
- **What breaks:** Only `export_lock()` resolves the path. `Registry.export()` continues its reads and publication through the original unresolved alias. If the alias is repointed while export A is active, A can write into target B while holding target A's lock. Another exporter can legitimately publish newer rows directly into B, after which A overwrites them with stale content. Ordinary stable aliases, and repointing strictly between completed operations, are not the failing case.
- **Minimal reproduction:** `test_x_repointed_export_alias_keeps_io_bound_to_locked_destination` in `test_registry_extras.py`. Pause A before publication, repoint its alias from FIRST to SECOND, let another real registry handle export two rows directly into SECOND, then resume A. SECOND ends with only A's stale one-row export.
- **Suggested fix:** Resolve the destination once at the operation boundary and use that same immutable resolved path for both locking and every file operation. Keep the scheduler's publication/commit/push destination consistent with it. No claim is made that the current production configuration changes aliases during export.

### X06 — Watermark recovery invents pre-deployment prospective gaps

- **Severity:** medium.
- **File and line:** `src/remy_finance/scheduler/tick.py:235`–`:240`; prior-session nightly markers at `:342`–`:354`.
- **What breaks:** Recovery treats the earliest marker's **session date** as the scheduler's start date. A first tick before today's nightly time creates a backup/export marker for the previous session. If the watermark becomes unreadable, recovery starts from that earlier marker date and records “missed” slots for a day on which the scheduler had never operated. The resulting append-only prospective gap history is false.
- **Minimal reproduction:** `test_watermark_recovery_does_not_invent_gaps_before_scheduler_first_tick` in `test_scheduler_cli_extras.py`. Start the scheduler Monday at 09:00; its nightly helper creates Friday's backup marker. Corrupt the watermark to simulate the legacy condition this fix supports, then rerun Monday at 09:05. Four Friday prospective gaps are invented despite deployment starting Monday.
- **Suggested fix:** Preserve a durable first-run boundary and use actual execution/start evidence when recovering older state. Do not infer deployment date solely from markers labelled for prior-session nightly work. Bound reconciliation so it cannot create gaps before installation, while retaining the atomic watermark replacement.

## 3. Gate results

| Check | Result |
|---|---|
| Offline dependency synchronization | Passed using locally cached/previously installed locked packages |
| Ruff lint | Passed |
| Ruff format | Passed: 205 source/test files |
| mypy | Passed: 72 source files |
| Brief's focused review/report/integration suites | **100 passed** |
| Complete repository suite | **549 passed** |
| New independent probes | **8 passed, 7 failed**, supporting six findings (X01 has two failures) |
| New probe lint/format | Passed |

The final pytest runs use a separate review-only plugin that blocks and counts `socket.connect` and `connect_ex`, including localhost. The complete commands, observed socket-attempt counts and results are in `reproduction-results.txt`. Temporary Git remotes are filesystem-local. The isolated source checkout remains clean.

## 4. Other brief-directed checks and qualifications

- **No feasible forecast changes is not generally true.** A normal synthetic 85C/87.5C debit spread remains feasible with the same anchored mean, but its 1% quantile changes from **−0.22236799** under the old upper-bound rule to **−0.09184190** under the corrected rule. The independent differential probe passes. The correction is warranted, and the available memo §6 already acknowledges feasible-mean forecasts receiving capped tails. This is a qualification to the brief's version rationale, not a demand to revert the fix or an unsupported claim that every numerical correction requires an engine-version bump. Producing code commits remain important for interpreting v3 records.
- **Annotations preserve the intended state.** Independent tests confirm unchanged cash, positions, mode separation, original exported bytes and registry integrity under normal append/retry. Ordinary engine annotations are not treated as defect notes. Several defect IDs do not require changing a trade's primary outcome.
- **Concurrent annotation limitation.** Two identical annotators can both pass the pre-insert duplicate check and append two notes. The observed defect-ID set and affected-trade sensitivity membership still deduplicate, and cash remains zero. This is weaker than transactional exactly-once insertion but no additional material financial or position-state defect was demonstrated, so it is not counted as another X finding.
- **Registry lock paths.** Mixed legacy/current exports preserve old bytes, and the legacy fallback does not hide a changed SQL kind. Existing caller-held `lock=False` paths complete, and no nested-lock deadlock was established in the scheduler, CLI export/verify or doctor callsites. These passing checks do not remove X04/X05.
- **F09 arithmetic and timing.** Genuine baseline grids were checked at knots, exact outer endpoints, tails and repeated values. Scores remain finite, zero-width segments are avoided, each board contributes its own MKT-1 fits, RAND-1 copies are not counted, closes are cached per symbol/expiration, future decisions are skipped, and fixture closes unavailable at report time are not scored.
- **Accepted source trade-offs are not re-reported.** DR-0040 explicitly chooses report-time closes without storing them, one observation per MKT-1 fit/board, a linear grid density and floor, and the fail-first “unavailable” behavior. A missing MKT-1 log is not recovered from RAND-1's copy under the chosen rule. A permanently absent close may remain labelled waiting; distinguishing temporary absence from permanent absence is a reporting limitation, not a demonstrated deviation from the accepted source protocol.
- **Corporate actions remain a design limitation.** The accepted computation is nominal official close divided by the recorded forward. A split can change those price units, and a ticker change can make the original symbol's close unavailable. The new path has no corporate-action adjustment convention. Specify and version any adjustment/annotation treatment explicitly rather than silently changing DR-0040 or excluding forecasts after the fact. No separate X finding is asserted merely for following the accepted raw-close/recorded-forward formula.
- **R2 was not reviewed as implemented code.** DR-0041, DR-0042 and EVAL-2 changes remain outside scope. Stop Point C and the production scheduler were not changed by this review; findings are maintenance items under the supplied brief.

## 5. Delivery

The ZIP contains this report, four separate independent probe files, the review-only offline socket guard, and `reproduction-results.txt`, with a short README. It does not contain market datasets, credentials, production registry files or a modified source checkout. The requested `99c1a34` source scope is covered through the owner's explicit source/test equivalence confirmation; its Git object was not independently available locally.
