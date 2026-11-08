# DR-0025: Exact Decimal arithmetic and strict numeric inputs

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Independent R0 review, findings F03 and F04 (`docs/review/R0-findings.md`); refines DR-0016 and DR-0018 (accepted)
- **Stage:** R0

## Context
- **F03:** the public factories `Structure.price` and `OptionContract.build` converted their inputs with `Decimal(x)` *before* any validation. A Python float such as `0.1` therefore entered the cash maths as `0.1000000000000000055511…`, bypassing the Decimal-only rule. That value also contaminated canonical hashes.
- **F04:** Python's default Decimal context keeps 28 significant digits and rounds silently. At an accepted terminal price of `1e29`, the legs of a bounded spread rounded before they were subtracted, and the spread's \$500 payoff became 0.

## Decision
1. **Strict inputs at public boundaries.** `core.types.to_decimal` accepts only `Decimal`, `int` or numeric `str`. It rejects `float` and `bool` *before* converting, and rejects non-finite values. It's used by `Structure.price` (entry prices), `OptionContract.build` (strike), `Structure.pnl_at_expiry` (S_T) and `settle_at_expiration` (official close). Floats are rejected, never stringified.
2. **Exact arithmetic for cash and payoff.** `core.exact.exact_decimal(*operands)` sets a local precision equal to the operands' full digit span plus 40 digits of headroom, and **traps `decimal.Inexact`**. Inside it, addition, subtraction, multiplication and `max` either produce the exact result or raise; they never round silently. It wraps every payoff, premium, max-loss and gain, capital-at-risk, P&L, settlement, fill-cash and assignment calculation.

## Consequences
- Results are exact for any finite input size. A bounded spread's payoff is identical at S_T = 105 and S_T = 10³⁰⁰, and there are property tests up to 10³⁰⁰. Unbounded long calls give exact huge values rather than rounded ones.
- If a future calculation ever exceeded the computed precision, it would raise `Inexact` instead of returning a wrong number.
- Division isn't used in these paths. Where division appears elsewhere (spot mid, sizing, universe averages), it's outside cash P&L and stays under the default context; those results are not cash flows.
- The cost is small: one local context per calculation. On the real 50-name board, the move generator still has to be re-timed; it was 18.2 s before this change.
