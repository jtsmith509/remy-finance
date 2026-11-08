# DR-0014: Dividend and corporate-action source

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A); endpoints and per-type sources refined by **DR-0026** (proposed)
- **Date:** 2026-11-08
- **Source:** R0.1 data verification (check 3); supports DR-0009 (ASSIGN-1 dividend rule) and handoff §4 (`DividendEvent`, `CorporateAction`, exclusion of non-standard deliverables)
- **Stage:** R0

## Context
R0.1 found that ThetaData v3 has **no** dividend, split or corporate-action data; all 8 candidate endpoints returned 404. ASSIGN-1 needs each dividend's ex-date, amount and the time it became public. Corporate actions create adjusted, non-standard-deliverable option contracts, which must be excluded from the universe.

## Options considered
- **Dividend source:** Polygon.io / Massive reference API; Nasdaq dividend history (scraped); SEC EDGAR 8-K filings; or deferring the dividend rule.
- **Dividend `public_time`:** declaration date at 16:00 ET; next trading day at 09:30 ET; or declaration date at 00:00 ET.
- **Corporate actions:** Polygon splits plus an OSI-root rule; the same plus OCC Information Memos; or the OSI-root rule only.

## Decision
Chosen by Josh on 2026-11-08:
1. **Dividends** come from the **Polygon.io / Massive** reference dividends API: `declaration_date`, `ex_dividend_date`, `cash_amount`, frequency and type.
2. **`public_time` = the declaration date at 16:00 ET** (America/New_York, converted to UTC). In practice a dividend is first usable at the next morning's 10:00 snapshot. A record with no declaration date is **not usable** by the dividend rule. It is logged, and any fallback rule needs a new DR.
3. **Corporate actions:**
   - Polygon's splits endpoint supplies split dates.
   - Any option whose OSI root carries a numeric suffix (for example `AAPL1`) is treated as **non-standard deliverable and excluded**.
   - Special (non-ordinary) cash dividends from Polygon are also logged as `CorporateAction` records, because OCC may adjust contracts for them.
4. Access sits behind the `MarketDataSource` adapter (`dividends(symbol, known_as_of)`, `corporate_actions(symbol, known_as_of)`). The API key goes in the environment variable `POLYGON_API_KEY`, never in the repo.

## Consequences
- Adds a second vendor. Polygon's free tier is rate-limited (about 5 requests per minute), which is enough for about 80 names refreshed daily with caching. A paid tier is needed only if that proves insufficient.
- Every fetched record is stored with its `ingest_time`. Polygon revisions are kept as new observations and never overwrite earlier ones, so replay can't see later corrections.
- A short verification of Polygon (declaration-date coverage for SPY, AAPL and KO over 2 years) is done at the start of R0.3, before anything depends on it.
- OCC Information Memos are not used for now; they can be added by a new DR if the OSI-root rule misses an adjustment.
