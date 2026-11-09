# DR-0029: Paper fill engine details (R1.2–R1.3)

- **Status:** proposed
- **Date:** 2026-11-09
- **Source:** handoff §6 R1.2–R1.3; DR-0003 (execution quote at +5 min), DR-0009 (ASSIGN-1, stock always fillable), DR-0010 (EXP-1), DR-0028 (journal order rules)
- **Stage:** R1.2

## Context
The brief fixes FILL-1 (cross the spread, full displayed size, all-or-none, single entry attempt) and says exits retry at every later snapshot's execution quote until expiration, when EXP-1 applies. HOLD-1 has no discretionary exits except forced closes from ASSIGN-1. Several mechanics are left open; the engine is `src/remy_finance/execution/engine.py`.

## Options considered and decisions

1. **The journal is the only state.** A trade's position is rebuilt from its events each time; no other state is kept between runs. Re-running a step for an already-processed board writes nothing, and a crash between `EXIT_SUBMITTED` and its fill resumes from the same board.
2. **Which execution board.**
   - The execution board must have `as_of` in [decision + 5 min, decision + 15 min], where 15 is the 5-minute offset plus the 10-minute prospective window (DR-0022). Anything else is refused as the wrong board.
   - A missing execution snapshot (a gap) means **unfilled**: entry is then over (single attempt), and an exit retries at the next snapshot.
3. **How a filled entry is written.**
   - Options: one event for the whole order, or one `LEG_FILLED` per leg.
   - **Decision: one per leg**, as the event name implies. Each carries its `LegFill` and its own cash (premium minus that leg's FEES-1 fee). The legs' cash sums exactly to the order's net cash, which the engine checks.
   - An unfilled entry writes `ORDER_UNFILLED` (with the `OrderFill` showing which legs failed and why), then `TRADE_CLOSED`.
4. **Assumed assignment.**
   - ASSIGN-1 runs on each open short leg at each decision snapshot, from the **decision board only**, with S = underlying mid (DR-0019). An assigned leg is assigned **in full** (all its contracts), which is conservative.
   - `ASSIGNMENT_ASSUMED` is written at that snapshot. It carries the strike cash now, and the stock goes into the position.
   - If the decision board has no two-sided underlying quote, ASSIGN-1 can't be evaluated. An `ANNOTATION` records this, and no assignment is assumed (see question 2).
   - ASSIGN-1 is **not** applied at the expiration day's close snapshot. Anything after it is EXP-1's job.
5. **Forced close after assignment** (at every snapshot after the assignment, until flat):
   - `EXIT_SUBMITTED`, then one result event at that slot's execution quote.
   - **Stock always closes** (DR-0009) by crossing the underlying spread. Dividends going ex on any session after the assignment snapshot, up to and including the close date, are owed on short stock. They come from dividends known on the execution board.
   - The **remaining option legs close all-or-none**. If they fill, the event is `EXIT_FILLED` and the trade closes. If not, it's `EXIT_UNFILLED`, which still carries the stock-close cash (a partial exit; spec §11 asks for partial sales to be recorded), and the options retry at the next snapshot.
   - Rejected alternative: keep the stock until the options also fill. That ties a liquid close to an illiquid one and needs dividend tracking over an open-ended period.
6. **Expiration.**
   - `settle` applies EXP-1 to the remaining legs at the official close (a new `OfficialClose` record). Any stock still open, possible only if no underlying quote existed at every attempt, is closed at the official close.
   - The event time is the close (16:00 ET); `public_time` is the vendor's publication time.
7. **Trade summary.** `TRADE_CLOSED` carries realized P&L (the sum of every event's cash, exact), capital at risk, realized return on capital at risk and its deviation from the predicted mean. The two returns are ratios, not cash, so they are rounded to 12 decimal places.
8. **Exit-policy interface.**
   - `ExitPolicy` has `policy_id`, `closing_trade_required` and `wants_exit(position, decision_board)`.
   - `HOLD-1` always returns False.
   - A discretionary exit uses the same exit path, with `forced = False`. R4's least-squares Monte Carlo policy plugs in here.

## Consequences
- Every number in the journal can be traced to a stored board and a typed evidence record. `registry verify` passes after every tested path.
- **Known limitations:**
  - Assignment fees are $0 under FEES-1.
  - A stock position still open at expiration owes no dividend in the settlement step.
  - Partial assignment of a leg isn't modelled.
  - Official closes don't yet have a data-adapter read path; that comes with the scheduler (R1.6).

## Questions for Josh
1. **Forced close (§5):** is closing the stock straight away while the options retry the right reading of DR-0009?
2. **Missing underlying quote (§4):** annotate and skip, or assume conservatively that every in-the-money-looking short leg is assigned? Without a spot price, "in the money" can't be determined, so skipping is the only well-defined choice.
