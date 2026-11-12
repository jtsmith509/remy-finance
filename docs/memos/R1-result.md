# R1 result: registry, paper execution, baselines and the live run (Stop Point C)

- **Stage:** R1 (handoff §6)
- **Date:** drafted 2026-11-11; the live-run section is completed after the 5-day run (11–17 November 2026)
- **Branch:** `claude/cool-pascal-80kptn`

> **DRAFT.** Everything except §5 (the 5-day live run) and the final gate numbers is written from results already in hand. **TODO** marks the parts that wait for the live run.

> **Status against the R1 definition of done (handoff §6):**
> - **Replay month:** done. August 2026 ran end to end three times (MKT-1/RAND-1 v1, v2, v3). The replay registry passed `registry verify`, and the reports ran.
> - **5-day live run:** in progress on the Mac mini since 2026-11-11, with no manual intervention. **TODO:** the result.
> - **Sanity property:** shown. No MKT-1 v3 prediction on the 3 August board is above carry, and MKT-1 v3's picks predicted 0.0% on average (§4).
> - **Decision records:** all written. DR-0028 to DR-0039 are accepted, and none are awaiting confirmation.
> - **Independent review:** Codex found 16 defects at `b38b9b1`. 15 are fixed with regression tests; 1 (F09) is deferred to after the live run by Josh's decision. A check of the fixes is with Codex (`docs/review/R1-fixes-review-brief.md`).

## 1. What was built

| Task | Module | Summary |
|---|---|---|
| R1.0 | `movegen/`, `core/record.py` | Move-generator speed-up that keeps DR-0025's exactness. Content hashes are cached per record and shared across candidates. Real 50-name boards: 423–516k candidates (MOVEGEN-2) in about 25–38 s. |
| R1.1 | `registry/` | SQLite (WAL) recommendations and journal tables. Append-only triggers, per-table hash chains, next-row-only insert guards, atomic event batches. `registry verify` and a nightly JSONL export to the `registry-export` branch. Separate prospective and replay databases (DR-0028). |
| R1.2 | `execution/engine.py`, `execution/fill.py` | FILL-1 paper engine. Fills use the execution quote, cross the spread, need the full displayed size, and are all-or-none per order; a single attempt. The journal is the only state, and lifecycle rules are checked on append (DR-0029). |
| R1.3 | `execution/exit_policy.py` | HOLD-1, plus the `ExitPolicy` interface for R4. Exits retry at each execution quote until EXP-1 applies. |
| R1.4 | `baselines/mkt1.py`, `baselines/smile.py` | MKT-1 v3 (DR-0030, DR-0037, DR-0038). Discount factor from the board's rates and forward from parity; arbitrage-free SVI (Durrleman g ≥ 0 on k ∈ [−3, 3]); Breeden–Litzenberger density. Each leg's expected payoff is anchored at mid ÷ DF, and the clipped distribution's mean is matched exactly. Bug signals are logged, never selected. The VRP hook is off. |
| R1.5 | `baselines/rand1.py` | RAND-1 v3 (DR-0031). Hash-ordered uniform sampling under the same SELECT-1 caps, with seed `sha256(snapshot_id, "RAND-1")`. Its forecasts are MKT-1's, so it differs from MKT-1 only in selection. |
| R1.6 | `scheduler/` | One idempotent `remy run tick` every minute from launchd (DR-0033). XNYS calendar; decision and execution slots (5-minute offset); gaps; fills; management; settlement; nightly export and backup. `remy doctor`. Held names on boards (DR-0039), a reconciliation watermark, and a separate guard on the recommendation step. |
| R1.7 | `reports/` | `report trades`, `report daily` and `report cumulative`, one mode each. Engine and version are never pooled. Each header lists every engine/version/code commit. Results use the EVAL-1 61-day cohort, with a labelled operational view, a CRPS cluster bootstrap, a PIT histogram, coverage, and breakdowns by structure and rank. |
| R1.8 | `scheduler/replay.py`, `reports/diagnose.py`, `reports/measured.py` | Replay driver over a date range with the live offsets, writing the replay registry (DR-0034). It runs a tail until every position closes. Diagnostics and power inputs come from the replay. |
| R0 amendment | `movegen/` | MOVEGEN-2, now the default: long straddles, strangles, long iron condors and long iron butterflies (DR-0035). |
| Evaluation | `docs/evaluation/` | The spec §11 power estimate with addenda, and the frozen EVAL-1 scoring protocol (DR-0032 record changes). |

## 2. Test results (offline, no network)

At the draft (commit `aa53762`): `ruff check`, `ruff format --check` and `mypy` are clean. **pytest: 515 passed** (309 at the R0 sign-off). CI runs the same gate on every push. **TODO:** re-run the gate for the final memo.

| Area | Highlights |
|---|---|
| Registry | `UPDATE` and `DELETE` abort; hash-chain tamper detection (property tests); inserts that skip a row are refused; corrections link to the row they correct; the export is append-only and safe under concurrent exporters; verification uses one read snapshot. |
| Fill engine | The handoff fixtures: 5 contracts against ask size 3 is unfilled; a condor with one short leg is unfilled; fills use the execution quote. Crash injection inside atomic batches (entry legs; terminal event plus `TRADE_CLOSED`), and repair of a flat trade left open. |
| MKT-1 | On a synthetic lognormal chain with zero spread, expected payoffs match model prices. With spreads, predictions are negative by about the spread cost. Durrleman g ≥ 0 and the exact clipped mean are tested; an infeasible mean is skipped and flagged. |
| RAND-1 | Same inputs give the same picks; the caps are respected; the seed is recorded. |
| Scheduler | Simulated trading days with a fake clock: gaps, the dirty-tree skip, a failed capture, a failed recommendation, export retry after a failed push, restart reconciliation, held names. |
| Reports | Scores (CRPS, binned log score, PIT, coverage); the 61-day cohort; the bootstrap is deterministic and clustered by ISO week; an end-to-end report fixture. |

## 3. The replay month (August 2026)

**Replay results are planning inputs and machinery tests, never evidence of edge** (handoff R1.8).

| Run | Configuration | Outcome |
|---|---|---|
| 1 | MOVEGEN-1, MKT-1/RAND-1 v1 | Took over 24 h before the hashing fix (R1.0). MKT-1 v1 ranked its own fit errors first: picks predicted +4.2% and realized −73.5%. Archived; led to DR-0037. |
| 2 | MOVEGEN-2, v2 (DR-0037) | Mid-anchored means and DF from rates. The arbitrage check rejected 250 of 376 expirations on 3 August, which led to DR-0038. |
| 3 | MOVEGEN-2, v3 (DR-0038): the live configuration | 21 decision sessions (3–31 August), 420 recommendations, every position closed. On 3 August, 320 of 376 expirations fit and 269,846 of 423,057 candidates are scored. |

**Run 3, cumulative (all closed trades):**

| | MKT-1 v3 | RAND-1 v3 |
|---|---|---|
| Recommended / filled | 210 / 210 (100%) | 210 / 166 (79%; 43 insufficient size, 1 no price) |
| Mean predicted / mean realized | 0.0% / −32.1% | −25.3% / −34.8% |
| Median realized | −41.1% | −56.1% |
| P&L / max drawdown | −$112,632 / $112,737 | −$69,066 / $69,066 |
| Assignments / forced exits / exit retries | 0 / 0 / 0 | 45 / 34 / 36 |
| Mean CRPS | 0.277 | 0.293 |
| Below the 5% quantile (expected 5%) | 39.5% | 31.9% |
| Spearman(predicted, realized) | −0.42 | +0.42 |

**Reading run 3:**
1. **MKT-1 bought long options, and long options lost.** Long calls realized −49% and long puts −27%; its 40 credit verticals made +0.3%. This is consistent with a volatility risk premium, which MKT-1 deliberately leaves out (VRP off in R1). It is a property of the risk-neutral baseline, not a defect.
2. **The forecasts look badly calibrated, for two reasons EVAL-1 specifies on purpose.**
   - Realized returns are measured against capital at risk at the *decision* price. A lost premium filled at a worse execution price therefore counts below −100%.
   - The forecast has no slippage model.
   - Together these put the realized −100% (and below) mass at or under the forecast's lowest quantile. That explains most of the 0.0–0.1 PIT bin (88 of 210 for MKT-1).
3. **RAND-1's positive rank correlation isn't skill.** Its predicted means mostly encode spread cost relative to capital at risk, and costly trades realize worse. MKT-1's negative correlation, from a single month, is one market path.
4. **The machinery worked end to end.** That includes 45 assignments, 34 forced exits and 36 exit retries in RAND-1, and settlement of every position by 30 October.

The power estimate's addenda (2026-11-10 and 2026-11-11) record the measured inputs: fill 100% and 79%, σ ≈ 0.96. Prior ρ = 0.10 is kept. Detecting δ = 5% / 10% / 20% needs about 238 / 59 / 15 months, and the smallest δ detectable in 12 months is about 22%.

## 4. The sanity property (handoff R1.4)

- **Required:** predicted net returns cluster near −(half-spreads + fees) / capital at risk, and a large positive prediction is a bug signal.
- **Synthetic tests:** passed. With zero spread, model prices match; with spreads, predictions are negative by about the spread cost.
- **The 3 August 2026 open board (v3):** none of the 269,846 scored candidates predicts above zero in any structure (`replay diagnose`). The highest predictions are −0.1% to −0.2%: cheap wings, where the spread is small relative to capital at risk.
- **MKT-1 v3's picks** predicted 0.0% on average over the month, which is SELECT-1 picking the candidates closest to carry. No bug signal was selected.
- **History:** v1 violated the property, because fit errors produced positive predictions. That was fixed by anchoring each leg's mean at mid (DR-0037). Every live recommendation uses v3.
- **TODO:** confirm on the live boards (the run logs' bug-signal counts and the predicted-return range per session).

## 5. The 5-day live run (TODO)

- **Installed:** 2026-11-11 (launchd, `remy run tick` every minute). `remy doctor` was clean apart from the expected pre-start warnings.
- **Install:** the first tick ran at 00:13 ET on Wednesday 11 November (05:13 UTC). Its nightly step exported 0 rows for 10 November and backed up the empty registry. The reconciliation watermark starts on 11 November.
- **Sessions:** Wednesday 11 to Tuesday 17 November 2026 (11, 12, 13, 16 and 17 November): 5 sessions with 2 slots each, for 20 snapshot slots.
- **Day 1, Wednesday 11 November** (from the scheduler log and `remy doctor`):

| Step | OPEN | CLOSE |
|---|---|---|
| Decision board stored (as_of, ET) | 10:01:48 | 15:46:33 |
| Candidates; movegen / scoring time | 400,021; 26 s / 17 s | 404,654; 26 s / 10 s |
| Recommendations | 10 | 10 |
| Execution board stored (as_of, ET) | 10:07:58 | 15:53:31 |
| Entry events | 21 | 23 |
| Position events | 0 | 0 |

  - **Daily report:** MKT-1 v3 filled 10 of 10 entries (13 legs). RAND-1 v3 filled 9 of 10 (29 legs); its 1 unfilled order closed the same day. 0 snapshot gaps. Every record names engine commit `aa53762`. The 44 entry events (13 + 29 legs, 1 unfilled, 1 close) match the log's 21 + 23.
  - **Nightly step (17:30 ET):** exported 84 rows (`registry-export` commit `ada8133803bf`) and wrote the backup `prospective-20261111T223026Z.sqlite`.
  - **`remy doctor` afterwards:** every line OK. The prospective registry holds 20 recommendations and 64 journal rows; the last snapshot is 2026-11-11 CLOSE, stored; no failures in 24 h; the code tree is clean.
- **TODO, per day:**
  - decision and execution snapshots stored, or gaps with reasons;
  - capture timing;
  - recommendations per baseline;
  - fills and unfilled reasons;
  - management events;
  - failures and notifications;
  - export commits on `registry-export`;
  - backups.
- **TODO:** `registry verify` on the prospective registry; the `report daily` and `report cumulative` summaries (operational view only, since no trade enters the 61-day EVAL-1 cohort until January); any manual intervention (target: none).

## 6. Independent review (Codex, at `b38b9b1`)

| Area | Findings | Fixed | Findings file |
|---|---|---|---|
| R1.1 registry | 4 (1 high) | 4 | `docs/review/R1.1-findings.md` |
| R1.2–R1.5 execution and baselines | 3 (3 high) | 3 | `docs/review/R1.2-R1.5-findings.md` |
| R1.6–R1.8 scheduler and reports | 9 (4 high) | 8 | `docs/review/R1.6-R1.8-findings.md` |

- **Regression tests:** every fix has one (`tests/unit/review/test_codex_r1_*.py`).
- **The open finding:** **F09**, EVAL-1 §2.2 scoring of the underlying S_T/F forecasts. Josh deferred it to after the live run (2026-11-11). The persisted `rn_quantiles` are the input; no live expiration needs it before the first scored cohort.
- **The fix check** (Codex, at `aa53762`, 2026-11-11): 9 of the 15 fixes are closed and 6 partly closed, with 7 new findings (V01–V07: one high, five medium, one low). None affects the live run (`docs/review/R1-fixes-findings.md`). **TODO:** the fixes, applied after the run.

## 7. Known limitations

- **No edge is detectable in R1.** The baselines aren't designed to have one; see the power estimate. The live run is a machinery test.
- **The VRP is not modelled** (R2). The risk-neutral MKT-1 therefore buys long options, which lose on average.
- **Calibration is distorted** by the decision-price denominator and the lack of a slippage model (§3). Both are as EVAL-1 specifies; changing them is an EVAL-2 question.
- **The American early-exercise premium is ignored** in MKT-1's implied volatilities (handoff R1.4); every recommendation is flagged.
- **Expirations that fail the fit are skipped,** not repaired: 56 of 376 on 3 August, of which 28 were negative densities and 26 were poor or failed fits.
- **Fills are all-or-none at the displayed size.** RAND-1 lost 21% of its entries to insufficient size, mostly on four-leg structures.
- **Option volume is end-of-day, prior session only.** There are no intraday signals.
- **The R0 limitations still apply:** survivorship bias in the candidate list; ASSIGN-1 near the money; the strike lattice; ETF dividends announced only on the ex-date.
- **The Mac must stay awake,** and Theta Terminal must stay running. A sleeping Mac produces gaps, which are recorded correctly but lose observations.

## 8. Decision records

- **R1 records, all accepted:**
  - DR-0028 to DR-0036 (2026-11-09);
  - DR-0037 and DR-0038 (2026-11-10);
  - DR-0039 (2026-11-11).
- **Departures from the handoff, each accepted through a DR:**
  - The discount factor comes from rates, not parity (DR-0037).
  - The mean is anchored at mid (DR-0037).
  - MOVEGEN-2 is the default (DR-0035).
  - Boards carry held names (DR-0039).
- **TODO:** any DR arising from the live run or the fix check.

## 9. Questions for Josh at Stop Point C (draft)

1. **F09:** build the EVAL-1 §2.2 underlying scores now that the live run is done, before starting R2?
2. **Calibration denominator:** should EVAL-2 also score returns on *execution-price* capital at risk, alongside the frozen decision-price measure, so that slippage stops dominating the tail metrics? EVAL-1 itself stays frozen.
3. **The live run's own questions:** **TODO.**
4. **Close Stop Point C** and begin R2?
