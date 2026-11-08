# DR-0027: Schedule-declared dividends become public on the ex-date (amends DR-0014)

- **Status:** accepted (confirmed by Josh, 2026-11-08, after the live Massive check)
- **Date:** 2026-11-08
- **Source:** Live Massive check M1 (`docs/memos/evidence/R0.1-massive-20261108T224316Z.md`); amends DR-0014 item 2 (accepted)
- **Stage:** R0

## Context
DR-0014 sets a dividend's `public_time` to its declaration date at 16:00 ET. The live check showed that SPY's 2026 distributions, with ex-dates 2026-03-20, 06-18 and 09-18, **all carry the same declaration date, 2026-01-02**, yet each has an exact amount (1.796999, 1.903516, 1.888834).
- **What was public when:** the *schedule* (the dates) is announced at the start of the year. An ETF's per-distribution *amount* isn't known until shortly before each ex-date.
- **The defect:** applying DR-0014 to these records would put September's amount on boards from January. That's look-ahead.
- **Company dividends are fine:** KO and AAPL show one declaration per dividend, made about 2–8 weeks before the ex-date with the amount. DR-0014's rule is correct for those.

## Options considered
1. **Detect schedule declarations and make those records public on the ex-date** at 09:30 ET. This is always later than the real announcement, so it can't look ahead.
2. Option 1, plus a **conservative estimated amount** (the previous distribution) available from the declaration date, used only by the ASSIGN-1 dividend rule. That's more protective against early assignment, but an estimate is a forecast, and the board must not hold forecasts. It would need a separate, clearly labelled record type.
3. Find the issuer's actual amount-announcement times (for SPY, State Street's notices) and use those. That's accurate but needs another data source.

## Decision: option 1
- **Detection:** a record is *schedule-declared* when its declaration date is shared by two or more different ex-dates for the same ticker. For these, `public_time` = **ex-date at 09:30 ET**. The vendor's `declaration_date` is kept on the record as data.
- **All other dividends** keep DR-0014's rule: declaration date at 16:00 ET.
- **The adapter** (`MassiveSource`) lists every schedule-declared record in `schedule_declared`, and `verify_massive.py` reports them.

## Consequences
- **No look-ahead** from ETF distribution amounts.
- **Less conservative ASSIGN-1 for ETFs:** a schedule-declared distribution isn't known at the 15:45 snapshot the day before its ex-date, so the dividend rule can't fire for SPY-style ETFs. A short ITM call over an ETF's ex-date can still be assumed assigned through the extrinsic-value threshold rule. Option 2 or 3 would close this gap later, through a new DR.
- **Detection misses a lone schedule-declared record**, one whose schedule declaration happens to cover a single ex-date. The check will keep listing these so a miss can be spotted.
