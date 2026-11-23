# CLOSE-1 (DR-0046): findings and resolution (internal adversarial review)

- **Reviewer:** an internal multi-agent review, not Codex. Five lenses (point-in-time hygiene, scheduler safety, conformance to P12/EVAL-2/DR-0046, data integrity and concurrency, test adequacy), then a skeptic on each candidate, trying to refute it.
- **Reviewed:** `35db39a`.
- **Result:** 20 candidates, of which **11 survived** and 9 were refuted. The 11 come down to **6 distinct issues**: 4 defects and a set of missing tests.
- **Fixed:** all of them, with regression tests in `tests/unit/history/test_build_close1.py`, `tests/unit/history/test_closes.py` and `tests/unit/scheduler/test_close1_nightly.py`.
- **Checking the tests:** I reverted the not-public filter, the vendor-error handling and the forecast-target union in turn. Each revert makes its test fail: 4 failures across the three.
- **Gate:** ruff, format and mypy clean; pytest **627 passed**.

| # | Severity | Finding (merged duplicates) | Fix |
|---|---|---|---|
| PIT-01 (also DATA-2) | major | **`build` could store a session before its 17:15 ET public time.** `remy hist nightly --day <today>` or `remy hist build --end <today>`, run during a session, would store any provisional vendor row for today write-once, stamped public at 17:15. Settlement and F09 would then read it, and the official value would only be logged as a conflict. | `build` keeps only sessions whose public time has passed (`not_public` counts the rest), and stores only yields that are already public. Both CLI commands print the skipped count. |
| SCHED-01 (also DATA-1) | major | **The tick opened the HIST-1 store before its guards.** A store that couldn't be opened (wrong schema, missing triggers, damaged, locked) killed every tick, captures and backup included, with no failure record. | `StoreFirstCloses` takes a factory and opens the store on its first read, inside the tick's `settle` guard. A bad store now fails only settlement, which is recorded and notified. `HistStore` turns any `sqlite3.DatabaseError` into `HistError`. |
| SPEC-01 (also SPEC-05) | major | **A vendor error during a gap fill stopped store reads.** In F09, one failure marked the source broken, so stored closes counted as unavailable. In settlement, one failure aborted the loop, so trades with stored closes went unsettled. | `StoreFirstCloses` catches the vendor error and returns `None` for that gap, counted in `fetch_failed`. It stops asking the vendor and keeps reading the store. The report prints the count and the error, and the tick logs it. |
| DATA-3 | minor | **A catch-up started just before 08:00 could run into session hours** under the tick lock. | The tick passes a deadline (07:45 ET on the next session day) to `build`, which stops cleanly when it passes and leaves the rest to the next run. No attempt starts within 60 minutes of the deadline, because the events fetch (up to about 25 minutes on Fridays) can't be interrupted. A stop at the deadline counts as a reason to retry. |
| TESTS-2, 3, 5, 7 | minor | **Missing tests:** the forecast-target part of the nightly tracked set; the report's CLOSE-1 count line; how `build` fills `closes_absent`; and the live capture marking tracked-only names. | Tests added for each. |

## Refuted (no change)

- **SCHED-02:** the same no-deadline path as DATA-3, which is fixed.
- **SCHED-03 and SPEC-06:** retrying on any fetch error, including an empty chain, is the design. DR-0045 makes those retryable.
- **SPEC-02:** expired but unsettled positions drop out of the tracked set, but the base names always cover them.
- **SPEC-03:** a catch-up uses only the current day's tracked set. The base names mask this, as the finding itself says.
- **SPEC-04:** replay boards have no tracked-only hook. The shadow panel doesn't exist yet, so nothing is wrong today. It belongs with the shadow panel work.
- **TESTS-1, 4, 6:** coverage of CLI wiring that is correct as written.
