# DR-0045: HIST-1, the point-in-time history store

- **Status:** proposed (2026-11-23). The dividend treatment was decided by Josh: total return. The rest awaits his confirmation.
- **Date:** 2026-11-23
- **Source:** R2 brief P13 (HIST-1), B02 (mature labels), B10 (as-of corporate actions) and B12 (missing data); DR-0044 (SPY 30-day IV, Massive rate history, 3-year minimum history); EVAL-2 §7.2 (IV outcomes "derived with the identical method").
- **Stage:** R2, Part 1

## Context
- **What needs it:** the factor model, VRP, HVOL-1 and EVAL-2's IV and underlying scores all need daily history from November 2022.
- **What history is needed:** official closes, 30-day ATM implied volatility, Treasury yields, and the dividends and splits needed to turn closes into returns.
- **The rules it must keep:**
  - Every value must be readable **as of** a past moment, with no look-ahead.
  - Nothing may be rewritten once stored.
  - A corporate action must never change a fit made before the action was public.

## Options considered
1. **Storage:**
   - an append-only SQLite store, with each row the canonical JSON of a frozen record plus its SHA-256, and UPDATE/DELETE refused by triggers (chosen, like the registry);
   - write-once Parquet files per symbol and month (awkward for daily appends);
   - JSON files per observation (around 100,000 files).
2. **Returns:**
   - total return (chosen by Josh);
   - price return with special dividends added back;
   - pure price return.
3. **The IV method:**
   - MKT-1's own forward and Black-76 routines on the session's end-of-day chain (chosen, so the live and historical values are comparable);
   - the vendor's IV field (rejected in P13).

## Decision
- **The store:** `data/hist/hist1.sqlite`, append-only, with three tables.
  - `obs`: one row per (series, symbol, session), holding a frozen `HistObservation`. The series are `CLOSE`, `ATMIV30`, `YIELD_1M`, `YIELD_3M` and `YIELD_10Y`.
  - `events`: dividends and splits, as the existing `DividendEvent` and `CorporateAction` records from Massive.
  - `missing`: deterministic derivation failures, with their reason.
  - `events` are keyed on the market fact: (kind, symbol, date, and for dividends their type). `ingest_time` is never part of the key (review fix HIST1-PIT-01).
  - `unusable`: vendor dividends that couldn't be used (no declaration date, or not USD). Their ex-date return is excluded with `DIVIDEND_UNUSABLE:<reason>`, visible from that day's 09:30 open, rather than read as a price loss (review fix TR-2).
  - **Revisions:** a value that arrives again unchanged is a no-op. A *different* value for an existing key is never written over: it goes to an append-only `conflicts` table and is reported.
- **Public times** (B10; backfills keep the real historical public time, and `ingest_time` records when we fetched it):
  - **Closes and ATM IV:** 17:15 ET on the session, ThetaData's end-of-day report time (DR-0026).
  - **Yields:** 09:30 ET on the next session, as the R1 adapters do.
  - **Dividends and splits:** as the Massive adapter already stamps them: the declaration date, or the ex-date for schedule-declared payments (DR-0027); splits on their execution date (DR-0021).
- **`ATMIV30` method,** for each name and session, from that session's ThetaData end-of-day chain:
  1. **Expirations considered:** 7 to 120 calendar days out.
  2. **Discount factor:** from the latest *public* 1-month and 3-month yields, interpolated in maturity, as MKT-1 does (DR-0037).
  3. **Forward:** the median of up to 8 put-call parity pairs nearest the close, with at least 3 required (MKT-1's `parity_pairs` and `min_parity_pairs`). It's rejected if more than MKT-1's `max_forward_gap` (10%) from the close.
  4. **ATM variance:** the out-of-the-money put strike just below the forward and the call strike just above, both two-sided and above MKT-1's minimum mid. Black-76 IVs are taken from mids, with time to expiry measured from the 16:00 ET close. Total variance is interpolated linearly in log-moneyness to k = 0.
  5. **30-day value:** total variance interpolated linearly in time between the nearest valid expirations on either side of 30 days (one exactly at 30 days is used alone). Then IV30 = √(w30 / (30/365)).
  6. **No pair of expirations either side of 30 days:** recorded as missing, with its reason. There is no extrapolation and no fallback.
- **Total returns as of a moment t:**
  - r_s = ln(m_s · (C_s + D_s) / C_{s−1}) for consecutive XNYS sessions.
    - m_s is the share multiplier of splits effective on s (split_to/split_from), counting only splits public by t.
    - D_s is the sum of cash dividends with ex-date s, ordinary and special, counting only dividends public by t.
  - **No return is computed** across a missing close. A session with an unsupported action (a ticker change or merger, or a Massive special-dividend price adjustment with no matching cash dividend) is excluded and flagged (B10).
  - So an action that wasn't yet public at a fit's date can't affect that fit.
- **`HistoryView(store, as_of)`:**
  - the only read path for models;
  - returns only observations and events whose public time is at or before `as_of`;
  - raises `LookaheadError` if asked for a session after `as_of`'s date.
- **Backfill:** `remy hist build --start … --end …`.
  - **Coverage:** UNIV-CANDIDATES-1, SPY and the sector SPDRs, with 4 parallel ThetaData requests.
  - **Resumable:** keys already stored are skipped.
  - **Missing data:** fetch errors are retried on the next run, never recorded as missing. Missing inputs count as fetch errors too: no public 1-month or 3-month yield within 10 days, or an empty chain response (review fixes HIST1-PIT-02 and H3).
- **Not in this DR:**
  - the nightly HIST-1 and CLOSE-1 update, and tracked-only capture (EVAL-2 §6.3), which come with the scheduler work before the cut-over;
  - B12's eligibility and fallback rules, which go in the factor-model DR.

## Review (2026-11-23)
An internal adversarial review found 9 distinct defects, all fixed before the first backfill with regression tests: see `docs/review/HIST-1-findings.md`. The schema is now `HIST-1/2`.

**Open question for Josh:** should a USD dividend with no declaration date be kept, with public time 09:30 ET on its ex-date? That can't look ahead, and the session would stay in the fits. It is excluded for now.

## Consequences
- **One store** backs `CLOSE-1` (its `CLOSE` series), so the official-close store is a view of HIST-1 rather than a second copy.
- **Size:** about 100,000 observations, a few tens of MB.
- **Build time:** the first build makes about 94,000 option-chain requests, about 4–5 h with 4 workers (R2.0).
- **IV method reuse:** EVAL-2's IV outcomes and FAC-1's inputs share this method, so a change to it is a new version of every engine that uses it (EVAL-2 §1).
