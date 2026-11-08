# DR-0012: Registry and snapshot storage

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D12
- **Stage:** R1 (R1.1), R0 (R0.4)

## Context
Spec §11 requires that recommendations are immutable, that corrections are linked records, and that the record can be audited. An external timestamp protects against silent rewriting of the local database.

## Options considered
1. **Proposed default:** SQLite (WAL) with append-only triggers and a hash chain. A nightly JSONL export is committed to the repo as an external timestamp. Market snapshots are **not** committed to git; they live under `data/` with manifests and are backed up separately.
2. Postgres. It's more machinery than a single-host personal project needs.
3. Append-only JSONL files alone. They're simple, but have no query layer and no transactional guard.
4. Commit snapshots to git (or git LFS). That would make the repo far too large; the R0.1 memo gives the storage estimate.

## Decision
Adopt option 1.
- There are separate databases for `PROSPECTIVE` and `REPLAY`.
- `BEFORE UPDATE` and `BEFORE DELETE` triggers `RAISE(ABORT)` on both the recommendations and journal tables.
- Each row stores its canonical JSON, its SHA-256 and the previous row's hash. `registry verify` recomputes the whole chain.
- The nightly export appends new rows only to `registry_export/YYYY/MM/DD.jsonl`. The commit and push give a third-party (GitHub) timestamp.
- Snapshots are content-addressed Parquet with JSON manifests under `data/snapshots/{prospective,replay}/`.

## Consequences
- SQLite triggers can be bypassed by someone with file access (for example, by dropping triggers). The hash chain plus the pushed export is what makes tampering *detectable*; the triggers only prevent accidents.
- The nightly push needs a git credential on the Mac mini with push rights to this repo. **Owner decision (Josh, 2026-11-08):** the export goes to a dedicated **`registry-export`** branch of this repo.
- **Owner decision (Josh, 2026-11-08), backup:** `data/` is backed up by **cloud sync**. A live SQLite file must never be synced directly, so each night the job:
  - writes a consistent copy of each registry DB with SQLite's online backup API to `data/backup/registry-<date>.sqlite`;
  - syncs `data/backup/` and the write-once `data/snapshots/` to the cloud folder.

  The live DB and WAL files are excluded from sync. `doctor` checks that the last backup is recent. Which cloud provider to use is still to be chosen at R1.6.
