# DR-0006: Paper sizing rule

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D6
- **Stage:** R1

## Context
The primary score, return on capital at risk, doesn't depend on scale. Recommendations still need a concrete quantity so that fills can be checked against displayed size and so that dollar P&L is meaningful.

## Options considered
1. **Proposed default:** a paper budget of **$2,000 maximum capital at risk per recommendation**, with quantity = `floor(budget / per-unit capital at risk)`. If the quantity is 0, the candidate is not recommendable; it is logged, not silently dropped.
2. Always use quantity 1. It's simpler, but makes cheap and expensive structures incomparable in dollars and understates the impact of the size check.
3. Size to equal notional or equal delta. That requires models that are out of scope for R1.

## Decision
Adopt option 1 (`SIZE-1`, budget $2,000, with a cap of 10 contracts per leg; see below). Per-unit capital at risk includes entry fees (and exit fees where the exit policy requires a closing trade), per R0.6.

## Consequences
- Cheap structures get large quantities. Because FILL-1 requires displayed size to cover the full quantity, many cheap out-of-the-money candidates will go unfilled. That is the conservative and intended behaviour, but it will lower fill rates. Reports will show fill rate by structure type.
- Structures with per-unit capital at risk above $2,000 (for example, wide condors on high-priced names) are never recommendable. Each one is logged with the reason `QTY_ZERO`.
- **Owner decision (Josh, 2026-11-08):** quantity is capped at **10 contracts per leg**: `quantity = min(10, floor(2000 / per-unit capital at risk))`. The cap is part of `SIZE-1`.
