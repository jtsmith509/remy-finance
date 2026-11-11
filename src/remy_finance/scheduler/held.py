"""Held names on captured boards (DR-0039, Codex R1.6 F02).

A board covers the UNIV-1 members in force *plus* every underlying with an open position,
so a position whose name has left the universe is still marked, managed, assigned and
force-closed. Names on the board only because they are held are listed in the board's
provenance (``held_only``), and recommendations never use them: candidates are restricted
to universe members.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from remy_finance.core import MarketState, Provenance
from remy_finance.execution.engine import PaperEngine
from remy_finance.registry import Registry

HELD_ONLY = "held_only"


def held_underlyings(registry: Registry, engine: PaperEngine, day: date) -> frozenset[str]:
    """Underlyings of every position still open on ``day`` (unexpired, not closed)."""
    names: set[str] = set()
    for rec in registry.recommendations():
        if rec.expiration < day:
            continue
        if engine.position(rec.trade_id).is_open:
            names.add(rec.candidate.legs[0].contract.underlying)
    return frozenset(names)


def board_symbols(members: Sequence[str], held: Iterable[str]) -> tuple[list[str], tuple[str, ...]]:
    """(symbols to capture, the held-only names among them)."""
    member_set = set(members)
    extra = tuple(sorted(set(held) - member_set))
    return [*members, *extra], extra


def held_provenance(held_only: Sequence[str]) -> tuple[Provenance, ...]:
    if not held_only:
        return ()
    return (Provenance(source="scheduler", details=((HELD_ONLY, ",".join(held_only)),)),)


def held_only(board: MarketState) -> frozenset[str]:
    """Names on ``board`` only because a position is open in them."""
    out: set[str] = set()
    for prov in board.provenance:
        for key, value in prov.details:
            if key == HELD_ONLY and value:
                out.update(value.split(","))
    return frozenset(out)
