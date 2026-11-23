"""Capture coverage for outcomes (EVAL-2 §6.3, DR-0046).

- **Boards:** names that have an open shadow-panel position are captured as
  **tracked-only**. They are listed in the board provenance (``tracked_only``) and are
  never eligible or recommended, like DR-0039's held-only names. The shadow panel is R2
  engine work, so until it exists the set of board-tracked names is empty; the mechanism
  is in place and tested.
- **Nightly:** CLOSE-1 and HIST-1 also store the close and ATM IV of every name with an
  open position or an unexpired forecast target (the tick's nightly step), so outcomes are
  captured even after a name leaves the universe.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from remy_finance.core import MarketState, Mode, Provenance
from remy_finance.scheduler.held import HELD_ONLY, held_only
from remy_finance.scheduler.runlog import RunLogStore

TRACKED_ONLY = "tracked_only"
# MKT-1 fits expirations up to 120 days out (and DR-0028's ladder); scanning a year of run
# logs covers every target still unexpired.
TARGET_LOOKBACK = timedelta(days=400)


@dataclass(frozen=True)
class BoardPlan:
    symbols: list[str]  # members first, then held-only, then tracked-only (each sorted)
    held_only: tuple[str, ...]
    tracked_only: tuple[str, ...]


def board_plan(members: Sequence[str], held: Iterable[str], tracked: Iterable[str]) -> BoardPlan:
    member_set = set(members)
    held_extra = tuple(sorted(set(held) - member_set))
    tracked_extra = tuple(sorted(set(tracked) - member_set - set(held_extra)))
    return BoardPlan([*members, *held_extra, *tracked_extra], held_extra, tracked_extra)


def plan_provenance(plan: BoardPlan) -> tuple[Provenance, ...]:
    details = [(HELD_ONLY, ",".join(plan.held_only))] if plan.held_only else []
    if plan.tracked_only:
        details.append((TRACKED_ONLY, ",".join(plan.tracked_only)))
    return (Provenance(source="scheduler", details=tuple(details)),) if details else ()


def tracked_only(board: MarketState) -> frozenset[str]:
    """Names on ``board`` only because an outcome there is being tracked."""
    out: set[str] = set()
    for prov in board.provenance:
        for key, value in prov.details:
            if key == TRACKED_ONLY and value:
                out.update(value.split(","))
    return frozenset(out)


def not_recommendable(board: MarketState) -> frozenset[str]:
    """Names on ``board`` that recommendations never use: held-only and tracked-only."""
    return held_only(board) | tracked_only(board)


def forecast_targets(runs: RunLogStore, mode: Mode, day: date) -> frozenset[str]:
    """Underlyings with an unexpired S_T/F target (a fitted expiration on or after ``day``)
    in the run logs of the last ``TARGET_LOOKBACK``."""
    base = runs.root / mode.value.lower()
    if not base.exists():
        return frozenset()
    first = (day - TARGET_LOOKBACK).isoformat()
    names: set[str] = set()
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name < first:
            continue
        for path in sorted(folder.glob("*.json")):
            run = runs.load(path)
            names.update(f.symbol for f in run.fits if f.ok and f.expiration >= day)
    return frozenset(names)
