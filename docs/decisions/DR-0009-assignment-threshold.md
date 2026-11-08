# DR-0009: Early-assignment threshold ASSIGN-1

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D9, and the consequences rule in handoff §5 R0.9
- **Stage:** R0 (R0.9)
- **Settled context:** A conservative early-assignment rule (spec §8, handoff §1).

## Context
Spec §8 requires a fixed extrinsic-value threshold below which any in-the-money short leg is assumed assigned. It also requires a dividend rule. Handoff R0.9 gives the formula and asks that the consequences be recorded as a DR.

## Options considered
1. **Proposed default:** `extrinsic = max(0, bid − intrinsic)`. Threshold **$0.05 per share**, strict less-than. The dividend rule applies: an ITM short call, with the ex-date on the next trading day for a dividend known as of the snapshot, and dividend > extrinsic, is assumed assigned.
2. Threshold $0.10. It's more conservative, but closes many deep-ITM short legs that real holders rarely exercise.
3. Use the mid instead of the bid for extrinsic. It's less conservative, because a higher extrinsic value means fewer assumed assignments.

## Decision
Adopt option 1 (`ASSIGN-1`). **Consequences rule:**
- Assignment is assumed to occur before the next snapshot.
- At the next snapshot, the resulting stock position and **all remaining legs of the structure** are closed by crossing the spread. Stock closes at the underlying bid or ask, and the remaining option legs follow FILL-1.
- If the resulting position is short stock across an ex-dividend date, the dividend is owed.
- Each assumed assignment is an `ASSIGNMENT_ASSUMED` journal event.

## Consequences
- Requires dividends **known as of the snapshot**. R0.1 found that ThetaData has none; see DR-0014.
- Requires underlying bid/ask at the next snapshot to close stock. R0.1 found that the Stock FREE tier lacks it; see DR-0013.
- **Owner decision (Josh, 2026-11-08), failed forced close:** stock is closed at once, assumed always fillable at the underlying bid or ask. Any option leg that fails the FILL-1 size check is retried at each subsequent execution quote until expiration, where EXP-1 applies.
- Dividend data comes from DR-0014 (Polygon/Massive, `public_time` = declaration date at 16:00 ET). The underlying bid/ask comes from DR-0013 (ThetaData Stock Standard).
- Fixtures from handoff R0.9 become unit tests in R0.9.
