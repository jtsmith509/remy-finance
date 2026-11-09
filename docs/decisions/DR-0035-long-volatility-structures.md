# DR-0035: R0 amendment: long-volatility debit structures in the move generator (MOVEGEN-2)

- **Status:** accepted (confirmed by Josh, 2026-11-09: straddles at every strike; strangles from every OTM pair; RAND-1 keeps uniform candidate sampling; the August 2026 replay uses MOVEGEN-2)
- **Date:** 2026-11-09
- **Source:** Josh's R0 amendment, 2026-11-09 (a narrow change ahead of a future spec v0.6). It amends the structure list of [DR-0008](DR-0008-candidate-bounds.md) (MOVEGEN-1) and extends [DR-0020](DR-0020-movegen-lattice.md) (lattice) and the R0.6 fixtures of [DR-0018](DR-0018-payoff-conventions.md).
- **Stage:** R0 (R0.6 and R0.7), carried into R1

## Context
REMY's core structures are long-volatility debit structures: they lose in a flat market and pay on a large move. MOVEGEN-1 generates long calls and puts, verticals, short (credit) iron condors and long butterflies, but none of the long-volatility combinations. Handoff §1 ("all defined-risk structures") already covers them, so this adds structures inside the settled scope.

## Decision
1. **Four new structure types:**
   - `LONG_STRADDLE`
   - `LONG_STRANGLE`
   - `LONG_IRON_CONDOR` (the reverse condor: buy the inner call and put, sell the wings farther out)
   - `LONG_IRON_BUTTERFLY` (buy the ATM call and put, sell OTM wings)

   The existing `IRON_CONDOR` remains the short (credit) condor.
2. **MOVEGEN-2** = MOVEGEN-1 plus the four types, and it is the generator's new default. `MOVEGEN_1` stays defined and unchanged, and so does its config hash, so earlier runs reproduce exactly. The same bounds apply (7–60 DTE, ±25% strikes, the DR-0020 lattice and widths, SIZE-1, FILL-1 pricing).
3. **Generation rules** (in `movegen.generator._shapes`):

   | Structure | Rule | Count per expiration (n lattice strikes) |
   |---|---|---|
   | Straddle | every lattice strike quoted for both call and put | n |
   | Strangle | OTM put (strike < spot) and OTM call (strike > spot), every such pair | about (n/2)² |
   | Long iron condor | OTM inner put and inner call; short wings one band width further out on each side; every width | about (n/2)² per width (mirrors the short condor) |
   | Long iron butterfly | centred on the lattice strike nearest spot (both strikes if spot is exactly between two), short wings one width either side; every width | at most 2 per width |

4. **Risk.** Long straddles and strangles have unbounded upside (`max_gain` = None) and bounded downside. The long iron condor and butterfly are fully bounded. All four are debits, so under HOLD-1 capital at risk = premium + entry fees.
5. **Short wings.** The short wings of the long iron condor and butterfly are ordinary short legs: ASSIGN-1 (DR-0009/0019) and EXP-1 (DR-0010) apply unchanged. No engine code changed.

## Not changing
These would each need a separate, explicit amendment:
- **MKT-1** stays the existing risk-neutral baseline, and the volatility-risk-premium adjustment stays OFF. MKT-1 simply scores the new candidates like any other.
- **The R1 exit policy** stays HOLD-1: no discretionary exits, and no selling into repricing.
- **Option volume** stays end-of-day and prior-session only, with no intraday activity signals.

## Tests (all passing; 479 in total)
- **R0.6 fixtures, exactly as given** (multiplier 100, $0.65 per contract per side, $0 exercise fee, spread-crossing prices):
  - straddle, capital at risk 481.30, including S=0 → +9,518.70;
  - strangle, 231.30;
  - long iron condor, 157.60;
  - long iron butterfly, 262.60.

  The test of the worst case on a fine grid of prices also covers these.
- **Risk numbers:** premium, max loss, max gain (None for straddle and strangle; 345 and 240 for the long condor and butterfly), fees, and no exit fees under HOLD-1.
- **Property tests** (hypothesis):
  - For each structure, with random strikes, widths, prices, quantities and S_T, including 0 and 10⁹: P&L ≥ −capital at risk.
  - For debits, the bound is attained.
  - The downside gain is finite.

  Hypothesis found one edge case for the tightness check, a "long" condor quoted at a net credit, whose max loss correctly floors at 0. Tightness is asserted for debits only; the safety property holds for every price.
- **Move generator:**
  - hand-computed MOVEGEN-2 counts on the test board: 9 straddles, 16 strangles, 13 long iron condors, 3 long iron butterflies;
  - shape checks: sides, OTM inner legs, ATM butterfly centre, equal wings, every candidate a debit;
  - MOVEGEN-1 counts unchanged.
- **Short-wing assignment:**
  - ASSIGN-1 assigns the long iron condor's deep-ITM short 110C wing (extrinsic 0.02), and leaves the OTM 90P wing alone.
  - EXP-1 settles the capped wing (+500 at S=112) and flags pin risk at S=110.10.
  - **End to end through the paper engine:** entry, ASSIGNMENT_ASSUMED on the wing, then a forced close at the next snapshot (stock bought back, three legs sold), with every cash flow hand-checked (realized +652.90).
- **One existing test was rewritten, not weakened.** The MKT-1 bug-signal test asserted that no pick contained the mispriced 115C; strangles can now legitimately include it. It now asserts its real intent: no candidate flagged as a bug signal is ever selected.

## Consequences
- **Candidate counts grow** by about 55% on a synthetic 49-name board (26,706 → 41,651). Movegen takes 4.3 s and MKT-1 2.7 s; real chains with more strikes per expiration will grow more, mostly through strangles and long condors.
- **RAND-1's structure mix.** RAND-1 samples *candidates* uniformly (DR-0031), so its mix now leans towards strangles and long condors, the most numerous types.
- **Timing:** the replay month (DR-0034) hasn't run yet, so it will use MOVEGEN-2 from the start.

## Questions for Josh (answered 2026-11-09)
1. **Straddle strikes:** every lattice strike (as built, following "same strike"), or only the strike nearest spot, as with the iron butterfly? **Every strike.**
2. **Strangle pairs:** every OTM put × OTM call pair (as built), or symmetric pairs only (equal distance from spot), which keeps the count linear? **Every OTM pair.**
3. **RAND-1 mix:** accept the structure mix that uniform candidate sampling implies, or sample a structure type first? The latter would need a linked DR to DR-0031. **Keep uniform.** The cumulative report now has a by-structure section (engine × structure: count, fill rate, closed, mean predicted, mean realized, P&L), so the mix stays visible.
