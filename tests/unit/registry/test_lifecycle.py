"""Journal order rules for one trade (DR-0028)."""

from __future__ import annotations

import pytest

from remy_finance.registry import EventKind, JournalEvent, LifecycleError, check_next
from tests.unit.registry.helpers import event, recommendation

E = EventKind
REC = recommendation()


def run(*kinds: EventKind) -> list[JournalEvent]:
    history: list[JournalEvent] = []
    for i, k in enumerate(kinds):
        note = "n" if k in (E.ANNOTATION, E.CORRECTION) else ""
        ev = event(REC, k, minutes=5 + i, note=note, corrects_seq=1 if k is E.CORRECTION else None)
        check_next(history, ev)
        history.append(ev)
    return history


@pytest.mark.parametrize(
    "path",
    [
        (E.ORDER_SUBMITTED, E.ORDER_UNFILLED, E.TRADE_CLOSED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.LEG_FILLED, E.EXPIRATION_SETTLED, E.TRADE_CLOSED),
        (
            E.ORDER_SUBMITTED,
            E.LEG_FILLED,
            E.EXIT_SUBMITTED,
            E.EXIT_UNFILLED,
            E.EXIT_SUBMITTED,
            E.EXIT_FILLED,
            E.TRADE_CLOSED,
        ),
        (
            E.ORDER_SUBMITTED,
            E.LEG_FILLED,
            E.ASSIGNMENT_ASSUMED,
            E.EXIT_SUBMITTED,
            E.EXIT_FILLED,
            E.TRADE_CLOSED,
            E.ANNOTATION,
            E.CORRECTION,
        ),
    ],
)
def test_allowed_paths(path: tuple[EventKind, ...]) -> None:
    run(*path)


@pytest.mark.parametrize(
    "path",
    [
        (E.LEG_FILLED,),
        (E.ORDER_SUBMITTED, E.ORDER_SUBMITTED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.ORDER_UNFILLED),  # all-or-none
        (E.ORDER_SUBMITTED, E.ORDER_UNFILLED, E.LEG_FILLED),
        (E.ORDER_SUBMITTED, E.ORDER_UNFILLED, E.EXIT_SUBMITTED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXIT_FILLED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXIT_SUBMITTED, E.EXIT_SUBMITTED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.TRADE_CLOSED),  # still open
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXIT_SUBMITTED, E.TRADE_CLOSED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXPIRATION_SETTLED, E.EXIT_SUBMITTED),
        (E.ORDER_SUBMITTED, E.ORDER_UNFILLED, E.TRADE_CLOSED, E.ORDER_SUBMITTED),
        (E.ORDER_SUBMITTED, E.LEG_FILLED, E.EXIT_SUBMITTED, E.EXIT_FILLED, E.EXIT_SUBMITTED),
    ],
)
def test_refused_paths(path: tuple[EventKind, ...]) -> None:
    with pytest.raises(LifecycleError):
        run(*path)


def test_time_cannot_go_backwards() -> None:
    history = run(E.ORDER_SUBMITTED)
    late = event(REC, E.ORDER_UNFILLED, minutes=1)
    with pytest.raises(LifecycleError, match="before"):
        check_next(history, late)
