# DR-0047: The run-status record (REGISTRY-2)

- **Status:** proposed (2026-11-23). Josh chose where the records live: a new hash-chained registry table. The rest awaits his confirmation.
- **Date:** 2026-11-23
- **Source:** EVAL-2 §4.3 (the run-status record, required before the cut-over; Codex I03) and §10; DR-0033 (scheduler: skipped slots, late receipt); R1.1's registry (DR-0012, Codex R1.1 and R1 fixes).
- **Stage:** R2, Part 1

## Context
- **What EVAL-2 §4.3 needs:** for every scheduled slot and every R2 engine, a status record that reaches the registry or run-log store and the cutoff export. The statuses are `ok`, `null`, `failed` (with the exception type), `skipped` (with the reason) and `not_scheduled` (with the reason).
- **Why it matters:** the coverage rule's numerator, F, and reason 2(a) are computed from these records before any pick or fill is examined. A missing status counts as a failure.
- **Independence:** each engine must run and record its status on its own, so one engine's failure never stops another.
- **What was wrong before:** the scheduler ran MKT-1 and RAND-1 in sequence inside one guard, so an exception in the first stopped the second, and nothing recorded the outcome per engine apart from the run logs.

## Options considered
1. **Where the records live (Josh):**
   - **A new hash-chained registry table, `run_status`** (chosen). It is exported and verified by the existing registry machinery, so it is in the cutoff state and anchored by the nightly export, like recommendations and the journal.
   - Write-once JSON files beside the run logs, copied into the export. No registry change, but no hash chain before the push.
2. **Migrating the live registry:**
   - **An explicit, one-time `remy registry migrate`** (chosen), run outside session hours with a backup and a verify before and after;
   - automatic migration on open. That would change the live database inside a tick, unattended.

## Decision
- **The record:** `RunStatus`, a frozen record with:
  - mode, session date, session (OPEN/CLOSE), the scheduled decision time (SNAPTIMES-1, half days included);
  - engine and engine version, status, reason;
  - the decision board's snapshot ID, if captured;
  - for `ok` and `null`, the SHA-256 of the run log, and the number of selections;
  - the code commit, and `recorded_at`, which places it in the cutoff state.
  - **Validation:** `ok` means at least one selection; `ok` and `null` need the run log hash and the board; every other status needs a reason and has no run log.
- **One status per (session date, session, engine) per registry.** The first recorded wins: a re-run never changes it.
- **REGISTRY-2:** a third chained table, `run_status`, keyed uniquely by `status_key` (`YYYY-MM-DD|SESSION|ENGINE`), with the same append-only triggers as the other chained tables.
  - It's exported in the nightly export lines (`"table": "run_status"`, carrying `status_key`) and checked by `verify`, including against the export.
  - REGISTRY-1 databases still open and work unchanged; they just record no statuses. `remy doctor` warns until they are migrated.
- **The migration** (`remy registry migrate`, both registries by default):
  1. take the scheduler's tick lock;
  2. verify the registry, with the export for the prospective one, and refuse to migrate on any problem. The export directory is `--export-dir`, which defaults to `REMY_EXPORT_REPO`; if it's missing the command refuses rather than skipping the check, unless `--no-export-check` is given. It also refuses if there's no prospective registry;
  3. take a backup;
  4. in one transaction: drop and recreate the three meta guards around one schema update and one `migrated` row, create `run_status` and its triggers;
  5. verify again, and check that the recommendation and journal chain heads are unchanged.
  - No chained row is touched, so every exported line still verifies. A failed migration rolls back and changes nothing.
- **How the scheduler sets each status:**
  - **`ok` / `null`:** the engine ran; `ok` if it recorded a recommendation, `null` if nothing in its pool passed its own rules (the reason is the run's shortfall).
  - **`failed`:** the engine raised. Each engine runs inside its own try block, so a failure is recorded with its exception type and message, and the next engine still runs. A movegen failure is shared, so every engine on that board fails with the same error, and movegen runs once.
  - **`skipped`:** the decision board wasn't captured (a gap); the code tree was dirty (DR-0033 §4); or the session was reconciled late because the scheduler wasn't running in its window.
  - **`not_scheduled`:** an engine listed as not scheduled for the slot, with its reason. None in R1; R2 engines use this before their start.
  - **Each failure** also gets a failure record and a notification, and the other engines' picks stand. A failed status write counts as that engine's failure; it never stops another engine.
  - **Repair:** each tick after the recommend step, an engine with no status is recorded from its run log, or as `failed` ("the run was interrupted") if it has none. When a slot is skipped (a dirty tree, or reconciled late), an engine that has a run log for the board is recorded from it, never as `skipped`.
- **Not stored as statuses, but derived as EVAL-2 says:**
  - late receipt (reason 2(e)), from each recommendation's receipt time against the execution board;
  - no accepted fit (reason 2(c)), from the fit-vintage records.
- **Replay** records statuses too, in the replay registry, through the same pipeline. It refuses a REGISTRY-1 registry. On an engine failure it stops, as before, and records no `failed` status, because a resumed replay re-runs that engine.
- **A handle opened before the migration** follows it. `verify` reports a migrated database set back to REGISTRY-1, and duplicate status keys.
- **`remy registry statuses`** lists counts by engine and status, and the latest slots.

## Review (2026-11-23)
An internal adversarial review found 6 defects and some missing tests, all fixed with regression tests: see `docs/review/run-status-findings.md`.

## Consequences
- **The R2 coverage rule** can be computed from the registry alone. Missing statuses are visible, and are counted as failures.
- **The Mac needs a one-time step:** after pulling, outside session hours, run `remy registry migrate`. Until then the scheduler trades as before and records no statuses. The doctor shows a WARN.
- **The registry's schema and the Codex-reviewed export/verify code change.** This goes in the next Codex brief.
- **B13's failure-isolation rehearsal** is the end-to-end test of this record before the cut-over.
