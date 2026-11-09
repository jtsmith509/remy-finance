# DR-0030: Market-implied baseline MKT-1 details

- **Status:** accepted (confirmed by Josh, 2026-11-09: bug signals logged and excluded; $0.05 mid floor)
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.4; DR-0006 (SIZE-1), DR-0007 (SELECT-1), DR-0010 (HOLD-1), DR-0016 (canonical serialization), DR-0025 (exact Decimal)
- **Stage:** R1.4

## Context
The brief fixes the method: parity forward and discount factor, OTM implied vols with Black-76 on the forward, a smooth arbitrage-checked smile (cubic spline or SVI), a Breeden–Litzenberger density, expected net return on capital at risk at executable prices, ranking with ADJ-0, and selection per D6–D7. It also fixes the sanity property and the volatility-risk-premium hook (off). The choices below are left open. Code: `src/remy_finance/baselines/{pricing,smile,mkt1,common}.py`.

## Options considered and decisions

1. **Numerics in floats; Decimal only at the record boundary.**
   - Implied vols, smile fits and densities are model internals, not cash, so they use numpy floats.
   - Every number that reaches a record is converted with `to_decimal_places` (8 decimals, half-even), and cash and capital at risk stay exact Decimal.
   - Model outputs are therefore reproducible to 8 decimals across machines in practice, but bit-level float differences between platforms could in principle change the last digit of a predicted return.
   - numpy becomes a direct dependency (it was already installed through exchange-calendars). scipy isn't needed: the normal CDF, Black-76, the implied-vol bisection, the SVI fit and Nelder–Mead are written out, each a few lines and deterministic.
2. **Parity.**
   - A straight-line fit of `C_mid − P_mid` against strike over the 8 strikes nearest spot that have two-sided calls and puts. At least 3 are required.
   - DF must lie in [0.90, 1.0001] and the forward within 10% of spot; otherwise the expiration is rejected.
   - Using the forward avoids forecasting dividends.
3. **Implied vols.**
   - Out-of-the-money mids only: puts below the forward, calls at or above it. Mids below **$0.05** are excluded because their implied vols are mostly tick noise.
   - At least 5 points are required.
   - Time is ACT/365 from the board's `as_of` to 16:00 ET on the expiration date.
4. **Smile: raw SVI rather than a cubic spline.**
   - SVI has well-behaved linear wings, while a spline extrapolates badly past the last strike.
   - The constraints are b ≥ 0, |ρ| ≤ 1, positive minimum variance, σ ≥ 0.005, and **Lee's wing bound b(1 + |ρ|) ≤ 2**. Without that bound, cent-tick quotes produced a near-vertical wing and an arbitrage rejection in testing.
   - It is fitted with the quasi-explicit method: an inner linear least-squares solve, then a grid search refined by Nelder–Mead.
   - A fit whose implied-vol RMSE exceeds 0.05 is rejected.
5. **Density.**
   - Discrete Breeden–Litzenberger on a 2,000-step strike grid spanning ±6 ATM standard deviations, widened to cover the listed strikes.
   - The two tails are point masses at their conditional means, matched to the fitted put and call at the grid ends, so payoffs that are linear in the tails have exact expectations.
   - **Rejected as arbitrage:** a digital outside [0, 1], negative density, probabilities that don't sum to 1, or a mean more than 0.1% from the forward.
6. **Predicted return.**
   - `(E[payoff] − net premium at executable prices − entry fees − exit fees) / capital at risk`, per unit, **undiscounted at expiration** (HOLD-1).
   - The full distribution is recorded as the 5/25/50/75/95% quantiles plus three tail measures: CVaR at 5%, P(loss) and P(maximum loss).
   - Probability of fill is null, with a reason.
7. **Ranking and selection.**
   - Candidates are ranked by predicted mean (ADJ-0), with ties broken by movegen order.
   - SELECT-1 is then applied: K = 5, one per underlying per baseline per ET day. Earlier picks are read from the registry.
   - **Fewer than K picks** are logged as a shortfall. **No picks at all** is a null decision.
8. **Bug signals.** A predicted mean above **+5%** is logged in the run record with the candidate's hash and is **not selected** (see question 1).
9. **Decision log.** Every run produces a `BaselineRun` record: its inputs (snapshot, movegen list hash), counts, every expiration fit with its diagnostics or rejection reason, picks, shortfall, bug signals and flags, including `AMERICAN_EXERCISE_PREMIUM_IGNORED`. This is DR-0028 §11's per-run decision log. Persisting it is R1.6 work.
10. **VRP hook.** `vrp_adjustment` must be `OFF`, and passing a hook raises an error. It's R2 work.

## Consequences
- **Sanity property** (tests on synthetic lognormal chains):
  - With zero spread, fitted prices match the model within 2×10⁻⁴, and predicted returns are just fees plus carry, within 5¢ per unit.
  - With 2¢ and 5¢ half-spreads, the gap between each predicted return and its analytic value (−(half-spread + fees)/CaR, plus carry) is within $3 per unit, with a median under 50¢. No candidate comes near the bug-signal threshold.
- **Speed:** about 3 s for a 49-underlying board (≈27,000 candidates).
- **Known limitations:**
  - The American early-exercise premium is ignored. This is flagged on every run; it matters for deep in-the-money puts and for calls before dividends.
  - Only same-expiration structures are scored, which is all MOVEGEN-1 produces.

## Questions for Josh (answered 2026-11-09)
1. **Bug signals.** Exclude a predicted mean above +5% from selection (proposed), or record it like any other pick and only flag it? Excluding stops MKT-1 from systematically picking fit artifacts. The cost is that a genuine mispricing could never be selected; the baseline isn't meant to find those anyway. **Log and exclude.**
2. **Mid floor for the fit:** is $0.05 acceptable? It's configurable and versioned with MKT-1. **Yes, $0.05.**
