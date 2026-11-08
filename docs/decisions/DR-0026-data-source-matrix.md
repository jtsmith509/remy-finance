# DR-0026: Which source supplies each data type (ThetaData, Massive, calendar)

- **Status:** proposed (verdicts to be confirmed by the live `scripts/verify_massive.py` run)
- **Date:** 2026-11-08
- **Source:** R0.1 extension (Massive), `docs/memos/R0.1-data-verification.md`; refines DR-0011, DR-0013 and DR-0014 (accepted) and DR-0021 (accepted)
- **Stage:** R0

## Context
ThetaData (Options Standard plus Stock Standard) supplies options and underlying quotes. R0.1 found it has no dividends or corporate actions, so DR-0014 chose Polygon, now renamed **Massive**. Josh has opened a Massive account. We need one explicit source per data type: which vendor is primary, what cross-checks it, and what plan each needs. Every vendor stays behind the `MarketDataSource` adapter.

## Options considered
- **ThetaData for everything it has**, Massive only where ThetaData has nothing.
- **Massive for all stock data,** including NBBO on the Stocks Advanced plan, with ThetaData for options only. That means paying twice for stock quotes, and the board's underlying would come from a different vendor than its options.
- **Massive for reference and economic data** (dividends, splits, ticker events, yields) where it's richer, and ThetaData for all market quotes.

## Decision (proposed)

| Data type | Primary | Cross-check or fallback | Plan needed |
|---|---|---|---|
| Option chains, NBBO and sizes; option volume | **ThetaData** (`ThetaDataSource`) | none | Options Standard (have it) |
| Underlying stock NBBO | **ThetaData** (DR-0013) | Massive `/v3/quotes`, only if ever upgraded | Stock Standard (have it); Massive would need **Stocks Advanced** |
| Dividends (ex-date, declaration date → `public_time`) | **Massive** `/stocks/v1/dividends` (DR-0014) | none | Any Stocks plan. **Basic: 2 years** of history; **Starter** for longer replay |
| Splits and stock-dividend adjustments | **Massive** `/stocks/v1/splits` | OSI-root rule (DR-0014) | Any plan (2 years on Basic) |
| Ticker changes | **Massive** `/vX/reference/tickers/{id}/events` | none | Any plan |
| Risk-free rate | **ThetaData** `interest_rate/history/eod` (DR-0011) | **Massive** `/fed/v1/treasury-yields`, compared in M4 | Rate FREE / any Massive plan |
| Trading calendar (sessions, half-days) | **`exchange_calendars` (XNYS)** | Massive upcoming holidays and status (forward-looking only), suitable for R1.6 `doctor` | n/a / any plan |
| Official close (EXP-1) | **ThetaData** stock EOD `close` (DR-0010) | Massive daily bar close (M6) | Stock Standard / any plan |

The adapter is implemented as `MassiveSource` (`data/massive.py`):
- **Endpoints:** it uses the non-deprecated `/stocks/v1/*` endpoints, since `/v3/reference/dividends` and `/v3/reference/splits` are deprecated.
- **Configuration:** it reads `MASSIVE_API_KEY` and `https://api.massive.com`.
- **Requests:** it throttles them and retries on 429.
- **Options:** asked for options data, it raises.

Snapshot boards are built with `CompositeSource(quotes=ThetaData, events=Massive)`.

## Consequences
- **Paid upgrades:** none needed for R0–R1 on current knowledge. **Starter** becomes worthwhile only if replay must go back more than 2 years, for older dividends and splits. **Stocks Advanced** isn't needed, because ThetaData Stock Standard already covers the underlying NBBO.
- **Free-plan rate:** the limit is about 5 requests a minute. Refreshing dividends and actions for 50 names should batch with `ticker.any_of` and cache daily. That's an R1.6 implementation detail and needs no new DR.
- **Pending the live run:** this record stays proposed until `verify_massive.py` has run. If it shows a different plan entitlement, or ThetaData and Massive disagreeing on rates or closes, the table is revised before acceptance.
