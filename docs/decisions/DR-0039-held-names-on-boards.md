# DR-0039: Held names on captured boards

- **Status:** proposed (Josh chose "Add held names to boards" on 2026-11-11 when deciding the Codex fixes; awaiting confirmation of this record)
- **Date:** 2026-11-11
- **Source:** Codex R1.6 F02 (`docs/review/R1.6-R1.8-findings.md`). Amends [DR-0033](DR-0033-scheduler.md)'s board capture; leaves UNIV-1 ([DR-0004](DR-0004-universe-rule.md), [DR-0023](DR-0023-univ-1-details.md)) unchanged.
- **Stage:** R1, before the 5-day live run

## Context
- **The defect.** Boards cover the UNIV-1 members in force on the day. `Pipeline.manage` skipped any open position whose underlying wasn't on the board. A position whose name left the universe at a monthly rebalance was then never marked, assigned, force-closed or exited until expiration. Short legs held through ex-dividend dates would miss ASSIGN-1.
- **Why it matters.** ASSIGN-1, the forced-close rules and the exit policy all need a board with the position's underlying. Silently skipping a position is a bookkeeping defect, not an approximation.

## Options considered
1. **Add held names to boards** (chosen): every captured board covers the members *plus* every underlying with an open position. Recommendations stay restricted to members.
2. Keep member-only boards and capture a separate small board for held names. That means two snapshots per slot, two snapshot IDs, and more scheduler states.
3. Keep the skip and log it. The defect remains.

## Decision
- **Capture.** The tick and the replay driver add every underlying with an open, unexpired position to the board's symbol list, after the members.
- **Recorded on the board.** The names that are on the board only because they're held are listed in the board's provenance under `held_only` (source `scheduler`). The board's hash and snapshot ID therefore cover the distinction.
- **Recommendations.** `Pipeline.recommend` drops candidates whose underlying is held-only before scoring. The run log's movegen list hash is the hash of the restricted list. MKT-1 and RAND-1 never recommend a held-only name.
- **Management.** `manage` uses the board as before. If a position's underlying is still missing (for example, a board built before this DR), the skip is no longer silent: it is logged with the trade ID and the symbol.
- **What stays the same.** UNIV-1's membership rule, its monthly rebalance and the per-day caps.

## Consequences
- **Bigger boards** only when a held name has left the universe: one extra symbol per such name, so negligible.
- **Replay boards** built before this DR (the August replays) don't carry held names. The August replay ran inside one membership, so nothing was affected.
- **Reports** are unchanged: a held name's trades were recommended while it was a member, under the engine that made them.
