# HIST-1: findings and resolution (internal adversarial review)

- **Reviewer:** an internal multi-agent review, not Codex. Five review lenses (point-in-time hygiene, total returns, the ATM IV method, store integrity, and operations), then an independent refutation pass on every candidate.
- **Reviewed source:** `e8f6d83`, the first HIST-1 commit (DR-0045).
- **Result:** 17 candidates, of which **15 survived refutation** and 2 were refuted. Several reviewers found the same defect independently, so the 15 come down to **9 distinct defects**.
- **Fixed:** all 9 on 2026-11-23, before any backfill was run. No HIST-1 store exists yet, so the schema change (`HIST-1/2`) needs no migration.
- **Regression tests:** `tests/unit/history/test_review_fixes.py`, 14 tests. To check that the tests catch the bugs, I reverted each fix and re-ran the tests: they fail as expected. For example, with the empty-chain fix and the pool fix reverted, their 2 tests fail.
- **Gate:** ruff, format and mypy clean; pytest **600 passed**.

| # | Severity | Finding (merged duplicates) | Fix |
|---|---|---|---|
| HIST1-PIT-01 (also TR-1, H1, H1-EV-DUP) | blocker | **Events were stored again on every re-run.** Each dividend or split was keyed on the hash of the full record, and that hash included `ingest_time`. So every `hist build` stored a new copy of each one, and total returns then summed the dividends and multiplied the split ratios. A vendor revision was likewise added on top of the original. | Each event is now keyed on the market fact: (kind, symbol, date, ident), where ident is the dividend type, so an ordinary and a special dividend on one ex-date are both kept. A re-fetch, where only `ingest_time` differs, is a no-op. A different record for the same key is logged in `conflicts` and the original is kept. As a backstop, `total_returns` also de-duplicates by that key. |
| HIST1-PIT-02 (also H2) | blocker | **A failed yields fetch permanently erased ATM IV.** With no public rates, every expiration was rejected and the value was recorded as missing. Missing rows are append-only, so the next run skipped those sessions. | `public_rates()` raises `MissingInputError` when the 1-month or 3-month yield is absent or more than 10 days old. The error is counted as a fetch error and retried; nothing is recorded as missing. The check runs before the chain request, so no request is wasted. |
| IV-01 (also H1-NONSTD-ROOT) | major | **Adjusted option roots (`XYZ1`) were mixed into the chain.** They share strikes with the standard root, which corrupted the parity forward and the IV. | `rows_from_vendor(raw, underlying)` keeps only standard-deliverable roots. It uses `is_standard_root`, a rule now shared with `ThetaDataSource._contract` so the two can't drift apart. |
| H3 | major | **An empty chain response was recorded as permanently missing,** though it can be a vendor or terminal hiccup. | An empty response raises `EMPTY_CHAIN` as a fetch error, which is retried. A response with rows that still yields no valid expiration is a derivation result and is recorded as missing, as before. |
| TR-2 (also H1-DIV-SKIP) | major | **Unusable dividends disappeared silently.** A dividend with no declaration date, or not in USD, was dropped, so its ex-date price drop read as a loss. Ticker-event fetch failures were also swallowed. | Massive records both in structured form (`unusable_dividends`, `event_failures`). The build stores the unusable dividends in a new append-only `unusable` table. `total_returns` excludes each such ex-date with `DIVIDEND_UNUSABLE:<reason>`, visible from the ex-date's 09:30 open. Event failures become fetch errors. `hist build` and `hist status` report both. |
| H4 | minor | **A revision was logged again on every run.** | Each conflict carries the hash of the offered fact. The same revision against the same kept record is logged once, enforced by a `UNIQUE` constraint and a trigger. |
| H5 | minor | **Store creation was not atomic.** A crash before the triggers were created left a store without append-only protection. | The store is created in one `BEGIN IMMEDIATE` transaction, with the meta row last. Every open checks that all the triggers exist. A file without a meta table raises `HistError`. |
| H1-POOL-DRAIN | minor | **Ctrl-C or a store error left about 90,000 queued chain requests running,** with nobody storing the results. | The thread pool is managed explicitly. Any exit from the loop cancels the queued futures. |
| H1-RATES-COST | minor | **Each IV task re-read and re-validated the whole yield history,** about 1.1 h of CPU time over the backfill. | `HistoryView.latest` uses `HistStore.latest_observation`, which reads newest-first and stops at the first public row. It is still the only read path. |

## Refuted (no change)

- **IV-02, half-day sessions.** Time to expiry is measured from 16:00 ET even on 13:00 closes. The error is about 0.05 vol points, and MKT-1 uses the same convention. It is a recorded convention, not a defect.
- **IV-03, monthly-only names in expiration week.** With no valid expiration below 30 days, the value is missing (`NO_BRACKET_30D`). This is the no-extrapolation rule DR-0045 chose.

## Open for Josh (DR-0045)

- **Undeclared dividends:** they are now excluded from total returns rather than kept. The alternative is to keep a USD dividend that has no declaration date, with public time 09:30 ET on its ex-date. It can't look ahead, because a dividend is always declared before its ex-date, and it would keep those sessions in the fits. That rule differs from DR-0027's (the declaration date), so it needs his confirmation. `hist status` will show how many sessions this affects once the backfill has run.
