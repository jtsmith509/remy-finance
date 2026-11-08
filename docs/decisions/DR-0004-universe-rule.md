# DR-0004: Point-in-time universe rule

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D4
- **Stage:** R0 (R0.5)
- **Settled context:** About 50 liquid US stocks and ETFs, selected point-in-time (spec §3, §15).

## Context
Survivorship bias is a form of look-ahead. The universe for date t may use only information available at t, and names that later delist or lose liquidity must stay in the historical record.

## Options considered
1. **Proposed default:** each month, rank a configured candidate list of about 80 names by trailing **20-trading-day average option volume**, using only data available at selection time, and take the top 50. Membership is stored per date and never rewritten.
2. A static hand-picked list of 50. It's simple, but it's survivorship-biased in replay.
3. Rank by open interest or by quoted spread. Spread is a better measure of executability but is noisier; open interest lags.
4. Rank the entire optionable universe. It's unbiased, but needs volume data for thousands of roots.

## Decision
Adopt option 1, with a version tag (`UNIV-1`):
- **Selection date:** the first trading day of each month, before the open snapshot. The input window is the 20 trading days ending the prior session.
- **Volume measure:** total daily option contract volume across all of a root's contracts, from end-of-day data.
- **Stored per decision:** the candidate list version, input data hashes, ranked scores and the resulting membership.

## Consequences
- The ≈80-name candidate list is itself a human choice made today, so it carries some survivorship bias. That bias is weaker than with a fixed 50, but it isn't zero. The candidate list is versioned and dated, and this limitation is recorded in every replay report.
- Requires historical option EOD volume per root. R0.1 checks whether ThetaData provides it.
- Bootstrapping a replay month needs the 20 trading days before it.
- Mid-month delistings or corporate actions remove a name from candidate generation from the effective date. The membership record is not rewritten.
- **Owner decision (Josh, 2026-11-08):** Claude drafts the candidate list and Josh approves it before R0.5. The draft is below.

## Draft candidate list `UNIV-CANDIDATES-1` (awaiting Josh's approval)

Drafted 2026-11-08 from general knowledge of US option liquidity. **Check each ticker for current listing and symbol changes before approval.** There are 80 names in total.

**ETFs (24):** SPY, QQQ, IWM, DIA, XLF, XLE, XLK, XLV, XLI, XLY, XLP, XLU, XLB, XLC, SMH, KRE, XBI, GLD, SLV, GDX, TLT, HYG, EEM, FXI

**Single names (56):** AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, AVGO, AMD, NFLX, ORCL, CRM, ADBE, INTC, MU, QCOM, TSM, ARM, SMCI, PLTR, UBER, SHOP, COIN, MSTR, HOOD, SOFI, PYPL, BABA, JPM, BAC, C, WFC, GS, V, MA, XOM, CVX, BA, CAT, DE, F, GM, RIVN, DIS, T, VZ, PFE, MRK, JNJ, LLY, UNH, ABBV, KO, WMT, COST, HD
