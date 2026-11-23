# MKT-1/RAND-1 v4 and the cut-over switch (DR-0048): findings and resolution

- **Reviewer:** an internal multi-agent review, not Codex. Four lenses (v3 unchanged, the DR-0041/DR-0042 rules, the cut-over switch, test adequacy), then a skeptic on each candidate, trying to refute it.
- **Reviewed:** `146da54`.
- **Result:** 9 candidates, of which **7 survived** and 2 were refuted. The 7 come down to **3 defects and 3 missing tests**. The v3 lens found no change to v3's behaviour.
- **Fixed:** all of them, with regression tests in `tests/unit/scheduler/test_cutover.py` and `tests/unit/baselines/test_v4.py`.
- **Checking the tests:** I reverted the three fixes, and their 3 tests fail.
- **Gate:** ruff, format and mypy clean; pytest **681 passed**.

| # | Severity | Finding (merged duplicates) | Fix |
|---|---|---|---|
| RULES-01 (also CUTOVER-03) | major | **A stored run log from the other version was reused.** Run logs are found by engine name, so `replay run --baselines v4` over days a v3 replay had covered silently reused the v3 logs, recorded v3 statuses and reported success. | A stored log whose engine version differs from the slot's version raises `VersionMismatchError`. In the live scheduler that's a recorded `failed` status; in a replay, it stops the replay. A stored log is never silently reused across versions. |
| CUTOVER-02 | major | **A replay registry could hold v3 and v4 records together.** For example, a replay started with one `--baselines` value and resumed with the other. | `ReplayDriver` refuses to start when the replay registry holds MKT-1/RAND-1 recommendations or statuses of another version: "archive it first". `remy replay archive` moves the run logs too. |
| CUTOVER-01 | minor | **v3 picks counted against v4's per-day cap.** The one-per-underlying-per-day cap counted all of an engine's picks, so with a CLOSE cut-over, v3's OPEN picks capped v4 at the CLOSE. | The cap counts only the same engine version's picks. v3 is unchanged, since every v3 pick is version 3. |
| TESTS-1, 3, 5 | minor | **Missing tests:** the engine version on statuses written without a run log after the cut-over; the bug-signal check on the mean, not the ADJ-1 key; and the CLI's v3 default and its flag check. | Tests added for each. |

## Refuted (no change)

- **TESTS-2:** the `<= 0` boundary in `can_only_lose` is correct as written. An exact-zero best return is rare enough that pinning it needs a constructed candidate.
- **TESTS-4:** `CANNOT_WIN_EXCLUDED:0` is always emitted for v4 by construction.
