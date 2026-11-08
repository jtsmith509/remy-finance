# DR-0005: Fee schedule FEES-1

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D5
- **Stage:** R0 (R0.6, R0.8)

## Context
Every capital-at-risk and P&L figure includes fees, so the schedule must be explicit, versioned and identical across engines.

## Options considered
1. **Proposed default:** **$0.65 per contract per side**, **$0 exercise/assignment fee**, configurable and versioned as `FEES-1`.
2. Model a specific broker's tiered schedule, including regulatory and exchange pass-through fees (ORF, OCC, SEC/TAF on sells). It's more realistic, but ties the project to one broker.
3. Zero fees. This is not acceptable, because fees are material on narrow spreads.

## Decision
Adopt option 1. Fees are a frozen config object (`FeeSchedule(version="FEES-1", per_contract_per_side=Decimal("0.65"), exercise_assignment=Decimal("0"), stock_per_share=Decimal("0"))`). Every cost computation records the fee version it used.

## Consequences
- The R0.6 fixtures in the handoff assume exactly these values.
- Under ASSIGN-1 and EXP-1, stock that results from assignment is closed. **Owner decision (Josh, 2026-11-08):** stock trades carry a **$0** commission (`stock_per_share = 0`). Slippage is captured by crossing the stock bid/ask.
- Regulatory pass-through fees (≈$0.02–0.05 per contract) are ignored. That is slightly optimistic and is noted in reports.
