# DR-0002: Snapshot times

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D2
- **Stage:** R0
- **Settled context:** Two snapshots per trading day, one after the open and one shortly before the close (spec §15, handoff §1).

## Context
Spec §15 leaves the exact times to an R0 decision record. Opening quotes are wide and unstable for the first minutes. The close snapshot must leave room for the execution quote (DR-0003) before 16:00, and half-days close at 13:00 ET.

## Options considered
1. **Proposed default:** open snapshot at **10:00 ET**; close snapshot at **15:45 ET**; on half-days, the close snapshot is at **12:45 ET**. All times are US/Eastern and DST-aware.
2. Open at 09:45. It leaves less time for quotes to settle, and spreads are measurably wider in the first 15 minutes.
3. Close at 15:30. It's safer against late failures, but quotes are 30 minutes stale relative to the close.
4. Close at 15:55. The execution quote would then land at the closing auction, where displayed quotes are unrepresentative.

## Decision
Adopt option 1. Times are stored as local ET wall-clock times in config (`SNAPTIMES-1`). They are converted to timezone-aware UTC per date using `exchange_calendars` (XNYS) so that DST and half-days are handled.

## Consequences
- The schedule always leaves 15 minutes before the close (10 minutes after the execution quote).
- Replay snapshots use the same wall-clock times. R0.1 must confirm that ThetaData can return the chain NBBO at an exact clock time; see the R0.1 memo.
- Changing the times later creates a new config version. Past snapshots are never relabelled.
