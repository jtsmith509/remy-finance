# DR-0020: Move-generator strike lattice and enumeration details

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.7; refines DR-0008 (accepted) and DR-0006 (accepted)
- **Stage:** R0

## Context
DR-0008 bounds candidates to 7–60 DTE, strikes within ±25%, and a list of widths per price band. Enumerated over **every listed strike**, iron condors explode. SPY lists roughly 370 strikes within ±25%, which gives about 100k condors per expiration. With about 30 SPY expirations, that's millions of candidates per snapshot, far beyond the "low hundreds of thousands" DR-0008 anticipated, and not feasible to price twice a day in Python.

## Options considered
1. **Strike lattice per price band:** only strikes that are multiples of a band step are used. Iron condor short strikes are out of the money: the short put is below spot and the short call above.
2. Every listed strike, with condor shorts restricted to a delta or moneyness window. That needs delta, a model output the board mustn't contain, or another parameter.
3. Every listed strike with no condors. That drops a settled structure type.

## Decision (proposed)
Option 1, as part of `MOVEGEN-1`:

| Underlying mid | Widths (DR-0008) | Strike step |
|---|---|---|
| < \$50 | 1, 2.5, 5 | 0.5 |
| \$50–200 | 2.5, 5, 10 | 2.5 |
| \$200–500 | 5, 10, 20 | 5 |
| ≥ \$500 | 10, 20, 50 | 10 |

The other enumeration details:
- **Reference price** is the underlying mid at the snapshot. Without a two-sided underlying quote, nothing is generated for that name, and a `NO_UNDERLYING_PRICE` note is logged.
- **Every leg must be within band:** on the lattice, inside ±25% of spot, and present in the board's chain for that expiration.
- **Iron condors:** both wings have the same width, and the short strikes are out of the money.
- **Butterflies:** symmetric only, with wings at the same width from the centre.
- **Verticals:** labelled by structure, not by sign of premium. A call spread long the lower strike, or a put spread long the higher strike, is a debit; the reverse is a credit.
- **Leg order** in a candidate is canonical: puts before calls, then ascending strike.
- **Candidate order** sorts by underlying, expiration, structure type, then legs. The null move always comes first.
- **List hash** is the SHA-256 over the ordered candidate content hashes. It's recorded with the snapshot ID and the config hash.
- **SIZE-1 per-leg cap:** "10 contracts per leg" means `quantity × leg ratio ≤ 10`, so a butterfly is capped at 5 units.

## Consequences
- **Measured on a synthetic SPY-sized board** (371 strikes × 30 expirations × 2 rights): about 37.5k candidates in about 8 s. The 50-name estimate is roughly 300–350k candidates and about 70 s per snapshot. R0.4 will measure it on real boards.
- **Strikes between lattice points** are not used. For example, \$1 SPY strikes between the \$10 steps are skipped, which coarsens long options and verticals on high-priced names. A finer step for longs and verticals only is an easy later change, through a new DR and `MOVEGEN-2`.
