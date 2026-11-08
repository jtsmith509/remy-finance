# DR-0021: Data-layer semantics (`PointInTimeView`, adapters)

- **Status:** proposed
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.3; DR-0011, DR-0013, DR-0014, DR-0017
- **Stage:** R0

## Decision
1. **The view raises; it never filters.** `PointInTimeView` raises `LookaheadError` in two cases: when a caller asks for a time after `as_of`, and when a source returns any observation available after `as_of`. A source that leaks future data has a defect to fix, not to hide.
2. **Quote semantics:** `chain_quotes_at(underlying, ts)` returns each contract's latest NBBO at or before `ts`, **within the same ET session**; prior-day quotes are dropped. This matches ThetaData `at_time`, as measured in R0.1. `FixtureSource` implements the same semantics.
3. **Prospective reads** use the adapter's `live_*` methods: ThetaData snapshot endpoints. The snapshot's `as_of` is set **after** the fetch completes, so a live quote can't post-date its board.
4. **ThetaData mapping:**
   - JSON is parsed with `parse_float=Decimal`.
   - Naive ET timestamps are localized to America/New_York and converted to UTC.
   - A root other than the underlying, or a root with a numeric suffix, is marked non-standard.
   - Status 472 (`NO_DATA`) is an empty result; any other non-200 status raises.
   - Rates follow DR-0011: percent is converted to a fraction, weekend rows are dropped, and `public_time` is the next XNYS session at 09:30 ET.
   - Asking ThetaData for dividends or corporate actions **raises**. It must not silently return nothing.
5. **Polygon mapping:**
   - **Dividends:** `public_time` is the declaration date at 16:00 ET. Records with no declaration date are excluded and listed in `skipped`.
   - **Splits:** the feed has no announcement date, so `public_time` is the **execution date at 09:30 ET**. That is later than the real announcement, which is the safe direction.
   - **Special cash dividends** (`SC`, plus the `LT`/`ST` capital-gain codes) are typed `SPECIAL` and also reported as `SPECIAL_DIVIDEND` corporate actions.
   - The key is sent as a bearer token, read from `POLYGON_API_KEY`.
6. **`CompositeSource`** routes quotes and rates to ThetaData, and dividends and corporate actions to Polygon.

## Consequences
- Because splits only become known on their execution day, the universe and contract filters may lag the real announcement by a few weeks. During that lag, the adjusted-root rule (DR-0014) still excludes the adjusted contracts.
- Before anything depends on the Polygon adapter, it must be verified live with `scripts/verify_polygon.py`. That run also spot-checks ThetaData's EOD close for DR-0010.
