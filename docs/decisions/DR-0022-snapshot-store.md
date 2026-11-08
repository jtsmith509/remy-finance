# DR-0022: Snapshot store rules (`SNAPSTORE-1`)

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.4; DR-0002, DR-0003, DR-0012, DR-0015
- **Stage:** R0

## Decision
1. **Layout:** `data/snapshots/{prospective|replay}/{date}/{SESSION}-{KIND}-{id16}/`. Each snapshot directory holds:
   - `options.parquet`: one row per option quote, with every number written as a canonical string, so there are no floats.
   - `board.json`: the canonical JSON of the board without its option quotes.
   - `manifest.json`: the labels, the snapshot ID, and a SHA-256 for each file.

   Loading recomputes the file hashes and the snapshot ID, and refuses on any mismatch.
2. **Write-once.** There is one snapshot per (mode, date, session, kind) slot. It's written to a temporary directory and atomically renamed into place, and its files are set read-only on disk. A second write to the same slot is refused.
3. **Kinds:** `DECISION` is taken at the DR-0002 time and `EXECUTION` 5 minutes later (DR-0003). An execution snapshot must link the stored decision snapshot ID for its slot.
4. **Replay** boards must have `as_of` exactly equal to the scheduled time.
5. **Prospective** boards must have `as_of` between 0 and **10 minutes** after the scheduled time. A later board is refused, and the job records a gap instead.
6. **Gaps:**
   - A missed prospective slot gets a `GapRecord`, appended to `prospective/gaps.jsonl`.
   - Once a slot has a gap, it can never take a prospective snapshot. That is the no-backfill rule.
   - A gap can't be recorded for a slot that already has a snapshot.
   - Recording the same gap twice does nothing.
7. **Builder:** replay boards are read only through `PointInTimeView`.
   - **Prospective `as_of`:** prospective boards use live quotes, with `as_of` stamped after the fetch.
   - **Data problems:** a vendor error or missing data for one symbol becomes a `QualityNote` on the board. A `LookaheadError` always aborts the build.
   - **Quote flags:** quotes are flagged under QAGE-1 (`STALE_QUOTE` when older than 5 minutes), and `ONE_SIDED`, `CROSSED` and `ZERO_SIZE` are flagged too.
8. **Running without Polygon:** `--no-events` builds boards with the dividend/corporate-action source disabled. Every symbol then carries an explicit note; nothing is silently missing.

## Consequences
- **10-minute window:** a prospective job that starts more than 10 minutes late produces a gap, not a stale "10:00" board. R1.6 jobs must start promptly, and `doctor` reports gaps.
- **Storage:** snapshots stay out of git (DR-0012). The size measured in R0.1 is about 1.8 MB per 50-name snapshot within 60 DTE, and full chains are stored. The live R0.4 run will measure the real figure.
