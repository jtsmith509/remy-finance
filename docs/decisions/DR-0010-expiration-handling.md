# DR-0010: Baseline exit policy HOLD-1 and expiration settlement EXP-1

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point A)
- **Date:** 2026-11-08
- **Source:** Handoff Section 2, default D10, and handoff §5 R0.10, §6 R1.3
- **Stage:** R0 (R0.10) / R1 (R1.3)

## Context
R1 baselines need a nonanticipating exit policy. The least-squares Monte Carlo optimizer is R4 and out of scope. Expiration settlement must follow OCC exercise-by-exception without modelling broker discretion.

## Options considered
1. **Proposed default:** `HOLD-1`: hold to expiration, settle under `EXP-1`, with forced closes only from ASSIGN-1.
2. A fixed take-profit or stop-loss rule. That's discretionary, adds parameters, and every exit is exposed to FILL-1 size failures.
3. Close at the last snapshot before expiration. It avoids pin risk, but adds spread cost and exit fill risk.

## Decision
Adopt option 1.
- **EXP-1:** options in the money by ≥ $0.01 at the official close are exercised or assigned. Settlement is at intrinsic value using the official closing price, with any resulting stock treated as closed at that price. If any short leg finishes within $0.25 of its strike, the settlement is flagged `PIN_RISK_APPROXIMATE`.
- Under HOLD-1, capital at risk includes entry fees only, because there is no closing trade. Exercise and assignment fees are $0 (DR-0005).
- An `ExitPolicy` interface is left for R4.

## Consequences
- Needs the underlying's **official closing price** on each expiration date. R0.1 found that ThetaData stock EOD `close` looks like the regular-session close, pending a spot-check against the exchange's official close. The EOD `bid`/`ask` are taken at 17:15 ET and **must not** be used.
- Settlement at intrinsic ignores after-hours moves and the holder's discretion near the money. PIN_RISK_APPROXIMATE marks the affected trades.
- Treating stock from exercise as closed at the official close is slightly optimistic; in reality it would be closed the next morning. This is noted in reports.
