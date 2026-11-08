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
Adopt option 1 as the order of preference. The user's ThetaData **Rate** subscription is on the FREE tier. Whether that tier serves historical daily rates is checked in R0.1 (see the memo). For each rate:
- `public_time` = the publisher's release time for the observation date. For SOFR, that's about 08:00 ET on the next business day. For Treasury bill rates, it's end of day on the observation date.
- A snapshot may use only the latest rate whose `public_time` ≤ `as_of`. That is typically the previous business day's value.

## Consequences
- In MKT-1, rates are a fallback and a cross-check only. The primary discount factor comes from parity.
- If ThetaData's free rate data turns out to be insufficient, adding Treasury or FRED requires a small second adapter behind the same `rates(known_as_of)` interface.
