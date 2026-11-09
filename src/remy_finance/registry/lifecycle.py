"""Allowed journal sequences for one trade (DR-0028).

The journal is append-only, so the order of events is checked when each one is appended
(and again by ``registry verify``). An event that doesn't fit the trade's history is
refused instead of being recorded and later explained away.

    ORDER_SUBMITTED  (once, first)
      -> LEG_FILLED+                (all-or-none: never mixed with ORDER_UNFILLED)
           -> EXIT_SUBMITTED -> EXIT_FILLED | EXIT_UNFILLED   (repeatable while open)
           -> ASSIGNMENT_ASSUMED, EXPIRATION_SETTLED
      -> ORDER_UNFILLED (once)
      -> TRADE_CLOSED (once; afterwards only ANNOTATION and CORRECTION)

ANNOTATION and CORRECTION are allowed at any time and don't change the state. Lifecycle
event times may not go backwards.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from remy_finance.registry.records import EventKind, JournalEvent

E = EventKind
NOTES = frozenset({E.ANNOTATION, E.CORRECTION})


class LifecycleError(ValueError):
    """A journal event that does not fit the trade's history."""


def check_next(history: Sequence[JournalEvent], event: JournalEvent) -> None:
    """Raise ``LifecycleError`` unless ``event`` may follow ``history`` (same trade)."""
    if event.kind in NOTES:
        return
    kinds = [h.kind for h in history if h.kind not in NOTES]
    last_time: datetime | None = max(
        (h.event_time for h in history if h.kind not in NOTES), default=None
    )
    if last_time is not None and event.event_time < last_time:
        raise LifecycleError(f"{event.kind.value} at {event.event_time} is before {last_time}")

    def need(cond: bool, why: str) -> None:
        if not cond:
            raise LifecycleError(f"{event.kind.value} not allowed: {why}")

    need(E.TRADE_CLOSED not in kinds, "the trade is closed")
    submitted = E.ORDER_SUBMITTED in kinds
    filled = E.LEG_FILLED in kinds
    unfilled = E.ORDER_UNFILLED in kinds
    exits = [k for k in kinds if k in (E.EXIT_SUBMITTED, E.EXIT_FILLED, E.EXIT_UNFILLED)]
    exit_pending = bool(exits) and exits[-1] is E.EXIT_SUBMITTED
    exit_done = E.EXIT_FILLED in kinds
    settled = E.EXPIRATION_SETTLED in kinds

    match event.kind:
        case E.ORDER_SUBMITTED:
            need(not kinds, "an order is submitted once, before anything else")
        case E.LEG_FILLED:
            need(submitted and not unfilled, "needs a submitted, not-unfilled order")
            need(kinds[-1] in (E.ORDER_SUBMITTED, E.LEG_FILLED), "legs fill together at entry")
        case E.ORDER_UNFILLED:
            need(submitted and not filled and not unfilled, "all-or-none: no leg may have filled")
        case E.EXIT_SUBMITTED:
            need(filled, "no open position")
            need(not exit_pending, "an exit is already pending")
            need(not exit_done and not settled, "the position is already closed out")
        case E.EXIT_FILLED | E.EXIT_UNFILLED:
            need(exit_pending, "no pending exit")
        case E.ASSIGNMENT_ASSUMED:
            need(filled and not exit_done and not settled, "no open position")
        case E.EXPIRATION_SETTLED:
            need(filled and not exit_done and not settled, "no open position")
            need(not exit_pending, "an exit is pending")
        case E.TRADE_CLOSED:
            need(submitted, "nothing to close")
            need(not exit_pending, "an exit is pending")
            need(unfilled or exit_done or settled, "the position is still open")
        case _:  # pragma: no cover - every kind is handled above
            raise LifecycleError(f"unknown kind {event.kind}")
