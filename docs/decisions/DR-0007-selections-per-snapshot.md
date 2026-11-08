# DR-0007: Selections per snapshot

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D7
- **Stage:** R1

## Context
Each baseline must issue falsifiable recommendations at a predictable rate. Concentration in one name would make observations highly dependent on each other.

## Options considered
1. **Proposed default:** each baseline issues its top **K = 5** recommendations per snapshot, with a cap of **one recommendation per underlying per baseline per trading day**.
2. Use a larger K, such as 20. It produces more records, but they're heavily correlated, and the registry grows faster than the number of effectively independent observations.
3. Use a per-snapshot cap instead of a per-day cap. That would allow the same name at both the open and the close.

## Decision
Adopt option 1 (`SELECT-1`, K = 5). The per-underlying cap is applied across both snapshots of the day, so an underlying picked at the open is ineligible at the close for that baseline. If fewer than K candidates qualify, fewer are issued, and the shortfall is logged.

## Consequences
- At most 10 recommendations per baseline per day, or about 200 per month for each of MKT-1 and RAND-1.
- The cap applies to *recommendations*, not to open positions. A name can have overlapping open positions from different days.
- RAND-1 applies the same caps, so the two baselines are comparable.
