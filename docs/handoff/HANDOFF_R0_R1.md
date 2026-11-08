# REMY Finance — Claude Code handoff: stages R0 and R1

**Owner:** Josh Smith · **Repo:** `github.com/jtsmith509/remy-finance` (private) · **Brief date:** November 7, 2026
**Governing spec:** `docs/spec/spec_v0.5.md` (accepted-changes copy of *REMY Financial Technical Specification v0.5*). Where this brief and the spec disagree, stop and ask; do not resolve it yourself.

---

## 0. Ground rules for this build

1. **Scope is R0 and R1 only.** Do not build R2–R6: no factor models, no causal search, no LSM policy optimizer, no neural or hybrid models, no volatility-risk-premium estimation. Leave clean seams for them; do not stub them out in detail.
2. **Stage process, same as the REMY chess engine.**
   - Every material design choice gets a decision record in `docs/decisions/` (`DR-0001-short-title.md`: context, options considered, decision, consequences, status = *proposed* until Josh confirms).
   - Each stage ends with a short result memo in `docs/memos/` (what was built, test results, known limitations, open questions).
   - **Stop points** (Section 9) are hard stops. Summarize, then wait for Josh's explicit confirmation.
3. **Correctness before breadth.** Payoff math, point-in-time hygiene, and registry immutability must be tested before anything that depends on them.
4. **No look-ahead, ever.** Any code path that can read data stamped after the decision time is a defect, not an approximation.
5. **No brokerage code.** No order routing, no broker SDKs. "Orders" are paper records only.
6. **Secrets** (ThetaData credentials, any API keys) live in environment variables or the macOS keychain, never in the repo. Add a `.env.example` with names only.

---

## 1. Settled decisions (do not reopen without a decision record)

| Area | Decision |
|---|---|
| Language | Python for everything. Rust only later, where profiling proves a bottleneck. |
| Host | Josh's Mac mini; scheduled jobs via `launchd`. |
| Data | ThetaData Options Standard (personal-use license) is the provisional primary source, behind an adapter interface. Verified in R0.1 before anything else depends on it. |
| Universe | About 50 liquid US stocks and ETFs, selected **point-in-time**. |
| Snapshots | Two per trading day: one after the open (once quotes settle) and one shortly before the close. |
| Strategies | All **defined-risk** structures: long calls and puts, vertical debit and credit spreads, iron condors, butterflies, and any multi-leg structure whose maximum loss is bounded and computable at entry. No undefined-risk structures. |
| Early assignment | Conservative rule (Section 5, R0.9). |
| Paper fill rule | Cross the spread per leg, only if displayed size covers the full quantity; a multi-leg order fills only if every leg fills (Section 6, R1.2). |
| R1 baselines | Market-implied baseline plus a seeded random baseline. |
| Process | Decision records, stage memos, explicit owner confirmation at stop points. |

---

## 2. Proposed defaults (each needs a decision record; Josh confirms or changes)

| # | Topic | Proposed default |
|---|---|---|
| D1 | Tooling | Python 3.12, `uv`, `pytest` + `hypothesis`, `ruff`, `mypy` (strict on `core/` and `registry/`), `pydantic` v2 frozen models, `polars` + `pyarrow` (Parquet), `exchange_calendars`, `typer` CLI. |
| D2 | Snapshot times | Open snapshot **10:00 ET**, close snapshot **15:45 ET** (US/Eastern, DST-aware). Half-days: close snapshot at 12:45 ET. |
| D3 | Execution latency | Recommendations are frozen at the snapshot. Paper fills use a separate **execution quote** taken **5 minutes later** (10:05 / 15:50 ET). In replay, the same offsets apply to historical quotes. |
| D4 | Universe rule | Each month, rank a configured candidate list (≈80 names) by trailing 20-trading-day average option volume using only data available at selection time; take the top 50. Membership is stored per date and never rewritten. |
| D5 | Fees | $0.65 per contract per side, $0 exercise/assignment fee, configurable and versioned (`FEES-1`). |
| D6 | Sizing | Paper budget of $2,000 maximum capital at risk per recommendation; quantity = floor(budget / per-unit capital at risk). If quantity is 0, the candidate is not recommendable (logged, not silently dropped). |
| D7 | Selections per snapshot | Each baseline issues its top **K = 5** recommendations per snapshot (subject to a cap of one recommendation per underlying per baseline per day). |
| D8 | Candidate bounds | Expirations 7–60 calendar days out; strikes within ±25% of the underlying; spread widths from a configured list per underlying price band. All bounds versioned (`MOVEGEN-1`). |
| D9 | Assignment threshold | Extrinsic-value threshold **$0.05 per share**, strict less-than (`ASSIGN-1`). |
| D10 | Expiration handling | Baseline policy `HOLD-1`: hold to expiration, settled under `EXP-1` (Section 5, R0.10). |
| D11 | Risk-free rate | Daily rate from ThetaData's interest-rate data if available; otherwise a public Treasury-bill series, stored as point-in-time observations. |
| D12 | Registry storage | SQLite (WAL) with append-only triggers and a hash chain; nightly JSONL export committed to the repo for an external timestamp (Section 6, R1.1). Market snapshots are **not** committed to git; they live under `data/` with manifests, backed up separately. |

---

## 3. Repository layout

```
remy-finance/
├── CLAUDE.md                     # short: points to this brief and the spec; ground rules
├── README.md
├── pyproject.toml
├── .env.example
├── docs/
│   ├── spec/spec_v0.5.md
│   ├── handoff/HANDOFF_R0_R1.md  # this file
│   ├── decisions/                # DR-xxxx records
│   └── memos/                    # stage result memos
├── src/remy_finance/
│   ├── core/          # contracts, legs, structures, payoff, capital at risk, fees
│   ├── data/          # adapter protocol, ThetaData adapter, fixture adapter, point-in-time view
│   ├── snapshots/     # MarketState builder, immutable store, manifests, gap log
│   ├── universe/      # point-in-time universe selection
│   ├── movegen/       # deterministic candidate generation (+ null move)
│   ├── execution/     # fill rule, cost model, assignment rule, expiration settlement
│   ├── baselines/     # market-implied baseline, random baseline
│   ├── registry/      # recommendation records, journal, immutability, hash chain, export
│   ├── scheduler/     # job entry points, launchd plist templates, trading calendar
│   ├── reports/       # per-trade outcomes, daily and cumulative summaries
│   └── cli.py
├── tests/
│   ├── unit/  ├── property/  ├── integration/  └── fixtures/
└── data/              # git-ignored: snapshots, registry DB, logs
```

---

## 4. Core data model (R0)

All records are immutable (`frozen=True`), serialize to **canonical JSON** (sorted keys, fixed decimal handling), and carry a SHA-256 content hash. Prices are `Decimal`, never float, in anything that touches cash flows.

**Time.** Every observation carries up to four timestamps, all timezone-aware UTC:
`event_time` (when it happened) · `public_time` (when it became public) · `ingest_time` (when we received it) · and, on use, the `as_of` of the decision that consumed it.

| Type | Key fields |
|---|---|
| `OptionContract` | OSI symbol, underlying, expiration, strike, right (C/P), style (`AMERICAN`), multiplier (100), standard-deliverable flag (non-standard deliverables are **excluded** from the universe) |
| `OptionQuote` | contract, bid, ask, bid_size, ask_size (contracts), quote_time, source, quality flags |
| `UnderlyingQuote` | symbol, bid, ask, last, sizes, quote_time |
| `DividendEvent` | symbol, ex_date, amount, declaration date / public_time |
| `CorporateAction` | symbol, type, effective date, public_time, details |
| `RatePoint` | tenor, rate, observation date, public_time |
| `MarketState` (the board) | snapshot_id (= content hash), session (`OPEN`/`CLOSE`), decision `as_of`, mode (`PROSPECTIVE`/`REPLAY`), universe version, underlying quotes, full option chains for the universe, known dividends/corporate actions as of `as_of`, rates, data-source provenance, gap/quality flags. **No forecasts, no scores.** |
| `Leg` | contract, side (+1 long / −1 short), ratio (whole contracts) |
| `Structure` | ordered legs, structure type label, per-unit capital at risk, max loss, max gain (if bounded) |
| `Candidate` | structure, quantity, entry rule, exit policy id, priceability status (`PRICED` / `UNPRICEABLE` with reason) |

---

## 5. Stage R0 — data and correctness

### R0.1 Data-source verification spike — **STOP POINT A after this task**
Write `scripts/verify_thetadata.py` that, for 3 underlyings (one ETF, one megacap, one name with a dividend in the test window), reports pass/fail with evidence on:
1. **Bid/ask sizes** present on historical and live option quotes.
2. **Underlying prices** available at the same timestamps (from the options subscription or a separate tier).
3. **Dividends and corporate actions** with enough timing information to determine `public_time` (if absent, recommend a supplementary source).
4. **Time-of-day historical retrieval**: fetching the full chain's NBBO at a given clock time (e.g., 10:00 and 15:45 ET) across a date range, without downloading all ticks.
5. **Expired contracts**: chains and quotes for contracts that have since expired.
Also record: whether the local Theta Terminal must run on the Mac mini, rate limits observed, and approximate storage per snapshot for 50 underlyings. Write `docs/memos/R0.1-data-verification.md` and **stop**.

### R0.2 Core types
Implement Section 4 types with canonical serialization and hashing. Tests: round-trip serialization, hash stability, immutability, Decimal-only cash fields.

### R0.3 Data adapter and point-in-time view
- `MarketDataSource` protocol: `list_contracts(underlying, as_of)`, `chain_quotes_at(underlying, ts)`, `underlying_quote_at(symbol, ts)`, `dividends(symbol, known_as_of)`, `corporate_actions(symbol, known_as_of)`, `rates(known_as_of)`.
- Implementations: `ThetaDataSource`, and `FixtureSource` (offline, file-based; all tests run against it, no network in the test suite).
- `PointInTimeView(source, as_of)`: the **only** way models, movegen, and baselines read data. It raises `LookaheadError` for any observation whose `public_time` (or `quote_time` for quotes) is after `as_of`.

### R0.4 Snapshot store
- `snapshot take --session open|close` (prospective) and `snapshot build --date D --session S` (replay). Replay snapshots are labeled `mode=REPLAY` and stored separately.
- Write-once, content-addressed Parquet plus a JSON manifest; refuse to overwrite.
- A missed prospective snapshot writes a **gap record**; it is never backfilled into the prospective record.

### R0.5 Point-in-time universe
Implement D4. Store each membership decision with its inputs' hashes. Test: building the universe for a past date uses no data after that date (run through `PointInTimeView`) and includes names that later delisted or fell out.

### R0.6 Payoff, capital at risk, and fees
- Terminal payoff per share: long call `max(S−K, 0)`, long put `max(K−S, 0)`; short legs negate. Multiply by multiplier and quantity.
- Structure payoff is piecewise linear in `S_T`. Compute **max loss** by evaluating at `S_T = 0`, at every strike, and checking slopes beyond the highest strike. Any structure whose loss is unbounded (negative slope as `S_T → ∞`) is **rejected** by construction.
- Per-unit capital at risk = max loss including entry fees (and, conservatively, exit fees if the exit policy requires a closing trade).
- **Required fixtures** (multiplier 100; fees $0.65/contract/side; exercise fee $0; prices are the fill prices under the spread-crossing rule):

| Case | Legs (fill price) | Capital at risk | Check |
|---|---|---|---|
| Long call | +1 100C @ 2.50 ask | 250.65 | S_T=110 → P&L **+749.35**; S_T=95 → **−250.65** |
| Bull call debit spread | +1 100C @ 2.50 ask, −1 105C @ 1.00 bid | 151.30 | S_T≥105 → **+348.70**; S_T=102 → **+48.70** |
| Bull put credit spread | −1 95P @ 1.20 bid, +1 90P @ 0.40 ask | 421.30 | S_T=92 → **−221.30**; S_T≥95 → **+78.70** |
| Iron condor | −1 95P @1.20, +1 90P @0.40, −1 105C @1.00, +1 110C @0.35 | 357.60 | S_T=100 → **+142.40**; S_T=112 → **−357.60** |
| Long call butterfly | +1 95C @6.20, −2 100C @2.40, +1 105C @0.70 | 212.60 | S_T=100 → **+287.40**; S_T=90 → **−212.60** |
| Naked short call | −1 100C | — | **Rejected** (unbounded loss) |

- Property tests (`hypothesis`): for random defined-risk structures, realized P&L at any `S_T` is ≥ −capital at risk; put–call structures with identical payoff diagrams produce identical payoffs.

### R0.7 Deterministic move generator
- From a `MarketState` and `MOVEGEN-1` config, enumerate long calls/puts, vertical debit and credit spreads, iron condors, and long butterflies (calls and puts) within D8 bounds; always include the **null move**.
- A candidate with any leg lacking a two-sided quote is kept with status `UNPRICEABLE` and a reason, not silently dropped.
- Same snapshot + same config ⇒ byte-identical ordered candidate list and identical list hash (test it).

### R0.8 Cost model
Versioned fee schedule (`FEES-1`, D5). Entry and exit costs computed only through the fill rule (R1.2) plus fees.

### R0.9 Early-assignment rule (`ASSIGN-1`)
At each snapshot, for every open short leg, with `extrinsic = max(0, bid − intrinsic)` (using the bid is the conservative choice):
- **Dividend rule:** short call is in the money, the next trading day is the ex-dividend date of a dividend known as of the snapshot, and `dividend > extrinsic` → assume assigned.
- **Threshold rule:** any in-the-money short leg with `extrinsic < $0.05` (strict) → assume assigned.

Consequences (default; record as a DR): assignment is assumed to occur before the next snapshot. At the next snapshot the resulting stock position and **all remaining legs of the structure** are closed by crossing the spread (stock at underlying bid/ask). If the resulting position is short stock across an ex-dividend date, the dividend is owed. Each assumed assignment is a journal event.

**Fixtures:**
- Short 100C, S=104, call bid 4.05 / ask 4.15 → extrinsic 0.05. Dividend 0.25, ex-date next trading day → **assigned**. Dividend 0.04 → not assigned by the dividend rule; threshold rule 0.05 < 0.05 is false → **not assigned**.
- Short 100P, S=90, put bid 9.98 / ask 10.04 → extrinsic max(0, −0.02) = 0 < 0.05 → **assigned**.
- Out-of-the-money short legs are never assigned under `ASSIGN-1`.

### R0.10 Expiration settlement (`EXP-1`)
Options in the money by ≥ $0.01 at the official close are exercised or assigned (OCC exercise-by-exception). Settle at intrinsic value using the official closing price, treating resulting stock as closed at that price. If any short leg finishes within $0.25 of its strike, flag the settlement `PIN_RISK_APPROXIMATE`.

### R0 definition of done
All R0 tests pass; the lookahead guard is tested to raise; a replay snapshot can be built for a historical date and a prospective snapshot taken live; candidate generation is deterministic; all R0 decision records written. Write `docs/memos/R0-result.md`. **STOP POINT B.**

---

## 6. Stage R1 — prospective paper-trade registry

### R1.1 Registry and journal
- **Recommendations** table: every field required by spec §11, including: trade ID; engine/baseline name and version; code commit; artifact hash; decision and receipt timestamps; `snapshot_id` and quote provenance; complete leg identifiers; quantity; entry and cancellation conditions; expiration and horizon; executable-price assumptions (fill rule version); capital at risk; predicted net-return distribution (for baselines: summary quantiles and mean); expected % and $ returns; tail measures; probability of fill (baselines may record `null` with a reason); exit policy id; ranking-adjustment method id (`ADJ-0` = none in R1); random seed where applicable; mode (`PROSPECTIVE`/`REPLAY`).
- **Journal** (append-only events): `ORDER_SUBMITTED`, `LEG_FILLED`, `ORDER_UNFILLED`, `EXIT_SUBMITTED`, `EXIT_FILLED`, `EXIT_UNFILLED`, `ASSIGNMENT_ASSUMED`, `EXPIRATION_SETTLED`, `TRADE_CLOSED`, `ANNOTATION`, `CORRECTION` (corrections link to the record they correct; nothing is edited).
- **Immutability:** SQLite triggers abort any `UPDATE` or `DELETE` on both tables. Each row stores its canonical JSON, its SHA-256, and the previous row's hash (hash chain). `registry verify` recomputes the chain.
- **External timestamp:** a nightly JSONL export of new rows is committed and pushed (D12).
- Prospective and replay records are kept in **separate databases**; reports never mix them.

### R1.2 Paper fill engine (`FILL-1`)
- Fills use the **execution quote** (D3), never the decision snapshot itself.
- Buy legs fill at the displayed ask, sell legs at the displayed bid, only if displayed size at that price ≥ the full leg quantity. A multi-leg order fills only if every leg fills; otherwise `ORDER_UNFILLED` and the trade is over (single attempt).
- Exits use the same rule at each scheduled exit opportunity; an unfilled exit retries at each subsequent snapshot's execution quote until expiration, where `EXP-1` applies.
- **Fixtures:** buy 5 contracts with ask size 3 → unfilled; a 4-leg condor with one leg's size short → whole order unfilled; a fill uses the execution quote's prices, not the snapshot's.

### R1.3 Exit policy for baselines
`HOLD-1`: no discretionary exits; the position is held to expiration and settled under `EXP-1`, except for forced closes from `ASSIGN-1`. (Spec v0.5's least-squares Monte Carlo optimizer is R4; leave an `ExitPolicy` interface for it.)

### R1.4 Market-implied baseline (`MKT-1`)
For each underlying and expiration in the snapshot:
1. Infer the forward and discount factor from put–call parity on near-the-money pairs (avoids needing exact dividend forecasts).
2. Compute implied volatilities from out-of-the-money mid prices. Black-Scholes on the forward is acceptable for R1; flag that American early-exercise premium is ignored.
3. Fit a smooth, arbitrage-checked smile (cubic spline or SVI in log-moneyness). Reject and flag expirations whose fit fails no-arbitrage checks (negative density, non-monotone call prices).
4. Convert to a risk-neutral terminal density (Breeden–Litzenberger) and compute each candidate's expected terminal payoff.
5. Expected net return on capital at risk = (expected payoff − executable entry cost − fees) / capital at risk, using the spread-crossing entry prices.
6. Rank with `ADJ-0` (no adjustment) and select per D6–D7.

**Sanity property (this is the baseline's purpose):** its predicted net returns should cluster near −(half-spreads + fees)/capital at risk. Tests: on a synthetic chain generated from a known lognormal model with zero spread, expected payoffs match model prices within tolerance; with non-zero spreads, predicted net returns are negative by approximately the spread cost. A large positive predicted return is treated as a bug signal and logged.

The optional volatility-risk-premium adjustment is **off** in R1 (estimating it is R2 work). Leave a hook.

### R1.5 Random baseline (`RAND-1`)
Uniformly sample K priceable, recommendable candidates per snapshot, subject to the same caps as `MKT-1`. Seed = hash(`snapshot_id`, `RAND-1`); recorded. Same inputs ⇒ same picks (test it).

### R1.6 Scheduler
- `launchd` plist templates for: open snapshot → recommendations → execution-quote fill pass; close snapshot → recommendations → fill pass → exit/assignment/expiration pass → nightly export.
- Trading calendar from `exchange_calendars` (holidays, half-days).
- Jobs are idempotent (re-running a completed job is a no-op) and log to `data/logs/`. Failures produce a macOS notification and a gap record.
- A `doctor` command checks: Theta Terminal reachable, credentials present, disk space, last successful snapshot, registry chain valid.

### R1.7 Reports
`report trades` (per-trade outcome vs prediction), `report daily`, `report cumulative` by baseline: fill rate, realized vs predicted net return on capital at risk, P&L distribution, drawdown, counts of assignments and pin-risk flags. Mode is always shown; prospective and replay are never combined.

### R1.8 Replay mode
Run the full R1 pipeline over a historical date range (e.g., one recent month) using replay snapshots and the same offsets, writing to the replay registry. Purpose: an end-to-end test of the machinery and early sanity numbers, **not** evidence of edge.

### R1 definition of done
- End-to-end replay over one historical month completes; `registry verify` passes; reports run.
- Prospective pipeline runs live for at least **5 consecutive trading days** on the Mac mini with no manual intervention (gaps, if any, recorded correctly).
- The market-implied baseline's predicted returns show the sanity property; any violations are explained in the memo.
- All R1 decision records written. Write `docs/memos/R1-result.md`. **STOP POINT C.**

---

## 7. Testing standards

- The full test suite runs offline against `FixtureSource`; no network in `pytest`.
- Unit tests for all Section 5–6 fixtures, property tests for payoff and capital at risk, integration tests for snapshot → candidates → recommendations → fills → settlement on a fixture week.
- A dedicated **look-ahead test module**: deliberately request future data through every public data path and assert `LookaheadError`.
- `ruff`, `mypy`, and `pytest` must pass before any stage memo is written. Commit in small, reviewable steps with descriptive messages.

## 8. Explicitly out of scope

Factor or stochastic economic models; causal search, opening books, transpositions, fingerprints; least-squares Monte Carlo or any optimized exit policy; uncertainty-adjusted ranking methods beyond the `ADJ-0` placeholder; volatility-risk-premium estimation; neural or hybrid engines; the REMY Analysis Protocol wire format; any brokerage integration; any web UI.

## 9. Stop points

| Stop | After | Josh reviews |
|---|---|---|
| **A** | R0.1 data verification | Whether ThetaData meets the data contract; supplementary sources; storage estimate |
| **B** | End of R0 | Payoff/assignment correctness, determinism, decision records D1–D12 |
| **C** | End of R1 | Registry immutability, fill behavior, baseline sanity, 5-day live run |

At each stop, end with: what was built, test summary, decision records awaiting confirmation, and the specific questions Josh needs to answer.

---

## 10. First message to paste into Claude Code

> Read `docs/handoff/HANDOFF_R0_R1.md` and `docs/spec/spec_v0.5.md` in full. Then create `CLAUDE.md` summarizing the ground rules in Section 0 of the handoff, scaffold the repository per Section 3, write decision records DR-0001 to DR-0012 for the proposed defaults in Section 2 (status: proposed), and carry out task R0.1 only. Stop at Stop Point A with the data-verification memo.
