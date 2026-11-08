# DR-0017: Observation time model and the board's look-ahead invariant

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §4 (four timestamps), §0 rule 4 (no look-ahead), R0.3 (`PointInTimeView` checks `public_time`, or `quote_time` for quotes); task R0.2
- **Stage:** R0

## Context
The handoff gives every observation up to four timestamps: event, public, ingest, and the consuming decision's `as_of`. The look-ahead guard needs a single, unambiguous "available from" time per observation.

## Options considered
1. **Two observation shapes with one `available_time`:**
   - Quotes carry `quote_time`, and are available at `quote_time`.
   - Other observations (dividends, corporate actions, rates) carry `event_time`, `public_time` and `ingest_time`, and are available at `public_time`.
2. Every observation carries all three timestamps, with quotes setting event = public = quote time. That's redundant, and the three can drift apart.

## Decision
Option 1.
- **All datetimes** are timezone-aware and normalized to UTC; naive or numeric timestamps are rejected.
- **`as_of`** is a field of `MarketState`, not of the observations.
- **`MarketState` refuses to exist with look-ahead.** Construction raises `LookaheadError` if any embedded observation has `available_time > as_of`. Equality is allowed: an observation at exactly `as_of` is known. This is a second line of defence behind `PointInTimeView` (R0.3).
- **`LookaheadError` is not a `ValueError`,** so pydantic can't swallow it into a generic validation error.
- **`ingest_time` is recorded but not checked against `as_of`.** In replay we always ingest long after `as_of`.

## Consequences
- A board can't be built containing data that wasn't available at its decision time, whatever code path builds it.
- Vendor data whose timestamp is the *request* time rather than the quote time gets `QUOTE_TIME_IS_REQUEST_TIME` (DR-0015). That applies to ThetaData stock `at_time` quotes, per R0.1. The guard can't detect staleness in such data, only future stamps.
