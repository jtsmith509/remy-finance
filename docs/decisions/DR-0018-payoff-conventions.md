# DR-0018: Payoff and capital-at-risk conventions; priced structures

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.6; §4 (`Structure`, `Candidate`)
- **Stage:** R0

## Context
R0.6 fixes the payoff maths and the fixtures, but leaves three things open:
- whether `max_loss` and `max_gain` include fees;
- where the entry prices behind capital at risk are recorded;
- how a `Candidate` with no executable prices (R0.7 `UNPRICEABLE`) is represented.

## Decision
1. **Per-unit amounts in dollars.** A unit is one set of leg ratios. `net_premium` is positive for a debit and negative for a credit.
2. **`max_loss` and `max_gain` are contractual:** premiums plus terminal payoff, **before fees**. `max_gain` is `None` when unbounded.
3. **Capital at risk:** `capital_at_risk = max_loss + entry_fees + exit_fees`. `exit_fees` is non-zero only when the exit policy requires a closing trade; under HOLD-1 it is 0, because EXP-1 settlement has a \$0 exercise fee. Every R0.6 handoff fixture reproduces exactly under this definition.
4. **Extremes:** found by evaluating at S_T = 0 and at every strike, plus the slope beyond the highest strike. A negative slope raises `UndefinedRiskError`. All legs must share one underlying and **one expiration**; calendars and diagonals are excluded, consistent with DR-0008.
5. **`Structure` stores** `entry_prices` (per-share fill price per leg), `fee_version`, `entry_fees` and `exit_fees`. Its validator **recomputes** `max_loss`, `max_gain` and `capital_at_risk` from those inputs and rejects any mismatch. An inconsistent or unbounded structure therefore can't be constructed by any code path. `Structure.price(...)` is the builder.
6. **`Candidate`** carries its legs and structure type directly, plus an optional priced `Structure`:
   - **PRICED:** has a structure whose legs equal the candidate's legs.
   - **UNPRICEABLE:** has no structure and must give a reason.
   - **Null move:** has no legs and quantity 0.

   The defined-risk check runs on the legs alone, so even an unpriceable candidate can't be unbounded.

## Consequences
- A naked short put is admitted by the maths: its loss is bounded at K×100 − premium. Movegen only generates MOVEGEN-1 structure types, so this has no practical effect in R1.
- Exit fees for forced closes under ASSIGN-1 are charged when they happen (R0.8/R1.2), not pre-loaded into the capital at risk of HOLD-1 structures. A force-closed trade's realized loss can therefore exceed its capital at risk by those fees, plus any adverse exit spread. Reports will show this.
