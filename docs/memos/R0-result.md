# R0 result: data and correctness (Stop Point B: ready for sign-off)

- **Stage:** R0 (handoff §5)
- **Date:** 2026-11-08, with the live runs completed 2026-11-09
- **Branch:** `claude/cool-pascal-80kptn`

> **Status:** every item in the R0 definition of done is met.
> - **Tests:** all pass (**309**, offline), with ruff, format and mypy clean; CI runs the same gate on every push.
> - **Look-ahead:** the guard is tested to raise on every read path.
> - **Snapshots:** a replay snapshot was built for a past date (with dividends), and a **live prospective snapshot** was taken on 2026-11-09.
> - **Determinism:** candidate generation is deterministic.
> - **Decision records:** all are written and accepted (DR-0001 to DR-0027).
> - **Independent review:** an independent review (Codex) found 8 defects, and all are fixed with regression tests.
>
> **R1 has not started.** It waits for Josh's confirmation.

## What was built

| Task | Module | Summary |
|---|---|---|
| R0.1 | `scripts/verify_thetadata.py` | Data verification; closed at Stop Point A ([memo](R0.1-data-verification.md)). |
| R0.2 | `core/` | Frozen, Decimal-only records with canonical JSON and SHA-256 hashes (DR-0016). `MarketState` refuses any observation that becomes available after `as_of` (DR-0017). |
| R0.3 | `data/` | `MarketDataSource` protocol; `PointInTimeView`, the only model-side read path, which raises `LookaheadError`; `FixtureSource` (offline); `ThetaDataSource` (R0.1-verified endpoints); `PolygonSource` (dividends and splits, DR-0014); `CompositeSource` (DR-0021). |
| R0.4 | `snapshots/` | Snapshot schedule (DST- and half-day-aware), replay and prospective builders, QAGE-1 flags, and a write-once, content-addressed Parquet + manifest store, verified on load. Gap records block backfill. CLI `remy snapshot build\|take\|verify` (DR-0022). |
| R0.5 | `universe/` | UNIV-1 monthly selection from the 80-name `UNIV-CANDIDATES-1`, by 20-session option volume read through the view. Write-once decisions with an inputs hash. CLI `remy universe build`. New `OptionVolume` read path (DR-0023). |
| R0.6 | `core/payoff.py`, `core/structure.py`, `core/fees.py` | Piecewise-linear payoff; max loss and gain; capital at risk; FEES-1. A `Structure` recomputes its own risk numbers, so an undefined-risk or inconsistent structure can't be built (DR-0018). |
| R0.7 | `movegen/` | MOVEGEN-1 enumeration (longs, verticals, condors, butterflies, null move) on a strike lattice (DR-0020); UNPRICEABLE candidates kept with reasons; SIZE-1 sizing; deterministic order and list hash. |
| R0.8 | `execution/fill.py` | FILL-1 cost model: cross the spread, full displayed size per leg, all-or-none, fees only on a fill; stock closes for assignments. |
| R0.9 | `execution/assignment.py` | ASSIGN-1: dividend rule, then the strict \$0.05 threshold; extrinsic from the bid; position and cash consequences (DR-0019). |
| R0.10 | `execution/settlement.py` | EXP-1: exercise-by-exception at ≥ \$0.01 in the money, settlement at intrinsic, PIN_RISK_APPROXIMATE within \$0.25. |

## Test results (offline, no network)

`ruff check`, `ruff format --check` and `mypy` (strict on `core` and `registry`) are all clean. **pytest: 309 passed** at sign-off. The table below is the breakdown from the first R0 pass, at 247 tests. Since then, the review regression tests (`tests/unit/review`), the Massive adapter and check, the CLI integration tests and the fixture-week test (`tests/integration`) have been added. CI runs the same gate on every push.

| Area | Tests | Highlights |
|---|---|---|
| `core` | 132 | Round trip for every record type; a pinned golden hash; immutability; Decimal-only fields (including a check that no record declares a float); OSI validation; every R0.6 handoff fixture **exactly**, including rejection of the naked short call. |
| `execution` | 34 | Every R0.9 fixture; R1.2 fill fixtures (5 vs ask size 3; condor with one short leg; execution quote used); EXP-1 thresholds and pin window. |
| `movegen` | 17 | Hand-computed counts per structure type (138 on the test board); bounds; unpriceable kept; sizing; byte-identical output and hash when input order changes. |
| `data` | 24 | Adapters against mocked HTTP. **The dedicated look-ahead module** requests future data through every public read path and asserts `LookaheadError`; a meta-test fails if a read path is added without a test. |
| `snapshots` | 14 | DST and half-day times; flags; gap notes; look-ahead propagation; store round trip with an identical ID; write-once; tamper detection; execution linkage; the gap/backfill rule. |
| `universe` | 6 | Only prior-session data is used (future data changes nothing); names that later delist are kept; a leaky source raises; deterministic; write-once. |
| property (hypothesis) | 11 | P&L ≥ −capital at risk for random defined-risk structures, and that bound is attained; call and put butterflies identical; call/put verticals differ by a constant; canonical Decimal idempotence; hash order and timezone independence. Repeated under 5 seeds. |
| scripts | 9 | Verification-script logic against a mocked Terminal. |

**Defects found by the tests and fixed:**
- `Decimal.normalize()` rounds to 28 digits, which would have changed canonical values (DR-0016).
- The original staleness check compared clock time without the date (R0.1).
- The Polygon pagination loop raised on an exact page limit.

## Measured performance
- **Move generator:** a synthetic SPY-sized board (22k quotes, 30 expirations) gives about 37.5k candidates in about 8 s. **On the real 50-name board: 260,806 candidates in 18.2 s** (see *Live runs*).
- **Hashing:** a 22k-quote board hashes in about 0.5 s.
- **Live boards:** a 50-name board is about 160k quotes.
  - **Replay:** built in 38–46 s.
  - **Live prospective:** stored about 18–20 s after the scheduled time.
  - **Massive prefetch:** 4 requests (38 s).
- **The move generator slowed after DR-0025:** 18.2 s on Sunday's board, and **56–71 s** since the exact-arithmetic change. That doesn't affect the window, because boards are stored before generation. It's an R1 optimization item.

## Known limitations
- **Survivorship bias** remains in the 2026-chosen candidate list (DR-0004/0023).
- **Under-assignment near the money:** ASSIGN-1 uses the underlying mid. A leg in the money by less than half the underlying spread may be under-assigned (DR-0019).
- **Strike lattice:** DR-0020's lattice skips strikes between grid points, so SPY uses \$10 steps.
- **Prospective job timing:** a prospective board must be taken within 10 minutes of its scheduled time, or the slot becomes a gap (DR-0022).
- **Splits:** they're only known on their execution date (DR-0021). Adjusted roots are still excluded by the OSI rule.
- **ETF dividends:** schedule-declared distributions (e.g. SPY) only become public on their ex-date (DR-0027). So the ASSIGN-1 dividend rule can't fire for such ETFs; the extrinsic-value threshold rule still applies.
- **Snapshot boards skip ticker-change events.** There's no batched Massive endpoint for them; the data check covers them.
- **Theta Terminal network exposure:** the Terminal listens on all interfaces. The macOS firewall now blocks other devices (verified), and this must be re-checked after Java upgrades (R0.1 memo).

## Decision records
**All 27 decision records are accepted** (DR-0001 to DR-0027, 2026-11-08):
- **DR-0016 to DR-0023:** R0 design.
- **DR-0024 and DR-0025:** fixes from the independent review.
- **DR-0026 and DR-0027:** the per-type data sources, and SPY-style schedule-declared dividends.

None are awaiting confirmation.

## Live runs on the Mac mini

**`remy universe build --month 2026-11`** (2026-11-08):
- **Result:** `UNIV-1:2026-11-02` selected **50 members in 278 s** with 4 workers, from a window of 2026-10-05 to 2026-10-30. All 80 candidates had **20 of 20 sessions** of volume data, so no name was ineligible. Inputs hash `b00e32214305fdaf…`.
- **Top of the ranking (average daily contracts):** SPY 12.13M, QQQ 8.49M, NVDA 2.66M, TSLA 2.09M, IWM 1.97M, AAPL 1.27M, MU 1.13M.
- **Cut-off:** #50 FXI (58.7k) against #51 QCOM (57.4k). Also just outside: KO, JPM, WFC, DIA, PYPL, LLY.
- **Members:** SPY, QQQ, NVDA, TSLA, IWM, AAPL, MU, INTC, AMZN, TLT, META, AMD, GOOGL, MSFT, HYG, PLTR, SOFI, NFLX, MSTR, ORCL, AVGO, GLD, HOOD, SLV, SMH, XLF, SMCI, BAC, XLE, WMT, TSM, COIN, GDX, BA, BABA, RIVN, EEM, XLU, UBER, T, XLP, VZ, PFE, UNH, KRE, F, ARM, C, XOM, FXI.
- **Observation:** XLB, XLC, DE and MA trade only 3–9k contracts a day. That's a candidate-list revision to consider later (`UNIV-CANDIDATES-2`). It needs no action now.

**First `remy snapshot build --date 2026-11-06 --session open --no-events`** (2026-11-08): **defective; removed and re-run.**
- **Symptom:** both boards stored with **0 option quotes** and 50 notes.
- **Cause:** a builder bug. Each symbol's four reads (chain, underlying, dividends, corporate actions) shared one error handler. With `--no-events`, the dividend read raises by design, so the quotes already fetched for that symbol were discarded.
- **Fix:** each read is now guarded separately. A failed read becomes a note naming it (e.g. `DIVIDENDS:UnsupportedDataError`), and the other reads are kept. Regression tests cover failed dividend and underlying reads.
- **Disposal:** the two defective **replay** boards (`data/snapshots/replay/2026-11-06/`) were deleted by hand so the slot could be rebuilt. This is a deliberate, recorded exception to write-once, allowed only because they were replay data produced by a bug. A prospective board would never be deleted; it would get a linked correction instead.
- **Timing seen:** the 50-symbol execution board was fetched in **37.5 s** with 4 workers.

**Second `remy snapshot build --date 2026-11-06 --session open --no-events --movegen`** (2026-11-08): **OK.**
- **Boards stored:** DECISION `51afcc79e22154bf` (10:00 ET) and EXECUTION `72cb26646ad3e2db` (10:05 ET), each for 50 symbols from universe `UNIV-1:2026-11-02`.
- **Notes:** each symbol carries exactly two, `DIVIDENDS:UnsupportedDataError` and `CORPORATE_ACTIONS:UnsupportedDataError`, as expected without Polygon.
- **Move generator on the real decision board:** **260,806 candidates in 18.2 s**, with list hash `5c76914d72ee3f08…`.
  - **Breakdown:** 231,928 priced, 28,877 unpriceable (about 11%: a leg without a two-sided quote, each kept with a reason), and 21,105 priced but quantity 0 under SIZE-1.
  - **By type:** iron condors 117,811; debit verticals 43,566; credit verticals 43,566; call butterflies 18,947; put butterflies 18,947; long calls 8,984; long puts 8,984; the null move 1.
  - **Against the estimates:** this is consistent with DR-0020's estimate of 300–350k. It's about 4× faster than the synthetic-board estimate of about 70 s.

**`remy snapshot verify`** (2026-11-08): `verified 2 snapshots, 0 failed; gaps recorded: 0`.

**First `remy snapshot take`** (Sunday 2026-11-08): **correctly refused**, because it's not a trading session. It did fetch a live board before the store refused it, though. Fixed: `take` now checks the trading-day window before fetching anything, and `--wait` lets it start early and wait for 10:00 ET. The live run is scheduled for Monday 2026-11-09.

## Independent review (Codex, 2026-11-08)
Codex reviewed commit `aeda0c2` against `docs/review/R0-review-brief.md` and found **8 defects: 4 blockers, 3 major and 1 minor**. All 8 were verified, fixed with regression tests (each shown to fail on `aeda0c2`) and pushed. Details are in [`docs/review/R0-findings.md`](../review/R0-findings.md).
- **Blockers fixed:**
  - F01: per-slot finality is now atomic.
  - F02: live `LookaheadError` is no longer swallowed.
  - F03: floats are rejected at factory inputs.
  - F04: payoff arithmetic is exact at any price.
- **Major and minor fixes:**
  - F05: missed captures now become gaps.
  - F06: event reads are independent.
  - F07: universe writes are atomic.
  - F08: `OptionVolume` fixtures can be reloaded.
- **New decision records:** DR-0024 (slot finality, amending DR-0022) and DR-0025 (exact Decimal arithmetic), **both accepted by Josh on 2026-11-08**.
- **Gate after the fixes:** 289 tests pass; ruff, format and mypy are clean.

## End-to-end test and CI (2026-11-08)
- **Fixture-week integration test** (`tests/integration/test_fixture_week.py`, brief §7): one underlying over a Monday–Friday week ending on its expiration, offline, through the real code. It:
  - builds and stores 20 replay boards (decision plus execution for each session);
  - runs the move generator;
  - opens two positions, a debit spread and a credit spread, through a labelled placeholder for R1's selection step;
  - fills them under FILL-1 on the 10:05 board;
  - runs ASSIGN-1 at every later snapshot. The short 90 call is assumed assigned at Wednesday's open, the day before the ex-date, and force-closed at Wednesday's close, so no dividend is owed;
  - settles the debit spread under EXP-1 at Friday's close.

  Both positions reconcile to the cent. Two runs are byte-identical, and every stored board verifies and holds nothing stamped after its `as_of`.
- **CI:** GitHub Actions (`.github/workflows/ci.yml`) runs the offline gate (ruff, format, mypy, pytest) on every push and pull request, with no secrets. Its first run passed.
- **Gate:** 307 tests pass.

**Replay with dividends: `remy snapshot build --date 2026-11-05 --session open --movegen`** (2026-11-09): **OK.**
- **Boards:** DECISION `5de474d665b8f6b8`, 160,808 quotes, **0 quality notes**, built in 46.1 s. EXECUTION `b831d66bea0c6071`, 160,808 quotes, 0 notes, built in 38.4 s.
- **Massive:** dividends and splits for all 50 symbols came from the batched prefetch, with no per-symbol data errors.
- **Move generator:** 238,717 candidates (213,612 priced, 25,104 unpriceable, 17,988 quantity 0), list hash `06172c89d1da9885…`, in **56.0 s**.
- **Performance note:** 56 s is about 3× Sunday's 18.2 s on a comparable board. That's attributable to the exact-arithmetic contexts added for review finding F04 (DR-0025). It doesn't affect the prospective window, because the decision board is stored before candidates are generated. Profiling and optimizing it is listed for R1.

**Live prospective snapshot: `remy snapshot take --session open --with-execution --movegen --wait`** (Monday 2026-11-09): **OK.** [Log](evidence/R0-live-take-20261109.log).
- **Massive prefetch:** 50 symbols in **4 requests (38 s)**, done before the wait.
- **DECISION** `c8476d2c96ee7d4b`: as_of **10:00:18 ET**, 160,468 quotes, **0 notes**.
- **EXECUTION** `cf5e38b148e64f15`: as_of **10:05:20 ET**, 160,468 quotes, 0 notes, linked to the decision board.
- **Timing:** both landed about 20 s after their scheduled times, well inside the 10-minute window (DR-0022), and no gap was recorded.
- **Move generator:** 245,490 candidates (226,052 priced, 19,437 unpriceable, 19,387 quantity 0) in 70.9 s, with list hash `fab52c21b72fba66…`. This ran after the decision board was stored.

## Stop Point B: questions for Josh
1. **Sign-off:** does R0 meet the definition of done, so that R1 can start? This covers the live snapshot, the replay with dividends, the independent-review fixes and 309 green tests.
2. **Spec §11 versus the brief (still open):** spec §11 requires a recorded **power estimate** and pre-registered scoring rules *before prospective paper trading begins*, which is R1's 5-day live run. The brief's R1 tasks don't list them. Should they be drafted as documents during R1, before the live run, or deferred?
3. **Move-generator speed** (56–71 s after DR-0025): optimize it early in R1, or leave it, since it doesn't affect the snapshot window?

