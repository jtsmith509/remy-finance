# DR-0034: Replay driver for the R1 replay month (R1.8)

- **Status:** proposed
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.8; DR-0033 (scheduler pipeline); EVAL-1 §1 and §4; power-estimate.md §5
- **Stage:** R1.8

## Context
The brief asks for the full R1 pipeline over a historical range (such as one recent month), with replay snapshots and the same offsets, written to the replay registry, as an end-to-end test that also gives early sanity numbers. Josh asked for the replay month's measured fill rate, σ and design effect to update the power estimate before the live run. Code: `scheduler/replay.py`, `reports/measured.py`, `remy replay run` and `remy report power-inputs`.

## Options considered and decisions
1. **Same pipeline as live.** The driver calls the scheduler's `Pipeline`:
   - recommend at the decision board;
   - fill at the execution board, which is at exactly +5 min in replay (DR-0022);
   - manage open positions;
   - settle at 17:30 ET using the official close.

   Only the board source differs: `build_replay` from ThetaData and Massive instead of a live capture.
2. **Simulated times.**
   - A replay recommendation is received at decision + 30 s, fills and management are recorded at execution + 60 s, and settlement at 17:30 ET.
   - Wall-clock times can't be used: every replay entry would then be "recorded after the execution quote" and left unfilled (DR-0033 §5).
   - The replay registry's `receipt_time` and `recorded_at` are therefore simulated. That's stated here and in the module docstring, and it never applies to prospective records.
3. **The tail.**
   - After the month, the driver keeps managing and settling until every position has closed, or the data runs out (yesterday).
   - Tail boards are **full-universe boards**, stored like any replay slot. Options rejected: boards restricted to the names with open positions. Slots are write-once, so a later replay covering those dates would reuse the partial boards.
   - To save time, a tail day builds **no boards** if no open position has a short leg, assignment stock, a pending exit, or a non-HOLD-1 policy. Such a day can't produce a board-driven event; settlement only needs the official close.
4. **Which month.** The month must end at least 61 days before today, so the 60-day tail completes on existing data. Proposed: **August 2026** (3 Aug – 31 Aug, 21 sessions; the tail ends about 30 Oct). That needs UNIV-1 memberships for August, September and October.
5. **Code commit.** Replay records use `code_commit(require_clean=False)`: a dirty tree is labelled `+dirty` rather than refused. Replay isn't evidence; the live rule (DR-0033 §4) is unchanged.
6. **Measured power inputs** (`remy report power-inputs`), per baseline:
   - recommendations per day, fill rate and σ of realized net return on capital at risk;
   - the **design effect**, `Var_cluster / Var_iid` of the mean realized return, clustered by ISO week of expiration (EVAL-1 §4) with the CR1 factor C/(C − 1);
   - the effective observations per month, and the implied ρ of the power model (Kish, inverted);
   - the months-to-detect table recomputed with the measured inputs. The prior ρ is used where fewer than 2 clusters exist.

   The power-estimate addendum records the measured values next to the prior; it doesn't replace them.

## Consequences
- **Runtime:** about 3–4 hours on the Mac mini (about 45 s per board). The run is resumable, and re-running it writes nothing new.
- **Tests:** 6 replay tests on synthetic replay boards:
  - a 3-day range plus its tail through settlement, including an ASSIGN-1 forced close;
  - re-running writes nothing;
  - the tail stops when the data runs out;
  - a missing universe gives a clear error;
  - the measured inputs;
  - the design-effect formula.
- **Runbook:** `docs/runbook/scheduler.md`, the "replay month" section.

## Questions for Josh
1. **Replay month:** August 2026 (proposed: the latest whose 60-day tail ends before today), or an earlier one?
2. **Tail boards (§3):** full-universe boards, skipped on days when nothing can happen (proposed)?
