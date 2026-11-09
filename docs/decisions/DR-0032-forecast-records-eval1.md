# DR-0032: Forecast records for EVAL-1 scoring (amends DR-0030 §6)

- **Status:** accepted. The substance was approved by Josh on 2026-11-09 as EVAL-1 question 3 (all three record changes, through a DR linked to DR-0030). The implementation details in §4 are recorded for review.
- **Date:** 2026-11-09
- **Source:** `docs/evaluation/EVAL-1-scoring.md` §2 and §6; amends [DR-0030](DR-0030-mkt1-details.md) §6 and extends [DR-0028](DR-0028-registry-details.md) (record schema)
- **Stage:** R1, before the 5-day live run

## Context
EVAL-1 scores every forecast with CRPS, a binned log score, PIT histograms and tail coverage. It also scores MKT-1's risk-neutral distribution of the underlying. DR-0030 recorded 5 quantiles per recommendation and no underlying-return distribution. That is too coarse for CRPS and PIT, and it rules out the log score entirely.

## Decisions
1. **15 quantile levels** for every strategy-return forecast: 1%, 2.5%, 5%, 10%, 20%, …, 90%, 95%, 97.5%, 99% (`mkt1.QUANTILE_PROBS`). RAND-1 inherits them, because it records MKT-1's forecast (DR-0031).
2. **Binned probabilities** of net return on capital at risk, over the EVAL-1 bins.
   - The bins are right-closed: {≤ −0.99}, (−0.99, −0.5], (−0.5, −0.25], (−0.25, 0], (0, 0.25], (0.25, 0.5], (0.5, 1], (1, 2], > 2.
   - They're named `EVAL-1-BINS` and defined once, as `registry.EVAL_1_BINS`.
   - `ReturnDistribution` gains `bins_version` and `bin_probabilities`. The validator requires a known version, one probability per bin, each in [0, 1], and an **exact** sum of 1.
3. **Risk-neutral quantiles of S_T/F** at 17 levels (0.1%, the 15 above, 99.9%) for every fitted expiration, recorded in the run log as `ExpirationFit.rn_quantiles`.

## Implementation details
- **Exact sum.** Bin masses are computed in floats, rounded to 8 decimals, and the rounding residual (at most a few units of 10⁻⁸) is added to the largest bin, so the recorded probabilities sum to exactly 1 in Decimal.
- **Schema versions.** `ReturnDistribution` goes to schema 2 and `ExpirationFit` to schema 2. Type tags carry the version (`ReturnDistribution@2`), so an old record can never be read as the new shape.
  - No registry has ever stored a version-1 record outside tests, so no migration is needed.
  - For the same reason MKT-1 and RAND-1 keep `engine_version = 1`: no recommendation was ever issued under the 5-quantile format.
- **Quantile rule.** Quantiles of the discrete distribution are the smallest support value whose cumulative probability reaches the level. This is the same rule as before, now shared by strategy returns and S_T/F.

## Consequences
- Every live record is fully scorable under EVAL-1 from the first day.
- **Record size:** each recommendation grows by about 10 quantiles and 9 bin probabilities, a few hundred bytes. Each fit in the run log grows by 17 pairs.
- **Tests** check:
  - bins against the distribution's P(loss) and P(maximum loss);
  - an exact sum of 1;
  - right-closed bin edges;
  - validation failures;
  - risk-neutral quantiles against the synthetic lognormal: the median and the 5% and 95% points within 0.003.
