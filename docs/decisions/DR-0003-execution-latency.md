# DR-0003: Execution latency and execution quote

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D3
- **Stage:** R0/R1
- **Settled context:** Fill rule FILL-1, crossing the spread with a full-size check (handoff §1, §6 R1.2).

## Context
If paper fills used the same quote the recommendation was computed from, the record would assume zero latency. A model could then implicitly "trade" on a stale or transient quote it just saw. A delay between decision and execution makes paper fills more honest.

## Options considered
1. **Proposed default:** recommendations are frozen at the snapshot. Fills use a separate **execution quote** taken **5 minutes later** (10:05 / 15:50 ET; 12:50 ET on half-days). Replay applies the same offsets to historical quotes.
2. Zero latency: fill at the snapshot quote. This is optimistic, and it's what spec §11 literally describes ("at the evaluation snapshot").
3. A 1-minute delay. It's closer to realistic human execution, but leaves little margin for pipeline time.
4. Randomized latency. It adds noise without adding information.

## Decision
Adopt option 1, with `latency = 5 min` recorded in the fill-rule version (`FILL-1`).

## Consequences
- Every session needs **two** chain retrievals: the snapshot and the execution quote. That roughly doubles storage and API calls. Execution quotes are stored like snapshots, as write-once and content-addressed, and linked to the decision snapshot.
- The execution quote contains only the contracts needed for that session's orders or exits. It does not need to be a full chain, but storing the full chain is simpler and supports audits. The R0.1 memo includes it in the storage estimate.
- **Owner decision (Josh, 2026-11-08):** the brief governs. The +5-minute execution quote is the "evaluation snapshot" for fills in spec §11's sense. A clarifying note goes into the next spec revision.
