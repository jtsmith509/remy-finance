# R0 result: data and correctness (Stop Point B, pending live runs)

- **Stage:** R0 (handoff §5)
- **Date:** 2026-11-08
- **Branch:** `claude/cool-pascal-80kptn`

> **Status:** all R0 code is written and the offline gate is green. Three items in the R0 definition of done are **live runs on the Mac mini** that this build environment can't perform: a replay snapshot, a live prospective snapshot, and Polygon verification. The commands are listed under *Remaining before Stop Point B closes*. This memo will be updated with their results.

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

`ruff check`, `ruff format --check` and `mypy` (strict on `core` and `registry`) are all clean. **pytest: 247 passed.**

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
- **Move generator:** a synthetic SPY-sized board (22k quotes, 30 expirations) gives about 37.5k candidates in about 8 s. The 50-name estimate is roughly 300–350k candidates and about 70 s per snapshot.
- **Hashing:** a 22k-quote board hashes in about 0.5 s.
- Both will be re-measured on real boards in the live R0.4 run (`--movegen`).

## Known limitations
- **Live runs pending** (see below). The R0.3/R0.4/R0.5 adapters are tested only against mocked HTTP, so far.
- **Survivorship bias** remains in the 2026-chosen candidate list (DR-0004/0023).
- **Under-assignment near the money:** ASSIGN-1 uses the underlying mid. A leg in the money by less than half the underlying spread may be under-assigned (DR-0019).
- **Strike lattice:** DR-0020's lattice skips strikes between grid points, so SPY uses \$10 steps.
- **Prospective job timing:** a prospective board must be taken within 10 minutes of its scheduled time, or the slot becomes a gap (DR-0022).
- **Splits:** they're only known on their execution date (DR-0021). Adjusted roots are still excluded by the OSI rule.

## Decision records
**DR-0016 to DR-0023 were accepted by Josh on 2026-11-08**, including DR-0020's strike lattice as proposed. All 23 decision records are now accepted.

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

## Remaining before Stop Point B closes (on the Mac mini)
```bash
cd ~/remy-finance && git pull && uv sync
export POLYGON_API_KEY=...   # or read it from the keychain
uv run python scripts/verify_polygon.py                         # DR-0014 + EXP-1 close spot-check
uv run remy universe build --month 2026-11                      # DONE 2026-11-08 (see above)
uv run remy snapshot build --date 2026-11-06 --session open --movegen     # replay snapshot
# On a trading day, between 10:00 and 10:10 ET:
uv run remy snapshot take --session open --with-execution --movegen      # live prospective snapshot
uv run remy snapshot verify
```

## Remaining question for Josh
After the live runs, do the results satisfy you before R1 starts? That covers Polygon declaration-date coverage, close agreement, and the real snapshot sizes and move-generator timing.
