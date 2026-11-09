# DR-0028: Registry implementation details REGISTRY-1

- **Status:** proposed
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.1, D12; spec §11; DR-0012 (registry backup)
- **Stage:** R1.1

## Context
The brief fixes the essentials: SQLite in WAL mode, triggers that abort UPDATE and DELETE, a per-row hash plus the previous row's hash, `registry verify`, separate prospective and replay databases, and a nightly JSONL export. These details were left open.

## Options considered and decisions

1. **Chain layout.**
   - Options: one chain per table, or one chain across both tables.
   - **Decision: one chain per table**, each starting from `GENESIS` (64 zeros). `chain_hash = sha256(prev_chain + "\n" + record_json)`.
   - Why: it is simpler and matches the brief's "previous row's hash". The order of events across tables is still fixed by the timestamps and links inside the records.
2. **What is hashed.** The record's canonical JSON (CANON-1) exactly as stored. Verify also re-parses each record and checks that it re-serializes to the same bytes, so a record can't be stored in a non-canonical form.
3. **Trade ID.**
   - **Decision:** `T-` followed by the first 24 hex characters of `sha256(engine \n version \n snapshot_id \n candidate_hash)`. The validator checks it.
   - Why: a replay rerun reproduces the same IDs, and a duplicate recommendation is refused (the column is UNIQUE).
   - The alternative was a random UUID, which is neither reproducible nor proof against duplicates.
4. **Mode stamping.** Each database has a `meta` table (`mode`, `schema = REGISTRY-1`), protected by the same triggers. A database refuses records of the other mode. Paths are `data/registry/prospective.sqlite` and `data/registry/replay.sqlite`.
5. **Journal order rules.**
   - Checked on append and again by verify:
     - one `ORDER_SUBMITTED`, first;
     - entry is all-or-none, so `LEG_FILLED` and `ORDER_UNFILLED` never appear together;
     - at most one pending exit at a time;
     - `TRADE_CLOSED` only once the position is flat (unfilled, exit filled, or settled), and afterwards only `ANNOTATION` or `CORRECTION`;
     - lifecycle event times don't go backwards.
   - Rejected alternative: no ordering checks, which would let a bug write an impossible history that could only be annotated afterwards.
6. **Corrections.**
   - `CORRECTION` carries `corrects_seq`, the journal sequence number of an event of the same trade, plus a mandatory note. It doesn't change the lifecycle state.
   - A wrong recommendation is never corrected in place. Spec §11 says reruns are separate, linked records, so a rerun is a new recommendation with a new trade ID.
7. **Evidence.** A journal event carries the canonical JSON of the record that justifies it (`OrderFill`, `AssignmentAssessment`, `ExpirationSettlement`, `StockFill`) together with that record's type name. Cash is a signed Decimal in dollars.
8. **Export layout.**
   - **Decision:** `registry_export/<mode>/YYYY/MM/DD.jsonl`, one line per new row (table, seq, hashes, record_json). The brief's path is `registry_export/YYYY/MM/DD.jsonl`; this adds the `<mode>` folder so the two databases' sequence numbers can never collide.
   - A day's file is replaced atomically and only ever grows.
   - `verify --export-dir` checks that every exported row is still in the database, byte for byte. That is the defence against someone dropping the triggers and truncating the newest rows, which leaves a valid but shorter chain.
   - Pushing the export to the `registry-export` branch is part of the R1.6 scheduler.
9. **Backup (DR-0012).** `remy registry backup` uses SQLite's online backup API to write `data/backup/<mode>-<UTC stamp>.sqlite`. Each copy is verifiable on its own.
10. **Insert guard.**
    - SQLite's `INSERT OR REPLACE` (and `REPLACE INTO`) deletes a conflicting row *without* firing DELETE triggers. I found and fixed this while building R1.1.
    - Each chained table therefore has a `BEFORE INSERT` trigger that accepts only the exact next row:
      - `seq` = max + 1;
      - `prev_chain` = the current head;
      - the chain hash is new;
      - for recommendations, the trade ID is new.
    - `meta` refuses every insert after creation.
11. **Null decisions.** These are not stored as recommendations, since the validator refuses null moves and quantity 0. Spec §11 says a null decision "can be audited separately"; it will be logged per snapshot with the engine run in R1.4–R1.6 rather than in this table.

## Consequences
- **Tamper evidence.** Any edit to a stored row is detected, with or without a recomputed record hash; a property test covers single-character edits. Truncation is detected only against the export, which is why the export is external.
- **Order rules.** The fill engine (R1.2) must write events in a legal order; an illegal one raises `RegistryError` and nothing is stored.
- **Schema changes** need a new schema version and a new DR. A database is never migrated in place.
