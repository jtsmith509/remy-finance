# DR-0026: Which source supplies each data type (ThetaData, Massive, calendar)

- **Status:** accepted (confirmed by Josh, 2026-11-08, after the live Massive check)
- **Date:** 2026-11-08
- **Source:** R0.1 extension (Massive), `docs/memos/R0.1-data-verification.md`; refines DR-0011, DR-0013 and DR-0014 (accepted) and DR-0021 (accepted)
- **Stage:** R0

## Context
ThetaData (Options Standard plus Stock Standard) supplies options and underlying quotes. R0.1 found it has no dividends or corporate actions, so DR-0014 chose Polygon, now renamed **Massive**. Josh has opened a Massive account. We need one explicit source per data type: which vendor is primary, what cross-checks it, and what plan each needs. Every vendor stays behind the `MarketDataSource` adapter.

## Options considered
- **ThetaData for everything it has**, Massive only where ThetaData has nothing.
- **Massive for all stock data,** including NBBO on the Stocks Advanced plan, with ThetaData for options only. That means paying twice for stock quotes, and the board's underlying would come from a different vendor than its options.
- **Massive for reference and economic data** (dividends, splits, ticker events, yields) where it's richer, and ThetaData for all market quotes.

## Decision

| Data type | Primary | Cross-check or fallback | Plan needed |
|---|---|---|---|
| Option chains, NBBO and sizes; option volume | **ThetaData** (`ThetaDataSource`) | none | Options Standard (have it) |
| Underlying stock NBBO | **ThetaData** (DR-0013) | Massive `/v3/quotes`, only if ever upgraded | Stock Standard (have it); Massive would need **Stocks Advanced** |
| Dividends (ex-date, declaration date → `public_time`) | **Massive** `/stocks/v1/dividends` (DR-0014; schedule-declared records: DR-0027) | none | Current plan (docs say 2 years on Basic; **the live run returned full history**) |
| Splits and stock-dividend adjustments | **Massive** `/stocks/v1/splits`; stock dividends ≥ 25% are treated as splits | OSI-root rule (DR-0014) | Current plan (full history observed) |
| Ticker changes | **Massive** `/vX/reference/tickers/{id}/events`; the earliest event is the initial ticker, not a change | none | Current plan |
| Risk-free rate | **ThetaData** `interest_rate/history/eod` (DR-0011) | **Massive** `/fed/v1/treasury-yields`, compared in M4 | Rate FREE / any Massive plan |
| Trading calendar (sessions, half-days) | **`exchange_calendars` (XNYS)** | Massive upcoming holidays and status (forward-looking only), suitable for R1.6 `doctor` | n/a / any plan |
| Official close (EXP-1) | **ThetaData** stock EOD `close` (DR-0010) | Massive daily bar close (M6) | Stock Standard / any plan |

The adapter is implemented as `MassiveSource` (`data/massive.py`):
- **Endpoints:** it uses the non-deprecated `/stocks/v1/*` endpoints, since `/v3/reference/dividends` and `/v3/reference/splits` are deprecated.
- **Configuration:** it reads `MASSIVE_API_KEY` and `https://api.massive.com`.
- **Requests:** it throttles them and retries on 429.
- **Options:** asked for options data, it raises.

Snapshot boards are built with `CompositeSource(quotes=ThetaData, events=Massive)`.

## Live results (2026-11-08, current plan)
Full report: `docs/memos/evidence/R0.1-massive-20261108T224316Z.md`.
- **M1 dividends:** recent records have declaration dates, and none were declared after their ex-date. **SPY's distributions are schedule-declared**, a look-ahead hazard handled by DR-0027. Records from 2003–2012 often lack declaration dates; that's harmless.
- **M2 splits and events:** OK, with full history (AAPL back to 1987). Two adapter fixes came out of it:
  - Each symbol's first ticker event (2003-09-10, naming its own ticker) isn't a change.
  - NVDA's 2021 4-for-1 split is labelled `stock_dividend`, so large stock dividends are treated as splits.
- **M3 stock NBBO:** **403, needs Stocks Advanced**, as the docs said. Not needed (ThetaData).
- **M4 Treasury yields:** OK. The 3-month yield **equals ThetaData's on all 5 days**.
- **M5 holidays and status:** OK, with 0 disagreements with XNYS, including the 2026-11-27 and 2026-12-24 half-days.
- **M6 daily close:** **33 of 33 sessions agree to the cent** with ThetaData EOD. This is the DR-0010 spot-check, now done.

## Consequences
- **Paid upgrades:** none needed for R0–R1. The current plan already returned full dividend and split history. **Stocks Advanced** isn't needed, because ThetaData Stock Standard covers the underlying NBBO.
- **Free-plan rate:** the limit is about 5 requests a minute. Refreshing dividends and actions for 50 names should batch with `ticker.any_of` and cache daily. That's an R1.6 implementation detail and needs no new DR.
- **Live run:** it confirmed the table. Rates and closes agree between the two vendors, so either can serve as the other's cross-check.
