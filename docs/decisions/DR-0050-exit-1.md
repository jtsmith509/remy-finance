# DR-0050: EXIT-1, the profit target and time stop for R2's long options

- **Status:** proposed (2026-11-25). Josh answered the three open questions (below). The rest is the approved brief's (B06) and awaits his confirmation as written.
- **Date:** 2026-11-25
- **Source:** R2 brief §1 (the exit rule, B06), P7 (exit fills on paths), P8 (cadence), §5 (EXIT-1 tests); EVAL-2 §1 (the entry boundary), §6.3 (shadow outcomes) and §7.1 (the close step); FILL-1 (DR-0003, DR-0029); EXP-1; ASSIGN-1; SNAPTIMES-1 (DR-0002).
- **Stage:** R2, Part 1 (R2.6). MKTV-1 (DR-0049), FAC-1, HVOL-1, RAND-L1 and the EVAL-2 panel all value or apply EXIT-1.

## Context
- **Why:** R2's four long-option engines recommend long calls and puts with EXIT-1, not HOLD-1. Their forecasts are EXIT-1 return distributions valued on paths, and their live trades exit under EXIT-1 on real boards.
- **B06's requirement:** the simulated and live rules must stop at the same opportunities, at the same prices, with the same retries.

## Josh's answers (2026-11-25)
1. **The target counts value after exit fees:** a target exit is a net return of at least +100% on capital at risk.
2. **A trigger is committed:** a reversal before the execution quote doesn't cancel it.
3. **An unfilled exit is retried at every later execution quote until expiration,** then settled by EXP-1.

## Decision

### Who and what
- **Positions:** a long call or a long put from FAC-1, MKTV-1, HVOL-1 or RAND-L1, entered under FILL-1 at the execution quote, sized by SIZE-1. MKT-1 v4 and RAND-1 v4 keep HOLD-1.
- **The entry boundary (EVAL-2 §1):** more than 7 and at most 60 calendar days to expiry, counted from the decision session's ET date. An entry already inside 7 days isn't eligible.

### The triggers, checked on each decision board after the entry fills
- **Checked:** on each SNAPTIMES-1 decision board (10:00 and 15:45 ET, or 12:45 on a half day), starting with the first decision board after the entry's execution fill.
  - An OPEN entry is first checked on the same day's CLOSE board; a CLOSE entry on the next session's OPEN board.
- **The profit target (Josh):** it fires when **bid value − exit fees ≥ 2 × entry cost**.
  - Bid value is the decision board's bid × quantity × multiplier.
  - Exit fees are FEES-1's fees for selling that quantity.
  - Entry cost is the actual entry fill's premium plus its fees.
  - A target exit therefore nets at least +100% on capital at risk. A board with no bid for the contract can't fire the target.
- **The time stop:** it fires at the **first decision board with 7 or fewer calendar days to expiry**, counted from the board's ET session date, whatever the price.
  - A weekend or holiday doesn't delay it: the first board on or after the 7-day line fires it.
- **Priority:** if both fire on one board, it's recorded as a target exit. Both sell the same way.

### Selling (Josh: committed; retried until expiration)
- **A fired trigger is committed:** the position sells at that slot's execution quote under FILL-1 (all-or-none at the bid, displayed size).
  - It isn't re-checked at execution: a reversal before the execution quote doesn't cancel it.
- **Unfilled:** no bid, or too little displayed size, or an execution gap. The exit is retried at **every later execution quote**, still committed, until it fills.
  - If the option expires unsold, EXP-1 settles it at the official close, as for any held option.
- **Long options can't be assigned.** ASSIGN-1 applies only to short legs, so it never acts on an EXIT-1 position.
- **Records:** the trigger (target or time stop), the board it fired on, each execution attempt and its outcome, and the fill are journal events with their evidence, as FILL-1's are today.

### On simulated paths (B06, P7, P8)
- **Nodes:** paths step at the same decision times. A trigger seen at node t sells at **node t + 1's price**, standing in for the execution delay. The trigger rule is the same as live, with the modelled bid (P7: the modelled mid minus the decision board's relative half-spread for that contract) in place of the quoted bid.
- **Fills:** a simulated exit always fills at the next node's modelled bid. Path retries aren't needed.
- **Settlement:** a path that reaches the expiration close unsold settles at S_T (EVAL-2 §7.1's close step).
  - With the time stop at 7 days and entries at more than 7, a path reaches expiry only if no node fired. That is impossible: the 7-day node always fires. So **every path exits by the target or the time stop**, matching live except for unfilled exits.
- **The return:** (exit proceeds − exit fees − entry cost) ÷ capital at risk, per path. EVAL-1's 15 quantile levels and bins are taken over the 2,000 paths.

### Not in this DR
- **The path generators** (DR-0049 for MKTV-1; the R2.5 path DR for FAC-1 and HVOL-1), and SIZE-1, FILL-1 and FEES-1 themselves, which are unchanged.

## Consequences
- **One rule, live and simulated:** the triggers, the committed sale, the target's fee basis and the cadence are identical. Only the fill differs: real quotes and retries live, the modelled bid on paths (P7).
- **Every EXIT-1 trade closes within about 7 days of its last eligible check, or at the first quote that fills after.** A trade reaches EXP-1 only if its exit never fills.
- **Tests (brief §5, B06):**
  - a target hit;
  - a time stop;
  - neither, with an unfilled exit through to EXP-1;
  - a crossing between observations (no trigger until a board shows it);
  - a reversal before execution (still sold);
  - a weekend or holiday time stop (fires on the first board on or after the line);
  - an unfilled first exit retried at the next quote;
  - the same cases on paths.
