# R1 result: registry, paper execution, baselines and the live run (Stop Point C)

- **Stage:** R1 (handoff §6)
- **Date:** 2026-11-18 (drafted 2026-11-11; the 5-day live run was 11–17 November 2026). Updated the same day with Josh's Stop Point C decisions (§9) and the R1 extras they added (§10).
- **Branch:** `claude/cool-pascal-80kptn`

> **Checks on the Mac (2026-11-18):** 0 bug signals. 3 of the 110 live recommendations (all RAND-1) have forecasts whose upper quantiles exceed the attainable maximum (Codex V04, §6); they are to be annotated.

> **Status against the R1 definition of done (handoff §6):**
> - **Replay month:** done. August 2026 ran end to end three times (MKT-1/RAND-1 v1, v2, v3). The replay registry passed `registry verify`, and the reports ran.
> - **5-day live run:** done. Five consecutive sessions (11, 12, 13, 16 and 17 November) ran with **no manual intervention**: 20 of 20 snapshot slots stored, 0 gaps, 0 failures, five nightly exports pushed, and `registry verify --export-dir` OK (§5).
> - **Sanity property:** shown, with one explained departure. MKT-1 v3's predictions are carry − (half-spreads + fees), as DR-0037 defines. Live picks therefore range from −0.2% to +0.4%, the positive ones being long-dated options whose carry exceeds their spread cost. All are far below the 5% bug-signal threshold (§4).
> - **Decision records:** DR-0028 to DR-0042 all accepted (DR-0040 to DR-0042 on 2026-11-18). None are awaiting confirmation.
> - **Independent review:** Codex found 16 defects at `b38b9b1`, all now fixed (F09 on 2026-11-18). Codex's check of the fixes found 7 more (V01–V07), all fixed (§6). Every fix has a regression test.
> - **Gate:** ruff, format and mypy clean; **pytest 549 passed** (§2).

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

At commit `f6c94a6` (after the V01–V07 fixes, the annotate command and F09): `ruff check`, `ruff format --check` (205 files) and `mypy` (72 source files) are clean. **pytest: 549 passed** (515 at the live run's code `aa53762`; 309 at the R0 sign-off). CI runs the same gate on every push.

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
- **The live run (55 MKT-1 picks, 11–18 November):** predictions range from −0.2% to +0.4% (mean about +0.05%; 29 of 55 are positive). This is the one departure from the property as the handoff words it, and it is explained by carry:
  - DR-0037 anchors each leg's expected payoff at its mid carried to expiration (mid ÷ DF). The prediction is therefore exactly (carry − half-spreads − fees) ÷ capital at risk.
  - For a long option, carry is the risk-free rate on the premium paid. Up to 60 days at about 4% a year, that is up to about 0.65% of the premium, which can exceed the cost of a tight spread.
  - SELECT-1 picks the highest predictions, so MKT-1 picks exactly those: 19 long calls and 24 long puts, plus 12 credit verticals.
  - Every prediction stays below carry and far below the 5% bug-signal threshold. Bug signals are excluded from selection by construction.
  - The handoff's wording (−(half-spreads + fees)) ignores carry. The August replay's picks averaged 0.0%.
- **Bug signals (checked on the Mac, 2026-11-18):** **0** over all 22 run logs (11 decision boards × 2 baselines, 11–18 November). No candidate predicted above 5%.

## 5. The 5-day live run (11–17 November 2026)

- **Install:** the first tick ran at 00:13 ET on Wednesday 11 November (05:13 UTC). Its nightly step exported 0 rows for 10 November and backed up the empty registry. The reconciliation watermark starts on 11 November.
- **No manual intervention:** the scheduler ran from launchd throughout. Nothing was pulled, restarted or edited, and no failure was recorded (`data/logs/failures/` never created).
- **Sources:** the five daily logs, `remy report daily` for each session, `remy report cumulative` and `report trades` (generated 2026-11-18, after the next open), `remy doctor`, `remy registry verify --export-dir` and the `registry-export` history. The files stay on the Mac mini.

**Per session** (times ET; boards are stamped after the fetch):

| Day | OPEN decision / execution | CLOSE decision / execution | Candidates (open, close) | Entry events (open, close) | Position events (open, close) |
|---|---|---|---|---|---|
| Wed 11 | 10:01:48 / 10:07:58 | 15:46:33 / 15:53:31 | 400,021, 404,654 | 21, 23 | 0, 0 |
| Thu 12 | 10:01:34 / 10:08:35 | 15:46:26 / 15:52:42 | 419,607, 399,315 | 18, 21 | 1, 3 |
| Fri 13 | 10:01:06 / 10:08:05 | 15:46:36 / 15:53:36 | 436,845, 431,578 | 23, 21 | 0, 0 |
| Mon 16 | 10:01:22 / 10:07:54 | 15:46:12 / 15:52:10 | 416,473, 417,855 | 18, 21 | 1, 3 |
| Tue 17 | 10:01:34 / 10:08:33 | 15:46:57 / 15:54:00 | 410,046, 423,706 | 21, 23 | 0, 0 |

- **Slots:** 20 of 20 stored and 0 gaps. Every decision board was stamped 1–2 minutes after its scheduled time, and every execution board 6–7 minutes after its decision. All were well inside the 10-minute windows.
- **Recommendations:** 10 per session (5 MKT-1, 5 RAND-1), so 100 in all. The step took about 40–60 s per board: movegen 25–39 s and scoring 10–20 s.
- **Nightly steps (17:30 ET):** exports of 84, 83, 84, 83 and 84 rows, pushed as `registry-export` commits `ada8133`, `b8fb5f9`, `863c8a8`, `5450bff` and `6b1d97d`. A backup was written each night.
- **Integrity:** `registry verify --export-dir` gives `registry OK`, with all 418 exported rows checked byte for byte; 418 equals the five nightly exports summed. `remy doctor` passes on every line. Every record names engine commit `aa53762`, the committed code.

**Per day, from `report daily`:**

| Day | MKT-1 filled legs / unfilled | RAND-1 filled legs / unfilled | RAND-1 assignments / exits filled / closed | P&L of trades closed |
|---|---|---|---|---|
| Wed 11 | 13 / 0 | 29 / 1 | 0 / 0 / 1 | $0.00 |
| Thu 12 | 13 / 0 | 26 / 0 | 1 / 1 / 1 | −$691.80 |
| Fri 13 | 16 / 0 | 26 / 1 | 0 / 0 / 1 | $0.00 |
| Mon 16 | 10 / 0 | 25 / 2 | 1 / 1 / 3 | −$839.50 |
| Tue 17 | 10 / 0 | 34 / 0 | 0 / 0 / 0 | $0.00 |

**Cumulative** (110 recommendations, including the 18 November open, which ran as normal):

| | MKT-1 v3 | RAND-1 v3 |
|---|---|---|
| Recommended / filled | 55 / 54 (98.2%) | 55 / 51 (92.7%) |
| Unfilled | 1, insufficient size (GLD long put, 18 November) | 4, insufficient size: SPY credit vertical, MU and XOM long iron condors, GLD long strangle |
| Open / closed | 54 / 0 | 49 / 2 filled closed (plus the unfilled ones) |
| Assignments / forced exits | 0 / 0 | 3 / 2 |
| P&L of closed trades | $0.00 | −$1,531.30 |
| EVAL-1 cohort | 0 trades (the first trades enter in January 2027) | 0 |

- **The machinery under live data:** ASSIGN-1 fired three times on RAND-1's short legs: a UNH put butterfly, an UBER credit vertical, and an AMZN credit vertical still open. The forced closes filled at the next execution quote.
- **The two closed trades:**
  - UNH: −39.9% realized against −96.5% predicted.
  - UBER: −136.9% realized against −77.0% predicted. It lands below −100% because returns are measured on decision-price capital at risk (§3, §7).
- **Machinery checks only:** the operational view's forecast scores (2 trades) are not EVAL-1 results.
- **Conclusion:** the R1 definition of done ("at least 5 consecutive trading days on the Mac mini with no manual intervention, gaps recorded correctly") is met. The scheduler keeps running, adding to the prospective record that EVAL-1's first confirmatory analysis uses at 12 months.

## 6. Independent review (Codex, at `b38b9b1`)

| Area | Findings | Fixed | Findings file |
|---|---|---|---|
| R1.1 registry | 4 (1 high) | 4 | `docs/review/R1.1-findings.md` |
| R1.2–R1.5 execution and baselines | 3 (3 high) | 3 | `docs/review/R1.2-R1.5-findings.md` |
| R1.6–R1.8 scheduler and reports | 9 (4 high) | 8 | `docs/review/R1.6-R1.8-findings.md` |

- **Regression tests:** every fix has one (`tests/unit/review/test_codex_r1_*.py`).
- **F09** (EVAL-1 §2.2 scoring of the underlying S_T/F forecasts): Josh deferred it to after the live run (2026-11-11), and it was built on 2026-11-18 (`f6c94a6`, DR-0040).
- **The fix check** (Codex, at `aa53762`, 2026-11-11): 9 of the 15 fixes closed and 6 partly closed, with 7 new findings (V01–V07: one high, five medium, one low). None affected the live run. All 7 were fixed on 2026-11-18 (`a3aefe8`, `a493978`, `e5b7146`, `c42d650`). Codex's probes are the regression tests (`tests/unit/review/test_codex_r1_verify_*.py`): 9 fail at `aa53762` and pass on the fix (`docs/review/R1-fixes-findings.md`).
- **V04 exposure (checked on the Mac, 2026-11-18):** 4 of the 110 live recommendations can only lose, all picked by RAND-1, none by MKT-1:

  | Decided | Structure | Trade |
  |---|---|---|
  | 13 Nov | LONG_IRON_CONDOR | `T-2003e1884e5cc367a87d951c` |
  | 17 Nov | IRON_CONDOR (META) | `T-cb1617862c81df3cad3b403c` |
  | 17 Nov | IRON_CONDOR (SMH) | `T-4b067e2f3855e90317373293` |
  | 18 Nov | LONG_IRON_CONDOR (TSM) | `T-77e55cad8f9b504e273e4fc0` |

  At executable prices (selling at the bid, buying at the ask) plus fees, even the best outcome of each is a loss. For the two short iron condors, the credit received doesn't cover the fees and the cost of the long wings.

  **Forecasts against the attainable maximum** (checked on the Mac, 2026-11-18):

  | Trade | Best attainable return | Recorded mean | Recorded top quantile | Affected? |
  |---|---|---|---|---|
  | `T-2003e1884e5cc367a87d951c` | −23.69% | −48.80% | −25.64% | no |
  | `T-cb1617862c81df3cad3b403c` | −49.81% | −58.71% | −42.18% | **yes**, by 7.6 points |
  | `T-4b067e2f3855e90317373293` | −11.47% | −16.30% | −9.73% | **yes**, by 1.7 points |
  | `T-77e55cad8f9b504e273e4fc0` | −9.52% | −42.28% | −8.95% | **yes**, by 0.6 points |

  - **Three forecasts are affected.** Their upper quantiles extend above anything the trade can return.
  - **Their means are right.** Each recorded mean is below the attainable best, so the fixed code would have scored them too, with the upper tail capped.
  - **Effect on EVAL-1:** CRPS is slightly off, and these trades can never exceed their upper quantiles, which biases the upper-coverage counts.
  - **What happens to them:** under EVAL-1 §1.7 they stay in and are annotated in the journal, never edited (question 6). Results are then reported with and without them.

## 7. Known limitations

- **No edge is detectable in R1.** The baselines aren't designed to have one; see the power estimate. The live run is a machinery test.
- **The VRP is not modelled** (R2). The risk-neutral MKT-1 therefore buys long options, which lose on average.
- **Calibration is distorted** by the decision-price denominator and the lack of a slippage model (§3). Both are as EVAL-1 specifies; changing them is an EVAL-2 question.
- **The American early-exercise premium is ignored** in MKT-1's implied volatilities (handoff R1.4); every recommendation is flagged.
- **Expirations that fail the fit are skipped,** not repaired: 56 of 376 on 3 August, of which 28 were negative densities and 26 were poor or failed fits.
- **Fills are all-or-none at the displayed size.** RAND-1 lost 21% of its entries to insufficient size in the replay and 7% live, mostly on four-leg structures.
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
- **Accepted at Stop Point C (Josh, 2026-11-18):**
  - DR-0040: the F09 scoring details (every fit on every board; a linear density with a 10⁻⁴ floor; closes fetched at report time);
  - DR-0041: from R2, exclude candidates whose best return is ≤ 0, and bump both baselines' versions;
  - DR-0042: from R2, rank on return in excess of the risk-free rate on capital at risk (ADJ-1); the recorded forecasts are unchanged.

## 9. Decisions at Stop Point C (Josh, 2026-11-18)

| # | Question | Decision |
|---|---|---|
| 1 | F09 underlying scores | **Build now, before R2.** Done (§10). |
| 2 | Calibration denominator | **Add to EVAL-2, alongside EVAL-1:** returns on execution-price capital at risk as a labelled second measure. The frozen EVAL-1 measure stays primary. Drafted with R2. |
| 3 | Carry in MKT-1's predictions | **Accept the explanation for R1; from R2, rank on excess return** (DR-0042, proposed). MKT-1 v3 is unchanged. |
| 4 | Pulling the V01–V07 fixes onto the Mac | **Pull tonight, after the 17:30 ET export.** |
| 5 | Candidates that can only lose | **Exclude from R2, via a new DR** (DR-0041, proposed). R1's v3 records stay as recorded. |
| 6 | The 3 affected forecasts | **Add an annotate command and annotate all 3.** Reports flag them and show results without them. Done in code (§10); the 3 annotations are written on the Mac after the pull. |
| 7 | When Stop Point C closes | **After the R1 extras:** built, tested, recorded here, then Josh's confirmation. |

## 10. R1 extras added at Stop Point C (2026-11-18)

- **Known-defect annotations** (`f1c98c2`; EVAL-1 §1.7):
  - `remy registry annotate --trade T --defect ID --note TEXT` appends an `ANNOTATION` whose note starts `DEFECT <ID>:`. It is append-only, hash-chained and exported. Re-running it writes nothing.
  - `report trades` flags each annotated trade.
  - `report cumulative` repeats the EVAL-1-cohort and operational results without annotated trades, as a labelled sensitivity analysis.
- **F09, underlying forecast scores** (`f6c94a6`; DR-0040):
  - `report cumulative` scores every MKT-1 fit's S_T/F quantiles against the public official close, by CRPS and a quantile-grid log score, in the EVAL-1 cohort and the operational view, with the cluster bootstrap.
  - It reads closes from ThetaData; `--no-closes` skips the section.
- **Tests:** 8 new (4 for annotations, 4 for F09), and the full gate passes (§2).
- **Not built (R2):** EVAL-2's execution-price measure, DR-0041's eligibility rule and DR-0042's ranking key.
- **On the Mac, after the pull:**
  - the 3 V04 annotations (`T-cb1617862c81df3cad3b403c`, `T-4b067e2f3855e90317373293`, `T-77e55cad8f9b504e273e4fc0`);
  - one `report cumulative` with closes, to confirm the F09 section runs against the live run logs.
- **Done on the Mac (2026-11-18, evening ET):**
  - The fixes were pulled.
  - The 3 annotations were written: the report's sensitivity sections list `V04 (3)`.
  - `report cumulative` ran with ThetaData closes. It scored 37 underlying forecasts over 20 expirations, mean CRPS 0.0249 in S_T/F units and mean log score −1.236. It counted 4,347 forecasts not yet expired, 0 waiting for a close and 0 unavailable.
  - All 20 scored expirations fall in one ISO week. Movegen's 7-day minimum tenor means the first fitted expirations were 18 November, so the bootstrap correctly reports insufficient clusters.
  - Three RAND-1 trades have closed so far (−$2,410.15).
  - `registry verify --export-dir` after the annotations, under the new verification code: **registry OK**. That covers 120 recommendations and 384 journal rows, with all 501 exported rows checked byte for byte (the five live-run exports plus 18 November's 83). The 3 annotations are exported at the next nightly step.
