# DR-0049: MKTV-1, its paths and the VRP estimate

- **Status:** accepted (Josh, 2026-11-24), with his six design answers (below).
- **Date:** 2026-11-24
- **Source:** R2 brief P2 (MKTV-1, B03), P6 (sticky moneyness), P7 (exit fills on paths), P8 (paths and time), P9 (VRP estimate), B02 (VRP labels), B05 (forwards on paths), B06 (cadence); EVAL-2 §1, §6 and §7.2; DR-0037 (MKT-1's mid-anchored mean); DR-0044 (`SPYIV30`); DR-0045 (HIST-1); DR-0048 (v4, ADJ-1).
- **Stage:** R2, Part 1 (R2.3). This DR comes before any path code depends on it, as P2 requires.

## Context
- **What MKTV-1 is:** the VRP-adjusted market benchmark of the long-option comparison (P3 contrasts FAC-1 with it).
  - It recommends long calls and puts with EXIT-1, ranked by ADJ-1.
  - Its terminal distribution is MKT-1's risk-neutral density with VRP-scaled variance (same forward, positive support), and IV is marked down by the same ratio along the path.
  - Its mean comes from valuing EXIT-1 on its own paths, with **no** re-centring on quoted mids (B03).
- **What B03 found:** scaling a terminal density doesn't define daily paths, intermediate IV or first passage of the profit target. Reusing DR-0037's mid anchoring would erase the VRP.

## Josh's answers (2026-11-24)
1. **The VRP transformation:** scale the smile's variance by ρ.
2. **The daily transition:** a Brownian bridge to a terminal draw from the scaled density.
3. **IV along the path:** each expiration's decision-board smile × √ρ, held fixed.
4. **The fallback:** the sector's fit, otherwise the name is excluded.
5. **The +5% bug signal:** tested on MKT-1's anchored mean (a quote-error test), never on MKTV-1's own mean.
6. **Shrinkage:** w = n_eff ÷ (n_eff + 12), fixed before any fit is seen.

## Decision

### The VRP ratio ρ (P9, B02)
- **The regression, per name:** ratio_s = RV_s ÷ IV_s², modelled as a + b × SPYIV30_s.
  - IV_s is the name's `ATMIV30` on session s (HIST-1, DR-0045).
  - SPYIV30_s is SPY's `ATMIV30` on s.
  - RV_s is the realized variance of the name's daily log total returns over the **21 sessions after s** (HIST-1's `total_returns`), annualized × 252.
  - Variance units throughout: annualized variance; IV as a decimal.
- **When a row is admitted (B02):** only once its whole window has ended and is public at the fit cutoff. The close of session s + 21 is public at 17:15 ET, so the response date is s + 21. A row is never built from data after the cutoff.
  - Sessions excluded from total returns (a missing close, an unsupported action) make that row's window incomplete, and the row is dropped and counted.
- **The window:** rows whose **response date** lies in the 3 years before the fit cutoff (rolling).
- **The fit:** ordinary least squares on daily, overlapping rows. The uncertainty is reported with Newey-West at lag 21, and the effective sample size as rows ÷ 21.
- **Shrinkage toward the sector (Josh, 2026-11-24):**
  - The sector's (a, b) is fitted the same way, pooled over the sector's names with at least 3 years of history (DR-0044).
  - The name's (a, b) = w × name + (1 − w) × sector, with w = n_eff ÷ (n_eff + 12), where n_eff is the name's effective sample size (rows ÷ 21). A name with 3 full years (about 36 effective windows) gets w ≈ 0.75.
  - Sector = the name's sector SPDR, under the same name-to-sector mapping the factor model uses (P3; fixed in the factor-model DR). SPY and the sector SPDRs use their own fit, unshrunk.
- **Applying it on a board:** ρ = a + b × SPYIV30 at the **previous session's public EOD value** (DR-0044, EVAL-2 §7.2), clipped to [0.3, 1.5].
  - The clipping frequency is reported per refit.
- **Refits** are monthly, dated and stored write-once with their inputs' hashes. A board uses the latest refit whose cutoff is at or before its decision time.
- **Fallback (Josh):** no usable name fit (too few mature rows, a failed fit, or no public SPYIV30) → the sector's fit. If that's missing too, the name leaves MKTV-1's pool for that board, counted by reason. EVAL-2 §7.2's IV forecast is then missing ("no VRP ratio").

### The terminal distribution (Josh: scale the smile's variance)
- For each expiration MKT-1 fits (DR-0030, DR-0038), MKTV-1 takes the fitted SVI total variance w(k) and uses **ρ × w(k)**, so every implied vol becomes IV × √ρ.
- It rebuilds the terminal density by Breeden-Litzenberger on the **same forward and discount factor**, with the same grid and arbitrage checks as MKT-1.
  - An expiration whose scaled fit fails those checks is rejected and flagged.
- The forward is preserved exactly and prices stay positive. The skew's shape in log-moneyness is kept.

### Paths (Josh: a bridge to the terminal; P8, B05, B06)
- **Count and draws:** 2,000 paths per board (P8), with a seed recorded in the run log. Terminal values S_T are drawn from the scaled density by **stratified quantiles** (one per 1/2,000 stratum, jittered by the seed), so the terminal marginal is reproduced to sampling error.
- **Nodes:** the remaining twice-daily decision times (10:00 and 15:45 ET, and 12:45 on half days) up to the expiration, plus one final step to the expiration's official close (16:00, or 13:00 on a half day). EVAL-2 §7.1 and the R2.5 path DR use the same close step.
- **The bridge:** x(t) = log(S(t) ÷ F(t)), where F(t) is each node's forward from the decision board's frozen carry and rate curve, rolled down to the remaining maturity (B05).
  - x runs from x(0) = log(S_0 ÷ F(0)) to x(T) = log(S_T ÷ F(T)), as a Brownian bridge whose variance is spread in proportion to calendar time.
  - The bridge's total variance is the scaled ATM total variance ρ × w(0) for that expiration.
  - Each name and expiration is simulated on its own: MKTV-1 has no factor structure. Expirations of one name share the bridge's Gaussian increments up to the earlier expiration, so a name's prices are consistent across its expirations.

### Option values along a path (Josh: a fixed scaled smile; P6, P7)
- At a node with forward F(t) and remaining time τ, an option of strike K is valued by Black-76 with IV = √(ρ × w(k) ÷ T) at k = log(K ÷ F(t)): the expiration's scaled smile, sticky in moneyness, held fixed for the life of the path.
- **Time-zero check (B05):** with ρ = 1, the node-0 value must reproduce MKT-1's fitted mid within a tolerance of max(one tick, 2% of mid). An expiration that fails is rejected and flagged.
- **Simulated exit fills (P7):** the modelled mid minus the decision board's relative half-spread for that contract.

### The forecast and selection
- **Each eligible long contract's EXIT-1 net return on capital at risk** is computed on every path: entry at FILL-1 on the decision board's quotes, then EXIT-1's target and time stop on the path, with fees.
  - The recorded distribution is EVAL-1's 15 quantile levels and bins.
  - The mean is the path average, **with no re-centring** on quoted mids (B03).
- **Ranking:** ADJ-1, mean − (1 ÷ DF − 1), as DR-0048. Selection: SELECT-1.
- **The eligible set:** EVAL-2 §1's, shared by the four long-option engines (DR-0041, DR-0044's 3 years, EXIT-1's 7–60 days).
- **The bug signal (Josh):** a contract is excluded, and logged as a bug signal in MKTV-1's run log, when **MKT-1's** mid-anchored mean for it (DR-0037) exceeds +5%, which marks a crossed or stale quote. MKTV-1's own mean is never tested.
- **The run log** records, per name: ρ, whether it came from the name or the sector fit, the refit's identity, and the fallback reason if any.

### Not in this DR
- **EXIT-1's own rules** (the 2× target valued at the bid, the 7-day time stop, retries and settlement): these come from the EXIT-1 DR (R2.6). MKTV-1 values whatever EXIT-1 specifies.
- **FAC-1's and HVOL-1's paths** (R2.5, R2.7).

## Addendum: details settled while building (Josh, 2026-11-24)
These fill gaps DR-0049 left open. They reopen nothing it decided. See `docs/review/VRP-findings.md`.
1. **A usable fit** needs at least **12 effective windows** (rows ÷ 21, about a year of mature rows). The rule is the same for a name's fit and a sector's pooled fit.
2. **A mapped name whose sector has no usable fit** uses its own fit, unshrunk. It's recorded as source "own", with the reason "no <sector> sector fit: own fit unshrunk".
3. **"3 years" for the sector pool:** the name's first stored official close is at least 3 years before the refit's cutoff. Pool members and the names left out are stored in each refit.
4. **Also recorded by the code:**
   - Only SPY and the sector SPDRs use their own fit; a name outside the sector mapping has no ratio.
   - A shrunk line carries its own clipping share and no standard errors, and each refit reports one overall clipping share.
   - The inputs hash covers the method and its constants, the cutoff, the symbols, the mapping, the pools and every admitted row.

## Consequences
- **A pure market benchmark with a VRP view.** MKTV-1 differs from MKT-1 only by ρ and by valuing EXIT-1 on paths. Its paths reproduce its terminal density exactly, and at ρ = 1 they reproduce MKT-1's distribution and the board's mids.
- **No IV dynamics.** MKTV-1's early exits don't benefit from IV moves. That's deliberate: IV dynamics are FAC-1's (P6). The P3 contrast then measures the factor model against the market's own view adjusted for the risk premium.
- **Tests before use:**
  - ρ = 1 reproduces MKT-1's terminal density and the time-zero mids;
  - the scaled density keeps the forward and positive support;
  - the bridge reproduces the terminal marginal (a KS test at the 2,000-path size);
  - the VRP estimator recovers a known premium on synthetic series;
  - changing any return after the fit cutoff leaves the fit unchanged (B02).
