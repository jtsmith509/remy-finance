# DR-0008: Candidate bounds MOVEGEN-1

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D8
- **Stage:** R0 (R0.7)
- **Settled context:** All defined-risk structures; no undefined-risk structures (handoff §1).

## Context
The candidate set must be broad (spec §7) but finite and deterministic. Enumerating every strike combination across every expiration is combinatorially large.

## Options considered
1. **Proposed default:** expirations **7–60 calendar days** out; strikes within **±25%** of the underlying; spread widths from a configured list per underlying price band. All bounds are versioned as `MOVEGEN-1`.
2. Bound by delta instead of moneyness. Delta needs a model, and the board must contain no model outputs.
3. Use no width list and enumerate all strike pairs. That is intractable for condors and butterflies.

## Decision
Adopt option 1. The width bands were **approved by Josh on 2026-11-08**:

| Underlying mid at snapshot | Allowed widths ($) |
|---|---|
| < $50 | 1, 2.5, 5 |
| $50 – $200 | 2.5, 5, 10 |
| $200 – $500 | 5, 10, 20 |
| ≥ $500 | 10, 20, 50 |

Widths apply to verticals, to each wing of an iron condor (put and call wings use the same width), and to butterfly wings (symmetric only in MOVEGEN-1). A width is used only where both strikes exist in the chain. The reference price is the underlying mid at the snapshot. Non-standard-deliverable contracts are excluded. The null move is always included.

## Consequences
- R0.1 measured that ThetaData's `at_time/quote` **ignores `max_dte`**, so every bound is applied in our own code.
- The candidate count is roughly in the low hundreds of thousands per snapshot across 50 names. It's manageable in polars, but movegen performance must be measured in R0.7.
- Same snapshot plus the same config gives a byte-identical ordered list. The ordering key is part of MOVEGEN-1.
- Asymmetric butterflies, broken-wing structures, calendars and diagonals are excluded from MOVEGEN-1. Calendars and diagonals involve multiple expirations, so their max loss at entry isn't bounded by terminal payoff alone. Any of them can be added later by DR.
