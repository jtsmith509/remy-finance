# DR-0015: Quote age handling (`QAGE-1`)

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** R0.1 data verification (check 4 rerun); affects `OptionQuote` quality flags (handoff §4), movegen priceability (R0.7) and FILL-1 (R1.2)
- **Stage:** R0

## Context
The R0.1 check-4 rerun found no quotes stamped after the requested time and none from a prior day. It did find a tail of **old** quotes, all stamped at the 09:30 open:
- About 4% of AAPL contracts are older than 60 s at 10:00.
- Up to about 4% of AAPL contracts are older than 1 hour at 15:45.

A quote unchanged since the open is still the standing NBBO, not a data error. It is, however, a weaker basis for a paper fill.

## Options considered
1. **Flag only, still usable:** record each quote's age and set a `STALE_QUOTE` quality flag above a threshold. Candidates stay priceable and fillable, and reports break out flagged legs.
2. Exclude above a threshold: an old leg makes the candidate `UNPRICEABLE(STALE_QUOTE)` and blocks fills.
3. Flag now and decide at R1, using real fill-rate data.

## Decision
Option 1, chosen by Josh on 2026-11-08 (`QAGE-1`):
- Every `OptionQuote` in a snapshot or execution quote stores `quote_age_s = as_of − quote_time`.
- The quality flag `STALE_QUOTE` is set when `quote_age_s > 300` (5 minutes).
- Flagged quotes stay priceable and fillable under FILL-1. Candidates and fills record whether any leg was flagged.
- `report trades` and `report cumulative` break out fill rate and realized-versus-predicted return for trades with any `STALE_QUOTE` leg.
- Underlying quotes from `stock/at_time` carry the requested time, not the quote time (R0.1), so `quote_age_s` is unknown for them. They get the flag `QUOTE_TIME_IS_REQUEST_TIME` instead.

## Consequences
- No candidates are dropped for quote age. The effect is measured, not assumed.
- Switching to exclusion later is a new DR (`QAGE-2`) and creates a separate, linked set of records. Past records are never rewritten.
