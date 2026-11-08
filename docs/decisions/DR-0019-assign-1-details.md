# DR-0019: ASSIGN-1 implementation details

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.9; refines DR-0009 (accepted)
- **Stage:** R0

## Context
DR-0009 fixes the ASSIGN-1 formula, the threshold and the consequences. A few inputs are left unspecified: which underlying price "S" is, what counts as in the money, how dividends are aggregated, and what happens when a quote is missing.

## Decision
- **S is the underlying mid** at the snapshot, (bid + ask) / 2 from `UnderlyingQuote` (DR-0013).
- **In the money** means intrinsic > 0. At-the-money legs (S = K) are not in the money, so they are never assigned.
- **Rule order:** the dividend rule is checked first, then the threshold rule. The recorded reason is the first rule that fires.
- **Dividends:** the dividend rule uses the **sum** of all cash dividends, ordinary and special, for the underlying whose ex-date is the next trading session. Only dividends with `public_time ≤ as_of` may be passed in; any other raises `LookaheadError`. The caller supplies the next session from the XNYS calendar.
- **No option quote** for an open short leg means extrinsic is treated as 0, which is conservative. An in-the-money leg is then assumed assigned, and the assessment records `option_bid = null`.
- **Each assessment is recorded** as an `AssignmentAssessment` record holding S, bid, intrinsic, extrinsic, the next session, the dividend total, the outcome and the reason. That record becomes the evidence in the `ASSIGNMENT_ASSUMED` journal event (R1.1).
- **Consequences:**
  - An assigned short call becomes short stock, with strike cash received. An assigned short put becomes long stock, with strike cash paid.
  - Short stock that crosses an ex-date before the closing trade owes the dividend.
  - Stock, and any option legs left over, are closed at the next snapshot by the fill engine (R1.2, DR-0009).

## Consequences
- Choosing the mid, rather than the side of the underlying quote that favours assignment, could under-assign a leg that is in the money by less than half the underlying spread. With liquid names that's a cent or two, so the threshold rule dominates in that zone anyway.
