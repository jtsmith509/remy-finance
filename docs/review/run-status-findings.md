# Run-status record (DR-0047): findings and resolution (internal adversarial review)

- **Reviewer:** an internal multi-agent review, not Codex. Five lenses (registry integrity, EVAL-2 §4.3 conformance, scheduler regressions, operating the migration on the Mac, test adequacy), then a skeptic on each candidate, trying to refute it.
- **Reviewed:** `56f4137`.
- **Result:** 19 candidates, of which **16 survived** and 3 were refuted. The 16 come down to **8 distinct issues**: 6 defects and a set of missing tests.
- **Fixed:** all of them, with regression tests in:
  - `tests/unit/registry/test_run_status.py`;
  - `tests/unit/scheduler/test_run_status_tick.py`;
  - `tests/integration/test_cli_registry.py`.
- **Checking the tests:** I reverted five fixes together: the status-write isolation, recording from the run log when skipping, replay's stop on failure, the duplicate-key check and the schema refresh. 5 tests fail.
- **Gate:** ruff, format and mypy clean; pytest **663 passed**.

| # | Severity | Finding (merged duplicates) | Fix |
|---|---|---|---|
| EVAL2-01 (also SCHED-03) | major | **A failed status write stopped the other engine.** For example, `database is locked` while `remy registry annotate` held the lock. The status writes sat outside the per-engine isolation, so the error escaped `recommend`, the slot was marked FAILED, and no engine ever got a status for it. | Every status write is guarded per engine. A failure is reported as that engine's failure and the loop carries on. A new `ensure_statuses` runs on each tick after the recommend step: an engine with a run log is recorded from it, and one with neither a run log nor a status is recorded as `failed` ("the run was interrupted"). |
| EVAL2-02 (also SCHED-01, TESTS-01) | major | **Replay now hid engine failures.** `recommend` caught them, the replay driver never read them, and a replay with a broken engine finished "successfully". | Replay stops on the first failure, with the engine and error, as it did before. It records no `failed` status, because a resumed replay re-runs that engine and records what it does then. Replay also refuses a REGISTRY-1 registry until it has been migrated. |
| EVAL2-03 (also SCHED-02) | major | **An engine that ran could be recorded as `skipped`.** If the process died after the run log but before its status, and the slot was later reconciled late (or hit a dirty tree), the engine got `skipped` even though its recommendations stood and were filled. | `record_skipped` first records each engine that has a run log for the board from that log (`ok` or `null`). Only engines that never ran get `skipped`. |
| OPS-1 | major | **`migrate` could skip the export check without saying so.** With `REMY_EXPORT_REPO` unset in the shell, or pointing to a mistyped path, it verified the prospective registry without its export and still printed "verify OK". | `--export-dir` defaults to `REMY_EXPORT_REPO`. For the prospective registry, the command refuses (exit 1) unless the export directory exists, or `--no-export-check` is given explicitly. It prints how many exported rows it checked before migrating. |
| OPS-2 | minor | **A handle opened before the migration kept the old schema.** A long replay running while `migrate` ran would record no statuses and then fail verification with "meta table altered". | A handle follows an upgrade from REGISTRY-1 to REGISTRY-2 in `has_run_status` and `verify`. Any other schema change is still reported. |
| OPS-4 | minor | **`migrate` exited 0 when it found no registry,** for example when `REMY_DATA_DIR` was unset or the command was run from another directory. | Exit 1 if there's no prospective registry. The resolved data directory and lock path are printed. |
| INTEGRITY-2 | minor | **`verify` didn't check that status keys are unique.** A rebuilt table without `UNIQUE` could hold two statuses for one slot. | `verify` reports `duplicate status <key>`, as it does for duplicate trades. |
| TESTS-03 to 07 | minor | **Missing tests:** `verify`'s checks on status records; the `migrate` refusals; the key clause of the insert trigger; the doctor warning; failures reset per board; and the snapshot ID, engine version and marker note. | Tests added for each. |

## Also added

- **Downgrade detection.** A migrated database whose schema row is set back to REGISTRY-1 to escape the run-status checks is now reported by `verify`. It's detected from the `migrated` meta row or the presence of the `run_status` table. INTEGRITY-1 and TESTS-02 raised this, and the skeptics refuted it as a separate hole, because it needs the same file-level trigger tampering as other attacks that only the export can catch. The check costs nothing, so I added it.

## Refuted (no change)

- **INTEGRITY-1 and TESTS-02:** see "Downgrade detection" above.
- **OPS-3: no rollback path.** Old code refusing a REGISTRY-2 database is the intended safety behaviour. The backup taken before migrating is the rollback.
