# DR-0038: Arbitrage-free SVI fit (MKT-1 v3)

- **Status:** accepted (Josh, 2026-11-10: "arbitrage-free fit", before the live run)
- **Date:** 2026-11-10
- **Source:** the v2 August replay's diagnostics: on the 3 August 2026 open board, **250 of 376** expirations were rejected with "call prices not decreasing with slope in [−DF, 0]". Amends [DR-0030](DR-0030-mkt1-details.md) §4 and follows [DR-0037](DR-0037-mkt1-v2.md).
- **Stage:** R1, before the 5-day live run

## Context
- **What failed.** MKT-1 v2 fits a raw SVI smile with b ≥ 0, |ρ| ≤ 1, positive variance and Lee's wing bound. The fit didn't exclude *butterfly arbitrage*: on steep equity put skews, the best least-squares smile can match the quotes closely while its extrapolated left wing implies a negative density, which makes call prices non-convex with a slope outside [−DF, 0].
- **What the checks did.** The post-fit checks correctly rejected those expirations, as handoff R1.4 step 3 requires.
- **Effect.** MKT-1 and RAND-1 scored only about 13% of candidates.
- **Reproduction.** A synthetic chain with a moderate skew (vol 0.25 − 0.3·ln(K/100), floored at 0.12) fit the quotes to an RMSE of 0.0004 in vol and was still rejected.

## Options considered
1. **Constrain the fit to be arbitrage-free** (chosen): the optimiser considers only smiles with no butterfly arbitrage. This keeps the handoff's rule ("reject and flag expirations whose fit fails no-arbitrage checks") and stops rejecting fits only because the unconstrained optimum happened to sit in an arbitrageable region.
2. Keep v2 and record low coverage as a known limitation.
3. Accept small violations by clipping the density to be non-negative and renormalising. This departs from the handoff's rule.

## Decision
- **The arbitrage condition.** A candidate SVI slice is admissible only if **Durrleman's g(k) ≥ 0** on log-moneyness [−3, 3] (601 points), with tolerance 10⁻⁹. Here g(k) = (1 − k w′/2w)² − (w′²/4)(1/w + 1/4) + w″/2 is Gatheral's butterfly-arbitrage function.
  - g ≥ 0 everywhere means a non-negative risk-neutral density.
  - With Lee's wing bound, it also keeps call prices convex with a slope in [−DF, 0].
  - The range covers every strike grid MKT-1 builds (S/F from about 0.05 to 20).
- **Where it applies.** It is checked in the inner least-squares step, so the grid search and Nelder–Mead only ever move between admissible smiles. The result is the best arbitrage-free fit.
- **What stays the same.** The distribution's own checks (digital in [0, 1], non-negative density, probabilities summing to 1, mean equal to the forward) remain the final gate, so a truly unfittable expiration is still rejected. So does the SVI-RMSE limit (0.05 in vol).
- **Versions.** MKT-1 and RAND-1 go to **engine version 3**.

## Consequences
- **Fewer rejections** on steep skews. Cost: about 33 ms per expiration fit (measured), so about 13 s for a full board, well inside the live budget.
- **A slightly looser fit where the constraint binds** (RMSE can rise). The mean is unaffected, because it is anchored to mids (DR-0037); the constraint only changes the distribution's shape.
- **Tests:**
  - g detects a kinked, near-vertical left wing, even one inside Lee's bound;
  - the moderate-skew chain that v2 rejected now fits, with the smile butterfly-free, an RMSE under 0.005 and a non-negative density.
  - 491 tests pass.
- **The August replay is re-run a third time** with v3; v2's run is archived like v1's.
