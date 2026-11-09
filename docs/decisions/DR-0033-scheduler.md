# DR-0033: Prospective scheduler design (R1.6)

- **Status:** proposed
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.6; DR-0002 (snapshot times), DR-0003 (execution quote at +5 min), DR-0012 (backup), DR-0022/0024 (gaps), DR-0026 (sources), DR-0028 (registry, run logs)
- **Stage:** R1.6

## Context
The brief asks for:
- launchd jobs for the open and close sessions (snapshot → recommendations → execution fill pass → exit, assignment and expiration pass → nightly export);
- the trading calendar from exchange_calendars;
- idempotent jobs that log to `data/logs/`;
- a macOS notification and a gap record on failure;
- a `doctor` command.

Code: `src/remy_finance/scheduler/`.

## Options considered and decisions

1. **One tick, not one job per time.**
   - Options:
     - (a) separate launchd jobs at fixed local clock times;
     - (b) **one job every 60 seconds** that works out from the XNYS calendar, in ET, what is due.
   - **Decision: (b), `remy run tick`.** launchd's calendar intervals use the Mac's local time zone. Fixed times would break with the Mac's time-zone setting, daylight-saving changes and half days, and a run missed while asleep would never be retried.
   - The tick is idempotent, protected by a non-blocking lock, and cheap when there's nothing to do (daily markers). It writes a heartbeat for `doctor`.
2. **Slot logic** (per session, per kind):
   - A snapshot is captured inside its window. A window that ends with no capture becomes a gap, and so does a capture that fails or overruns its window. A decision gap makes the execution slot a gap too.
   - The **execution capture starts no earlier than the decision's actual `as_of` + 5 minutes**, not the scheduled 10:05. A prospective decision is stamped after its fetch, so it can be minutes late, and the engine requires the execution board at least 5 minutes after the decision (DR-0029 §2).
3. **Order of steps.**
   - Decision snapshot → recommendations (both baselines) → execution snapshot → fills of this snapshot's orders → management of older positions.
   - Recommendations are written before the run log, and the per-day cap counts only *earlier* snapshots. A crash between the two therefore re-runs to identical picks, whose trade IDs are already recorded and are skipped.
4. **No recommendations from uncommitted code.**
   - A prospective recommendation must name the exact code that made it, so the tick checks `git status` (tracked files only).
   - If the tree is dirty, that slot's recommendations are **skipped**: failure record, notification, and the marker `SKIPPED`. They aren't retried later with a stale receipt time.
   - Fills and management of existing positions continue regardless.
   - Alternative rejected: record the commit as `<sha>+dirty`. That would label the record as unreproducible but still issue it.
5. **Late receipt means unfilled.** A recommendation recorded after its execution quote was taken is unfilled, never filled at a price that already existed when it was recorded.
6. **Settlement and the nightly work.**
   - Expiration settlement runs on every tick, as soon as the official close is public. That's ThetaData's stock EOD `close`, public at 17:15 ET (R0.1 M6: 33 of 33 sessions match the exchange); the event time is the session close, half days included.
   - Reading a close before it's public returns nothing, and asking for a future session raises `LookaheadError`.
   - From **17:30 ET** on the latest session, once per session:
     - the **export**: `registry export` into the `registry-export` checkout, then `git commit` and `git push`;
     - the **backup** (DR-0012).
     A failed push is retried at the next tick.
7. **Run logs.** Run logs are persisted write-once under `data/runs/<mode>/<date>/<SESSION>-<engine>-<snapshot>.json`, including null decisions (DR-0028 §11).
8. **Secrets.** launchd doesn't load shell profiles, so the Massive key is read from the environment, falling back to the macOS Keychain (service `remy-finance`). The plist holds no secrets, and `doctor` reports only where a key was found.
9. **Failures.** Every step is guarded. A failure writes `data/logs/failures/<time>-<step>.json` and a macOS notification, and the tick continues with the independent steps. A failed capture is also a gap.

## Consequences
- **Running it** needs a one-time setup on the Mac mini (`docs/runbook/scheduler.md`): keep the Mac awake, put the key in the Keychain, check out `registry-export`, run `doctor`, install the plist. After that there are no manual steps.
- **Tests:** 15 offline scheduler tests simulate whole trading days with a fake clock and capture. They cover:
  - gaps from missed windows and failed captures;
  - the five-minute execution rule;
  - re-runs that write nothing;
  - a dirty tree;
  - lock contention;
  - settlement timing;
  - weekend runs;
  - an export pushed to a real git remote.
- **Known limitations:**
  - The scheduler can't stop the Mac sleeping; `doctor`'s heartbeat check is what shows it.
  - Massive's prefetch adds about 50 s to each capture on the free plan, inside the 10-minute window.

## Questions for Josh
1. **Dirty tree (§4):** skip recommendations (proposed), or issue them with a `+dirty` commit label?
2. **Nightly time (§6):** 17:30 ET for the export and backup?
3. **The export checkout** at `~/remy-registry-export` (runbook step 3), pushed to the `registry-export` branch of this repository?
