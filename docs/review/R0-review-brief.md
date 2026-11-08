# R0 code review brief

**For:** an independent reviewer, such as Codex, who hasn't seen how the code was written.
**Scope:** stage R0, on branch `claude/cool-pascal-80kptn`.
**Goal:** find correctness defects **before R1 builds on this code**. Style and taste comments are low priority.

## 1. What to read first (about 15 minutes)
1. `CLAUDE.md`: the ground rules. The ones that matter most here are **no look-ahead, ever** and **Decimal-only cash maths**.
2. `docs/handoff/HANDOFF_R0_R1.md` §4–§5: the data model, R0 tasks and required fixtures.
3. `docs/memos/R0-result.md`: what was built, the live-run results and the known limitations.
4. `docs/decisions/README.md`: 23 **accepted** decision records. See §5 below before reporting something that a decision record already settles.

## 2. How to run everything (offline, no network)
```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy            # strict on remy_finance.core and remy_finance.registry
uv run pytest -q       # about 257 tests; hypothesis property tests included
```
Re-run the property tests with different seeds: `uv run pytest tests/property --hypothesis-seed=<n>`.

## 3. Invariants to attack (most important first)
For each one, try to construct an input that breaks it. If you can, write a failing test.

1. **No look-ahead.** No code path may hand a model data that became available after the decision time `as_of`.
   - **Guards:** `data/view.py` (`PointInTimeView`, the only model-side read path) and `core/market.py` (`MarketState._check_board`).
   - **Where to probe:** the "same session" semantics in `data/fixture.py` and `data/thetadata.py`; the `option_volume` guard (session-date comparison); the `public_time` rules for rates (DR-0011), dividends and splits (DR-0021); `snapshots/builder.build_prospective`, which stamps `as_of` after the fetch; stock `at_time` rows stamped with the *request* time (`QUOTE_TIME_IS_REQUEST_TIME`).
   - **Also check:** whether a `LookaheadError` can be swallowed. Look at the per-read handlers in `snapshots/builder.py`, the thread pools in `builder.py` and `universe/selection.py`, and the `except Exception` blocks.
2. **Decimal only, no floats** anywhere cash or hashing is involved.
   - **Where to probe:** JSON parsing (`parse_float=Decimal`) in `data/thetadata.py` and `data/polygon.py`; Parquet encoding and decoding in `snapshots/store.py` (numbers are stored as strings); Decimal context-precision effects in divisions (spot mid, `SizingRule.quantity`, the universe average, `canonical_decimal`).
3. **Canonical serialization and hash stability** (`core/canonical.py`, `core/record.py`, DR-0016).
   - **Requirements:** equal values must hash equally, a change must change the hash, and nothing may depend on dict or set order or on the pydantic version.
   - **Note:** a golden hash is pinned in `tests/unit/core/test_records.py`.
4. **Payoff and capital at risk** (`core/payoff.py`, `core/structure.py`, DR-0018).
   - **Requirements:** max loss and max gain must be exact for any defined-risk structure, unbounded loss must always be rejected, and a `Structure`'s validator must recompute its numbers so they can't be inconsistent.
   - **Where to probe:** ratio legs; a short-put-only structure; prices of zero; a large strike range; a single-expiration check.
5. **Immutability.**
   - **Records:** frozen; `model_copy` revalidates.
   - **Snapshots:** write-once, with an atomic rename and read-only files (`snapshots/store.py`).
   - **Gaps:** a prospective slot with a gap can never be filled.
   - **Universe decisions:** write-once.
   - **Where to probe:** races between two concurrent writers, `os.rename` behaviour on macOS, and interrupted writes.
6. **Determinism.** The same board plus the same config must give byte-identical candidate lists and the same list hash (`movegen/generator.py`). Results must also not depend on thread count or completion order (`snapshots/builder.py`, `universe/selection.py`).
7. **Execution rules match their decision records.**
   - **ASSIGN-1** (`execution/assignment.py`; DR-0009, DR-0019): extrinsic value is taken from the bid, the threshold is strict, the dividend rule comes before the threshold rule, and out-of-the-money or at-the-money legs are never assigned.
   - **EXP-1** (`execution/settlement.py`; DR-0010): exercise when ≥ \$0.01 in the money, and the pin window is inclusive.
   - **FILL-1** (`execution/fill.py`; DR-0003): the full displayed size is required, fills are all-or-none, and fees are charged only on a fill.
8. **Time and calendar.**
   - **Where to probe:** `data/timeutil.py`, `snapshots/schedule.py`, DST transitions, half-days, the non-session refusal in `prospective_window`, and naive ET → UTC conversion.

## 4. Files by risk (review in this order)
| Priority | Files |
|---|---|
| Highest | `core/payoff.py`, `core/structure.py`, `data/view.py`, `core/market.py`, `snapshots/builder.py`, `snapshots/store.py` |
| High | `core/canonical.py`, `core/record.py`, `core/types.py`, `data/thetadata.py`, `data/polygon.py`, `execution/*.py`, `movegen/generator.py`, `universe/selection.py` |
| Medium | `core/instruments.py` (OSI), `data/fixture.py`, `data/timeutil.py`, `snapshots/schedule.py`, `movegen/config.py`, `cli.py` |
| Low | `scripts/verify_thetadata.py`, `scripts/verify_polygon.py` |

## 5. Deliberate decisions (not defects unless the code doesn't do what the record says)
- `PointInTimeView` **raises** on future data instead of filtering it (DR-0021).
- `max_loss` and `max_gain` exclude fees; capital at risk = max loss + fees (DR-0018).
- ASSIGN-1 uses the underlying **mid** as S; a missing option quote means it's assumed assigned (DR-0019).
- The move generator uses a **coarse strike lattice**, with iron-condor short strikes out of the money (DR-0020).
- Splits become public on their execution date at 09:30 ET, and dividends on their declaration date at 16:00 ET (DR-0014, DR-0021).
- A prospective snapshot must land within 10 minutes of its scheduled time, or the slot is a gap; there is never a backfill (DR-0022).
- Quotes older than 5 minutes are flagged, not excluded (DR-0015).
- **No brokerage code** (ground rule 5). That scope is intentional.

If you think one of these decisions is *wrong*, rather than incorrectly implemented, report it separately as a design concern.

## 6. Bugs already found (hints about where the tests were weak)
1. `Decimal.normalize()` rounded to 28 digits inside canonical serialization. Found by hypothesis.
2. The R0.1 staleness check compared clock time without the date.
3. Polygon pagination raised when a result hit the page limit exactly.
4. The CLI's `--symbols` was still required after the universe default was added.
5. **The snapshot builder discarded a symbol's quotes when its dividend read failed**, which produced empty boards in the first live run.
6. `snapshot take` fetched a live board before checking that today was a trading session.

Bugs 4–6 were found only by running live, so look for similar gaps between the unit tests and the real wiring: the CLI, adapters, error paths and concurrency.

## 7. How to report
Report one finding per item, most severe first:

| Field | Content |
|---|---|
| Severity | **blocker** (look-ahead, wrong cash or payoff, broken immutability), **major**, **minor** or **nit** |
| Location | `path:line` |
| Failure scenario | the concrete input or state, and the wrong result |
| Evidence | a failing test (preferred) or a precise reproduction |
| Suggested fix | optional |

Put the findings in `docs/review/R0-findings.md` on a **separate branch**, or paste them back to Josh. **Don't push fixes to `claude/cool-pascal-80kptn`.** Fixes come back through the normal process, each with a regression test.
