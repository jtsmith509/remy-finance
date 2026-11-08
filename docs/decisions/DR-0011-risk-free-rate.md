# DR-0011: Risk-free rate source

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D11
- **Stage:** R0 (R0.3) / R1 (R1.4)

## Context
MKT-1 infers forwards and discount factors from put–call parity. A risk-free rate is still needed as a fallback and a sanity check, and for any Black-Scholes step. Rates are observations and must be point-in-time (`RatePoint` with `public_time`).

## Options considered
1. **Proposed default:** a daily rate from ThetaData's interest-rate data if available; otherwise a public Treasury-bill series, stored as point-in-time observations.
2. Always use the U.S. Treasury daily bill rates or FRED (DTB3 and similar). They're free and authoritative, but they're a second vendor.
3. Use only parity-implied rates. That needs nothing extra, but is noisy for short tenors and fails when the parity fit fails.

## Decision
Adopt option 1. R0.1 verified that ThetaData Rate FREE works at `/v3/interest_rate/history/eod` (symbols `SOFR` and `TREASURY_M1` … `TREASURY_Y30`):
- `rate` is in **percent**, and `created` is a date only.
- There is one row per **calendar** day, with weekends forward-filled.
- No publication time is given.

For each rate:
- `public_time` for the row dated D is the **next business day at 09:30 ET**. That is conservative for both SOFR, published about 08:00 ET on D+1, and Treasury bill rates.
- A snapshot may use only the latest row whose `public_time` ≤ `as_of`.
- Weekend and holiday forward-filled rows are dropped in favour of the last business-day observation.

## Consequences
- In MKT-1, rates are a fallback and a cross-check only. The primary discount factor comes from parity.
- If ThetaData's free rate data turns out to be insufficient, adding Treasury or FRED requires a small second adapter behind the same `rates(known_as_of)` interface.
