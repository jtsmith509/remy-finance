# DR-0046: CLOSE-1, the nightly HIST-1 update and capture coverage

- **Status:** proposed (2026-11-23). Awaits Josh's confirmation.
- **Date:** 2026-11-23
- **Source:** R2 brief P12 (CLOSE-1) and P13 (HIST-1); EVAL-2 §6.3 (capture coverage, "recorded in a new DR") and §7.1 (closes read only from CLOSE-1); DR-0040 (F09 scores), as amended by P12; DR-0045 (HIST-1); DR-0039 (held names on boards).
- **Stage:** R2, Part 1

## Context
- **P12** asks for a write-once store of the official close of every universe name, SPY, the sector SPDRs and VIX. Each close is saved nightly once it's public, plus a one-off backfill of every past date the records need. F09 and R2 score **only from the store**; report-time fetching only fills gaps.
- **DR-0045** proposed that CLOSE-1 is a view of HIST-1's `CLOSE` series, not a second copy. DR-0044 replaced VIX with SPY's 30-day ATM IV, which HIST-1 derives.
- **EVAL-2 §6.3** needs, from the cut-over:
  - (a) boards that also capture every underlying with an open shadow-panel position, as **tracked-only**;
  - (b) a nightly store of the close and 30-day ATM IV of every name with an open shadow position, an unexpired FAC-1 S_T/F target or an unresolved IV target.
- **Settlement (EXP-1) and the F09 scores** read ThetaData directly today (DR-0040), so a vendor revision could change a later report.

## Options considered
1. **Where CLOSE-1 lives:**
   - HIST-1's `CLOSE` series, `data/hist/hist1.sqlite` (chosen, per DR-0045);
   - a separate write-once store under `data/closes/`, as P12 sketched. That is two copies of one fact, which can disagree.
2. **How readers fill a gap:**
   - store first, and fetch a gap from ThetaData and **store it**, write-once with its real public time, but only once that time has passed (chosen);
   - store only, so a gap stays a gap until the nightly step. Settlement would then wait up to a day for any close the nightly step missed;
   - fetch without storing (DR-0040 today). The score then isn't reproducible.
3. **Where the nightly step runs:**
   - in the scheduler tick, after the export and the backup (chosen): one launchd job, one lock, one failure log;
   - a separate launchd job, which means a second plist to install and keep in step.

## Decision
- **CLOSE-1 is HIST-1's `CLOSE` series.** `HistCloseSource` reads the store only. `StoreFirstCloses` reads the store first; a gap is fetched from ThetaData and written to the store once it's public, and each fill is counted.
  - The **scheduler's settlement**, the **replay driver** and `report cumulative`'s **F09 scores** all read through `StoreFirstCloses`. They still go through `official_close_as_of`, so nothing is used before 17:15 ET.
  - The report prints how many closes came from the store, how many gaps were filled from ThetaData, and how many weren't found (EVAL-2 §7.1).
  - A stored close always wins. A vendor value that differs is logged as a HIST-1 conflict (DR-0045), never written over.
- **The nightly HIST-1 / CLOSE-1 step** runs in the tick after the export and the backup.
  - **Names:** UNIV-CANDIDATES-1, SPY, the sector SPDRs, the UNIV-1 members in force, and every **tracked** name: underlyings with an open position, plus those with an unexpired S_T/F target in the run logs. FAC-1's targets, IV targets and shadow positions join this set when those engines exist.
  - **Series:** closes, Treasury yields, dividends and splits, and the 30-day ATM IV, by HIST-1's `build`. Ticker events cost one Massive request per name, so they're fetched only on the last session of each week.
  - **Window:** the last 10 sessions. The build is resumable, so a missed night is caught up and only what's missing is fetched.
  - **When:** after 17:30 ET, and **never between 08:00 and 17:30 ET on a session day**, so the tick lock can't delay a capture.
  - **Retries:** hourly, at most 3 times a night, then the night is marked "gave up" with a notification, and the next night's window retries.
    - A retry is triggered by any fetch error, or by more than half the names lacking that day's close (the vendor's end-of-day isn't out yet).
    - A single name with no close (delisted or halted) is logged, not retried.
  - **Failures** are recorded and notified like every other tick step, and never stop the export, the backup or settlement.
  - `remy hist nightly [--day]` runs the same step by hand.
- **Tracked-only names on boards (§6.3a):** a board's capture takes the held names (DR-0039) and the tracked names. Names on the board only because they are tracked are listed in its provenance as `tracked_only`, and recommendations never use them, exactly as with `held_only`. Until the shadow panel exists, the set of board-tracked names is empty; the mechanism is in place and tested.
- **The one-off backfill (P12):** `remy hist build` covers the base names from November 2022. `remy hist coverage` lists every close the records need: each position's expiration, and each MKT-1 fit's target, from both registries and run logs. It counts how many are stored and lists the missing ones. `--fill` fetches the missing ones into CLOSE-1.
- **Amends DR-0040:** closes come from CLOSE-1, not from a report-time fetch alone.

## Consequences
- **Scores and settlement are reproducible:** every close used is in the append-only store, with its public time.
- **The tick runs longer once a night,** about 3–5 minutes on most nights and about 25 minutes on Fridays (ticker events), after 17:30 ET when nothing else is scheduled.
- **The settlement path needs the HIST-1 store.** If the store can't be opened, settlement fails loudly in the tick's failure log rather than quietly reading the vendor.
- **The board-tracked set stays empty until the shadow panel exists,** and the run-status record (EVAL-2 §4.3) is a separate DR.
