# DR-0041: Exclude candidates that can only lose (from R2)

- **Status:** proposed (Josh chose this at Stop Point C, 2026-11-18; awaiting confirmation of this record)
- **Date:** 2026-11-18
- **Source:** the live run's Codex V04 exposure check (`docs/memos/R1-result.md` §6). Amends the eligibility rule of [DR-0030](DR-0030-mkt1-details.md) and [DR-0031](DR-0031-rand1-details.md), from R2.
- **Stage:** R1 decision, effective at R2

## Context
- **What happened:** 4 of RAND-1's 55 live picks could not make money at executable prices. Their best terminal P&L net of fees is negative.
  - At executable prices (sold at the bid, bought at the ask) plus fees, even the best outcome of each is a loss.
  - Two are short iron condors and two are long iron condors.
- **Why it matters:** the null move dominates every such trade. MKT-1 never selects them, but RAND-1 samples uniformly from every priceable candidate, so it does.

## Options considered
1. **Exclude them from the eligible set from R2, with a RAND-1 version bump** (chosen).
2. Exclude them now (RAND-1 v4). The prospective record would then mix v3 and v4 within R1.
3. Keep them, as a uniform sample of what movegen offers.

## Decision
- **The rule:** from R2, a candidate is eligible only if its best attainable net return is positive, meaning the signed maximum of payoff minus premium, minus fees, is above 0. This is the same quantity `return_bounds` uses after the V04 fix.
  - Movegen still generates such candidates, so the movegen list hash is unchanged.
  - The eligibility step drops them, and each run log counts the exclusions.
- **Versions:** RAND-1 changes version at R2, because its sampling population changes. MKT-1's selections are unaffected but bump with it, so both baselines keep one shared eligible set.
- **R1:** the v3 records stay as recorded. The 3 affected forecasts are annotated (EVAL-1 §1.7).

## Consequences
- RAND-1 stops spending picks on trades that can't win. Its comparison with MKT-1 (EVAL-1 P2) is then over a population that only contains trades worth considering.
- The rule needs an R2 regression test: a candidate whose debit exceeds its width is never eligible.
