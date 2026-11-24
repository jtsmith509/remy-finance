# VRP estimator (DR-0049): findings and resolution (internal adversarial review)

- **Reviewer:** an internal multi-agent review, not Codex. Four lenses (point-in-time hygiene, statistics, conformance to DR-0049/P9/B02/EVAL-2, test adequacy), then a skeptic on each candidate, trying to refute it.
- **Reviewed:** `85ef4c3`.
- **Result:** 21 candidates, of which **15 survived** and 6 were refuted. The 15 come down to **5 issues and a set of missing tests**.
- **No look-ahead was found.** The point-in-time lens's only surviving finding was about the inputs hash.
- **Fixed:** all of them, with regression tests in `tests/unit/vrp/test_vrp.py` (18 tests).
- **Checking the tests:** I reverted the 3-year pool rule and the shrunk line's clipping share together, and 2 tests fail.
- **Gate:** ruff, format and mypy clean; pytest **699 passed**.

| # | Severity | Finding (merged duplicates) | Fix |
|---|---|---|---|
| PIT-01 (also STATS-2, SPEC-1) | minor | **The inputs hash covered only the admitted rows.** It left out the sector mapping, the sector pools and the method constants, so two refits that applied different coefficients carried the same hash. | The hash now covers the method string with its constants, the cutoff, the symbols, the sector mapping, each sector's pool and every admitted row. The refit stores each sector's members and the names left out for short history. |
| SPEC-3 | minor | **The sector pool admitted every mapped name by default.** DR-0049 pools only names with DR-0044's 3 years. | `fit_refit` applies the rule itself, as of the cutoff: a name's first stored close must be at least 3 years old. The `history_ok` argument is gone. |
| STATS-1 (also SPEC-6) | minor | **A shrunk name's stored diagnostics described its own fit.** The clipping share and standard errors attached to the applied line were the name's own-fit values. | A shrunk line carries its own clipping share on the name's rows, and no SEs (they belong to the fits it mixes). Each refit also reports one clipping share over every name's applied line. |
| SPEC-4 | minor | **A name missing from the sector mapping was treated like SPY,** unshrunk and with no reason given. | Only SPY and the sector SPDRs use their own fit. An unmapped name has no ratio, with the reason "not in the sector mapping". A mapped name with no sector fit uses its own fit unshrunk, and says so. |
| STATS-4 (also SPEC-7) | minor | **The minimum for a usable fit,** 12 effective windows, isn't in DR-0049. | Named in the method string (and so in the hash). Josh confirms or changes it; see below. |
| TESTS-1, 2, 6–9 | minor | **Missing tests:** Newey-West against an independent reference (Bartlett weights, lag 21, per-date summing); the exact 21-session window and divisor; the lower clip bound; shrinking b; dropping rows with no SPYIV30; and the inputs hash. | Tests added for each, along with latest-refit selection and the 3-year window edge. |

## Refuted (no change)

- **STATS-3, TESTS-3:** the `history_ok` default. Superseded: the rule now lives inside `fit_refit`.
- **SPEC-2:** a cutoff later than the data in the store. Nothing stores or applies refits yet. MKTV-1's integration will check data coverage.
- **SPEC-5:** the fallback order is as DR-0049 specifies.
- **TESTS-4, TESTS-5:** coverage gaps, closed anyway by the new tests.

## For Josh (DR-0049 details the code fixes)

1. **The minimum for a usable fit:** 12 effective windows, about a year of rows. It applies to name fits and to pooled sector fits.
2. **A mapped name whose sector has no fit:** it uses its own fit unshrunk, flagged. The alternative is no ratio.
3. **"3 years" for the sector pool:** the name's first stored close is at least 3 years before the cutoff.
