# DR-0024: Atomic slot finality and per-slot gap files (amends DR-0022)

- **Status:** accepted (confirmed by Josh, 2026-11-08, after the independent R0 review)
- **Date:** 2026-11-08
- **Source:** Independent R0 review, finding F01 (`docs/review/R0-findings.md`); amends DR-0022 (accepted), item 6, and the store layout
- **Stage:** R0

## Context
DR-0022 promised one snapshot per (mode, date, session, kind) slot, and no backfill once a prospective slot had a gap. The implementation only *checked* the slot before publishing:
- **Two writers:** with different content, both passed the check. Their directories were named by content hash, so both atomic renames succeeded, and the slot ended up with two snapshots. The review reproduced this with real concurrent writes.
- **Writer and gap:** a gap could be committed between a writer's check and its rename, leaving both a gap and a snapshot.
- **Partial lines:** gaps were appended to one shared `gaps.jsonl`, so an interrupted append could leave a partial line that breaks every later read.

## Decision
1. **A per-slot lock.** An exclusive `flock` on `<mode>/<date>/.lock-<SESSION>-<KIND>` is held while the slot is re-checked (existing snapshot? recorded gap?) and the snapshot is published. Both `write()` and `record_gap()` take it, so a slot ends with **at most one terminal state: one snapshot or one gap**.
2. **Gaps are one file per slot:** `prospective/<date>/<SESSION>-<KIND>.gap.json`. Each is written to a temporary file and fsynced, then **published with `os.link`**, which fails rather than overwrite, and set read-only. This replaces the shared `prospective/gaps.jsonl`. No gap had been recorded under the old layout, so there's nothing to migrate.
3. **Recording a gap is idempotent:** recording one that already exists returns the existing record. Recording a gap for a slot that holds a snapshot is still refused.

## Consequences
- **Same machine only:** the lock is POSIX `flock`. That covers every process on the Mac mini, including launchd jobs, manual CLI runs and threads. It wouldn't protect a store shared over a network filesystem, which isn't a planned setup.
- **Regression tests** (`tests/unit/review`): two concurrent writers give one winner; a competing gap is shown to *wait* on the lock and is then refused; an interrupted gap write leaves no gap and no temporary file.
