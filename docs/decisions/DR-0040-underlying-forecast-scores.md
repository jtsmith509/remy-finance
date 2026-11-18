# DR-0040: Scoring MKT-1's underlying (S_T/F) forecasts

- **Status:** accepted (Josh, 2026-11-18). Every fit on every board is an observation; the density is linear with a 10⁻⁴ floor; closes are fetched at report time.
- **Date:** 2026-11-18
- **Source:** Codex R1.6 F09 (`docs/review/R1.6-R1.8-findings.md`). It implements EVAL-1 §2.2 (frozen) and uses the forecasts recorded under [DR-0032](DR-0032-forecast-records-eval1.md).
- **Stage:** R1, before Stop Point C

## Context
- **What §2.2 says:** for each fitted expiration, MKT-1's risk-neutral distribution of S_T/F is scored against the realized official close on expiration day, by CRPS and by a log score whose density comes from the recorded quantile grid.
- **What it leaves open:** what counts as one observation; where the close comes from at report time; the density between and beyond recorded quantiles; and which cohort the scores belong to.

## Options considered
1. **Score every MKT-1 run log's fits at report time** against official closes read through the point-in-time rule (chosen).
2. Store closes when settlement reads them. Settlement only reads closes for expirations that have a position, which is a small, selected subset.
3. Score one forecast per (symbol, expiration), the latest before expiry. That throws away the earlier forecasts, which are the ones with real horizon.

## Decision
- **Observation:** one per fitted expiration (`ExpirationFit.ok` with recorded `rn_quantiles`) per MKT-1 decision board. RAND-1's run logs carry the same fits (DR-0031) and aren't counted again.
- **Outcome:** the official close on the expiration date, read with `official_close_as_of(source, symbol, expiration, report time)`, so it must be public (17:15 ET). The realized value is close ÷ the fit's recorded forward. In the CLI the source is ThetaData (DR-0026), the same source settlement uses.
  - Each close is fetched once per report.
  - If the source fails, the rest count as *unavailable*; the report never fails.
  - `--no-closes` skips the section.
- **CRPS:** the quantile (pinball) form of §2.1, in units of S_T/F.
- **Log score:** −log of the density at the realized value.
  - The CDF is linear between recorded quantiles, so the density is Δlevel ÷ Δvalue on the segment that holds the value.
  - Beyond the outermost quantiles it is floored at 10⁻⁴, the same floor as §2.1.
- **Cohorts:** the EVAL-1 cohort applies §1.4's 61-day rule to the forecast's decision date. The operational view includes every observation with a public close. Counts of forecasts not yet expired, waiting for a close, or unavailable are printed.
- **Intervals:** the §4 cluster bootstrap on mean CRPS, clustered by the ISO week of the expiration.
- **Reported in** `report cumulative` as "Underlying forecasts (EVAL-1 §2.2)", per engine and version.

## Consequences
- **Many correlated observations:** every board refits every expiration, so there are many observations per expiration. The bootstrap clusters by expiration week, which keeps the intervals honest.
- **A miscalibrated risk-neutral density is expected,** because of risk premia (§2.2). It's reported, not treated as a defect.
- **The report needs the Theta Terminal** for this section. Without it, the section says so.
- **Closes aren't stored.** A vendor revision of an official close would change later reports. ThetaData's EOD closes were verified against the exchange in R0.1, so this is accepted. A write-once close store remains possible later, through a new DR.
