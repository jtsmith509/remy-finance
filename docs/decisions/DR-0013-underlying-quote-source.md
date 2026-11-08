# DR-0013: Underlying quote source

- **Status:** proposed (the option was chosen by Josh on 2026-11-08; awaiting confirmation of this record)
- **Date:** 2026-11-08
- **Source:** R0.1 data verification (check 2); amends DR-0009 and the handoff §4 `UnderlyingQuote` data contract
- **Stage:** R0

## Context
R0.1 found that the ThetaData Stock FREE tier returns **403** for underlying quotes ("requires a value subscription"). `MarketState` needs `UnderlyingQuote` (bid, ask, sizes) at each snapshot, and ASSIGN-1 forced closes need the underlying bid/ask. Options Standard greeks provide only a 1-minute `underlying_price`. Its bar convention is undocumented and may include data from after the minute stamp.

## Options considered
1. **Upgrade ThetaData Stock to Standard:** real-time Nasdaq Basic bid/ask, with history from 2016.
2. Upgrade Stock to Value: history is fine, but live stock quotes are 15-minute delayed.
3. Stay on FREE: use the prior-minute greeks `underlying_price` and approximate the bid/ask. That changes a handoff rule.

## Decision
Option 1, chosen by Josh on 2026-11-08.
- **Replay:** underlying quotes from `/v3/stock/at_time/quote` at the snapshot and execution times.
- **Prospective:** `/v3/stock/snapshot/quote`.
- The venue is recorded in provenance. Use `venue=nqb` (Nasdaq Basic) unless R0.3 testing shows that `utp_cta` is available and better.

## Consequences
- After the upgrade, Josh reruns `scripts/verify_thetadata.py --only 2` to confirm PASS.
- Nasdaq Basic is not the consolidated NBBO. Its quotes can be slightly wider or narrower than the national best, so they are labelled `source=thetadata:nqb` in provenance.
- The greeks `underlying_price` is not used for the board. If it's ever used as a cross-check, it's read from the bar stamped one minute before `as_of`.
- This adds a recurring subscription cost.
