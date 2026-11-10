"""Replay driver: the R1 pipeline over a historical date range (handoff R1.8, DR-0034).

For every session of the decision range, OPEN then CLOSE:
  decision board (built or reused) -> recommendations -> execution board (decision + 5 min)
  -> fills -> position management; after the CLOSE session, settlement of expirations
  whose official close is public by 17:30 ET.

After the range, a **tail** keeps managing and settling the positions until they are all
closed (or the data runs out). A tail day builds boards only if some open position can
still produce an event from a board: an open short leg (ASSIGN-1), assignment stock, or a
pending exit. Long-only HOLD-1 positions just wait for EXP-1 settlement.

Everything goes to the REPLAY registry and snapshot store; nothing here is evidence of
edge. Times are simulated: a recommendation is received at decision + 30 s, and fills and
management are recorded just after the execution board; settlement at 17:30 ET.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from remy_finance.core import MarketState, Mode, Session, Side
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.data.timeutil import et_at, next_session
from remy_finance.registry import Registry, registry_path
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.snapshots.schedule import SnapshotKind, is_session
from remy_finance.snapshots.store import SnapshotStore
from remy_finance.universe.selection import UniverseStore

RECEIPT_DELAY = timedelta(seconds=30)
RECORD_DELAY = timedelta(seconds=60)
SETTLE_HHMM = (17, 30)

# (session date, session, kind, symbols, universe version) -> a REPLAY board
BoardBuilder = Callable[[date, Session, SnapshotKind, Sequence[str], str], MarketState]


@dataclass
class ReplaySummary:
    sessions: int = 0
    tail_sessions: int = 0
    tail_sessions_skipped: int = 0
    boards_built: int = 0
    boards_reused: int = 0
    recommended: int = 0
    settled: int = 0
    still_open: int = 0
    stopped_at: date | None = None
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class ReplayDriver:
    data_dir: Path
    build: BoardBuilder
    closes: OfficialCloseSource
    code_commit: str
    log: Callable[[str], None] = print
    last_day: date | None = None  # stop the tail here (the last day with data)

    def _board(
        self,
        store: SnapshotStore,
        d: date,
        session: Session,
        kind: SnapshotKind,
        decision: MarketState | None,
        summary: ReplaySummary,
    ) -> MarketState:
        found = store.find(Mode.REPLAY, d, session, kind)
        if found is not None:
            summary.boards_reused += 1
            return store.load(found)
        membership = UniverseStore(self.data_dir / "universe").membership_for(d)
        if membership is None:
            raise RuntimeError(
                f"no UNIV-1 membership in force on {d}: run `remy universe build --month "
                f"{d:%Y-%m}` (or the month before) first"
            )
        t0 = time.perf_counter()
        board = self.build(d, session, kind, list(membership.members), membership.version_label)
        store.write(
            board,
            kind=kind,
            decision_snapshot_id=None if decision is None else decision.snapshot_id,
        )
        summary.boards_built += 1
        self.log(
            f"{d} {session.value} {kind.value}: built {board.snapshot_id[:16]} "
            f"({len(board.option_quotes)} quotes, {time.perf_counter() - t0:.0f}s)"
        )
        return board

    def _needs_boards(self, p: Pipeline, d: date) -> bool:
        for rec in p.registry.recommendations():
            if rec.expiration < d:
                continue
            pos = p.engine.position(rec.trade_id)
            if not pos.is_open:
                continue
            if pos.stock_shares or pos.exit_pending_since is not None or pos.assigned_at:
                return True
            if any(leg.side is Side.SHORT for leg in pos.open_legs):
                return True
            if rec.exit_policy_id != "HOLD-1":
                return True
        return False

    def _open_count(self, p: Pipeline) -> int:
        return sum(1 for r in p.registry.recommendations() if p.engine.position(r.trade_id).is_open)

    def run(self, start: date, end: date, *, tail: bool = True) -> ReplaySummary:
        if end < start:
            raise ValueError("end before start")
        t_start = time.perf_counter()
        summary = ReplaySummary()
        store = SnapshotStore(self.data_dir / "snapshots")
        reg = Registry.open_or_create(registry_path(self.data_dir, Mode.REPLAY), Mode.REPLAY)
        try:
            p = Pipeline(
                reg, RunLogStore(self.data_dir / "runs"), self.code_commit, lambda: _never()
            )
            d = start if is_session(start) else next_session(start)
            while True:
                in_range = d <= end
                if not in_range:
                    if not tail or self._open_count(p) == 0:
                        break
                    if self.last_day is not None and d > self.last_day:
                        summary.notes.append(f"tail stopped: no data after {self.last_day}")
                        break
                self._day(p, store, d, in_range, summary)
                summary.stopped_at = d
                d = next_session(d)
            summary.still_open = self._open_count(p)
            ok = reg.verify()
            if not ok.ok:
                raise RuntimeError(f"replay registry failed verification: {ok.problems[:3]}")
        finally:
            reg.close()
        summary.seconds = time.perf_counter() - t_start
        return summary

    def _day(
        self, p: Pipeline, store: SnapshotStore, d: date, in_range: bool, summary: ReplaySummary
    ) -> None:
        if in_range:
            summary.sessions += 1
        else:
            summary.tail_sessions += 1
        boards = in_range or self._needs_boards(p, d)
        if not boards:
            summary.tail_sessions_skipped += 1
        for session in (Session.OPEN, Session.CLOSE):
            if not boards:
                break
            decision = self._board(store, d, session, SnapshotKind.DECISION, None, summary)
            if in_range:
                p.clock = _fixed(decision.as_of + RECEIPT_DELAY)
                t0 = time.perf_counter()
                picks = p.recommend(decision)
                summary.recommended += len(picks)
                for line in p.timings:
                    self.log(f"{line}; recommend total {time.perf_counter() - t0:.0f}s")
                p.timings.clear()
            execution = self._board(store, d, session, SnapshotKind.EXECUTION, decision, summary)
            p.clock = _fixed(execution.as_of + RECORD_DELAY)
            p.fill_entries(decision, execution)
            p.manage(decision, execution)
        settle_at = et_at(d, *SETTLE_HHMM)
        p.clock = _fixed(settle_at)
        settled, waiting = p.settle_expired(self.closes, settle_at)
        summary.settled += settled
        if waiting:
            self.log(f"{d}: {waiting} expired trades without an official close yet")


def _fixed(t: datetime) -> Callable[[], datetime]:
    return lambda: t


def _never() -> datetime:
    raise RuntimeError("the replay clock is set per step")
