# DR-0044: R2 data substitutions and the minimum-history rule

- **Status:** accepted (Josh, 2026-11-23, at the R2.0 go/no-go)
- **Date:** 2026-11-23
- **Source:** the R2.0 data verification (`docs/memos/R2.0-data-verification.md`). Amends the R2 brief's P3, P6 and P9 (the VIX factor) and DR-0026 (the rate source for history); implements B12's minimum-history rule.
- **Stage:** R2, Part 1

## Context
- **What R2.0 found:** ThetaData's free index and rate tiers start on 2024-01-01. VIX and ThetaData's rates therefore cover 726 of the 1,018 sessions from 2022-11-01, and R2 needs all of them: a 3-year window before the November 2025 replay start.
- **ARM:** it listed on 2023-09-14, so it lacks 3 years of history until September 2026. Any future universe entrant can have the same problem.

## Options considered
1. **Volatility factor:**
   - SPY's own 30-day ATM implied volatility, derived from its end-of-day chains (chosen);
   - Cboe's public VIX history spliced to ThetaData's;
   - a paid ThetaData index tier.
2. **Rates before 2024:**
   - Massive Treasury yields (chosen; DR-0026's named cross-check, which matched ThetaData exactly in R0.1);
   - a paid ThetaData tier.
3. **Short history:**
   - exclude the name until it has 3 years (chosen);
   - allow 1 year at minimum.

## Decision
- **The volatility factor is `SPYIV30`:** SPY's 30-day ATM implied volatility, derived from ThetaData EOD option chains with the same method as every name's IV in `HIST-1` (parity forward, Black-76, interpolated to 30 days). It replaces VIX everywhere the R2 brief names it:
  - P3: the volatility factor;
  - P6: the IV-level model;
  - P9: the VRP regime variable.
  
  It's available back to 2022 (H5: 68 of 68 samples usable).
- **Rates:** `HIST-1` takes the 1-month, 3-month and 10-year Treasury yields from Massive for the whole window. The 9 sessions where the bond market is closed but the NYSE is open carry the previous observation forward, with that observation's original public time. Live boards keep ThetaData rates, as in R1.
- **Minimum history:** FAC-1, MKTV-1, HVOL-1 and RAND-L1 consider a name only once it has a full 3-year window of closes and IV in `HIST-1` at the fit date. Excluded names are counted per board. MKT-1 and RAND-1 are unaffected.

## Consequences
- **One source, one method** for the volatility factor and for every name's IV.
- **SPYIV30 isn't VIX:** it's ATM rather than variance-swap, and SPY rather than SPX. For a factor that drives daily IV moves, the two move together closely; the R2.4 diagnostics report the correlation over 2024–2026, where both exist.
- **ARM** is outside the R2 engines' eligible set until September 2026.
