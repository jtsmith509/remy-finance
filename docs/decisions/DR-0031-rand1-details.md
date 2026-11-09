# DR-0031: Random baseline RAND-1 details

- **Status:** accepted (confirmed by Josh, 2026-11-09: RAND-1 uses MKT-1's forecasts)
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.5; DR-0007 (SELECT-1), DR-0030 (MKT-1)
- **Stage:** R1.5

## Context
The brief says RAND-1 samples K priceable, recommendable candidates per snapshot uniformly, under the same caps as MKT-1. Its seed is hash(`snapshot_id`, `RAND-1`), it is recorded, and the same inputs must give the same picks. Two points are left open:
- A recommendation must carry a predicted return distribution (spec §11), and the brief doesn't say where RAND-1's comes from.
- The brief doesn't say how the sampling is done.

Code: `src/remy_finance/baselines/rand1.py`.

## Options considered and decisions

1. **Forecasts.**
   - Options:
     - (a) **MKT-1's market-implied forecast** for each pick.
     - (b) A naive forecast, such as "expected return = −fees / capital at risk", with no distribution.
     - (c) A forecast from RAND-1's own model.
   - **Decision: (a).** RAND-1 then differs from MKT-1 **only in selection**, so comparing the two isolates whether MKT-1's ranking adds anything. Calibration comparisons between them become meaningless, since they share one forecast model; that is expected of a random baseline.
   - Option (b) has no full distribution, so it fails spec §11. Option (c) would be a new model, which is out of R1 scope.
   - **Consequence:** RAND-1's eligible set is the candidates MKT-1 could score: an expiration that passed the fit and its checks, and no bug signal (DR-0030). Both baselines therefore draw from the same set.
2. **Sampling.**
   - Options: Python's `random.Random(seed).sample`, or ordering by a hash.
   - **Decision:** each eligible candidate gets the key `sha256(seed + "\n" + candidate content hash)`. Candidates are taken in key order and SELECT-1 is applied: K = 5, one per underlying per baseline per day, with earlier picks that day read from the registry. Taking candidates in a uniformly random order and skipping capped underlyings is exactly uniform sampling without replacement under the caps.
   - Why not `random.Random`: its sampling algorithms have changed between Python versions, and a hash order can't. A chi-square test checks uniformity.
   - `seed = sha256("<snapshot_id>\nRAND-1")`, recorded on every recommendation and in the run log.
3. **Rank.** `rank` records the draw order, not a preference.
4. **Trade IDs.** A candidate picked by both baselines gets two different trade IDs, because the engine name goes into the ID (DR-0028 §3). The two picks are separate trades.

## Consequences
- **Reproducible:** the same board and the same earlier picks give byte-identical recommendations and an identical run log. A different snapshot gives a different draw.
- **Implicit weighting:** an underlying with more candidates is somewhat more likely to be drawn first. This follows from sampling *candidates* uniformly, as the brief specifies, rather than sampling underlyings.

## Questions for Josh (answered 2026-11-09)
1. **Forecast source:** RAND-1 uses MKT-1's forecasts (proposed), so the two baselines differ only in selection. Agree? **Yes.**
