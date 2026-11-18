# DR-0042: Rank on return in excess of the risk-free rate (from R2)

- **Status:** proposed (Josh chose this at Stop Point C, 2026-11-18; awaiting confirmation of this record)
- **Date:** 2026-11-18
- **Source:** the live run's sanity check (`docs/memos/R1-result.md` §4). Builds on [DR-0037](DR-0037-mkt1-v2.md).
- **Stage:** R1 decision, effective at R2

## Context
- **What MKT-1 predicts:** by DR-0037, MKT-1 v3 predicts (carry − half-spreads − fees) ÷ capital at risk. Carry is the interest on the premium over the holding period.
- **What happened live:** for long-dated options with tight spreads, carry exceeds the cost. Live picks predicted up to +0.4%, and SELECT-1 picked exactly those options.
- **Why that's a problem:** selection then rewards tying up capital, not a better expected outcome.

## Options considered
1. **Accept the explanation for R1, and from R2 rank on the excess return** (chosen).
2. Accept it, and keep ranking on net return including carry.
3. Change MKT-1 now (v4). That would split R1's prospective record.

## Decision
- **R1:** MKT-1 v3 is unchanged. The memo explains the departure from the handoff's sanity wording.
- **From R2:** engines rank and select on the predicted net return **minus the risk-free return** over the trade's horizon, using the board's rate (the discount factor already used, so (1 ÷ DF − 1) on the capital committed).
  - The recorded predicted distribution stays the net return on capital at risk that EVAL-1 scores. Only the ranking key changes.
  - Each record names its ranking key in its search config.
- **EVAL-1 is unaffected:** it scores forecasts and realized returns, not ranks. The excess-return view of results belongs to EVAL-2 if it's wanted.

## Consequences
- Selection no longer favours long-dated options for their carry. The sanity property then holds in the handoff's form: rankings cluster near −(half-spreads + fees) ÷ capital at risk.
- The ranking key needs a version (e.g. `ADJ-1`, alongside `ADJ-0`), so records show which key selected them.
