"""The prospective scheduler: one idempotent ``tick``, run every minute by launchd (DR-0033).

Each tick works out from the XNYS calendar (in ET) what is due and does it:

1. For each session (OPEN, CLOSE) of today:
   - **decision snapshot**: captured in its window [t, t + 10 min]; if the window passed
     with no capture, a gap is recorded.
   - **recommendations**: MKT-1 and RAND-1 on the stored decision board; refused if the
     code tree has uncommitted changes (DirtyTreeError).
   - **execution snapshot**: captured from max(t + 5 min, decision as_of + 5 min) to the
     end of its window. A decision gap makes the execution slot a gap too.
   - **fills and management**, once the execution slot is resolved (stored or a gap).
2. **Expiration settlement** of any position whose official close is public.
3. After 17:30 ET on the latest session: the **nightly export** (pushed to the
   ``registry-export`` checkout, if configured) and the **backup**, once per session.

A lock makes overlapping ticks exit at once. Steps are idempotent, and daily markers only
save work. A failing step writes a failure record and a macOS notification, and the tick
carries on with the steps that don't depend on it.
"""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from remy_finance.core import MarketState, Mode, Session
from remy_finance.data.protocol import OfficialCloseSource
from remy_finance.data.timeutil import et_at, et_date, next_session, previous_session
from remy_finance.registry import Registry, registry_path
from remy_finance.registry.store import export_lock
from remy_finance.scheduler.ops import DirtyTreeError, Markers, push_export, record_failure
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.snapshots.schedule import (
    PROSPECTIVE_MAX_DELAY,
    SnapshotKind,
    is_session,
    snapshot_time,
)
from remy_finance.snapshots.store import (
    GapRecordedError,
    LateSnapshotError,
    SnapshotExistsError,
    SnapshotStore,
)

EXECUTION_DELAY = timedelta(minutes=5)
NIGHTLY_HHMM = (17, 30)


class Slot(StrEnum):
    PENDING = "PENDING"
    STORED = "STORED"
    GAP = "GAP"


@dataclass
class Tick:
    data_dir: Path
    # A prospective board (as_of stamped after the fetch) of the UNIV-1 members plus the
    # held names given (DR-0039).
    capture: Callable[[Session, frozenset[str]], MarketState]
    closes: OfficialCloseSource
    clock: Callable[[], datetime]
    commit: Callable[[], str]  # raises DirtyTreeError
    notify: Callable[[str, str], None]
    export_repo: Path | None = None
    push: Callable[[Path, date, int], str] = push_export
    actions: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ plumbing

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def log(self, msg: str) -> None:
        self.actions.append(msg)

    @contextmanager
    def guard(self, step: str) -> Iterator[None]:
        try:
            yield
        except Exception as e:  # recorded and surfaced; the tick continues
            path = record_failure(self.logs_dir, step, e, self.clock())
            self.notify("REMY scheduler", f"{step} failed: {type(e).__name__}: {e}")
            self.log(f"FAILED {step}: {type(e).__name__}: {e} ({path.name})")

    @contextmanager
    def lock(self) -> Iterator[bool]:
        path = self.data_dir / "scheduler" / "tick.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------ run

    def run(self) -> list[str]:
        with self.lock() as acquired:
            if not acquired:
                self.log("another tick is running")
                return self.actions
            self._run()
            beat = self.data_dir / "scheduler" / "last_tick"
            beat.write_text(self.clock().isoformat() + "\n", encoding="utf-8")
        return self.actions

    def _run(self) -> None:
        now = self.clock()
        today = et_date(now)
        self.store = SnapshotStore(self.data_dir / "snapshots")
        self.markers = Markers(self.data_dir / "scheduler" / "markers")
        path = registry_path(self.data_dir, Mode.PROSPECTIVE)
        with Registry.open_or_create(path, Mode.PROSPECTIVE) as registry:
            self.pipeline = Pipeline(registry, RunLogStore(self.data_dir / "runs"), "", self.clock)
            with self.guard("reconcile"):
                self._reconcile(today)
            if is_session(today):
                for session in (Session.OPEN, Session.CLOSE):
                    with self.guard(f"{session.value.lower()}-session"):
                        self._session(today, session)
            with self.guard("settle"):
                settled, waiting = self.pipeline.settle_expired(self.closes, self.clock())
                if settled or waiting:
                    self.log(f"settled {settled}; {waiting} waiting for an official close")
            self._nightly(today, registry)

    # ------------------------------------------------------------------ sessions

    def _slot(
        self,
        day: date,
        session: Session,
        kind: SnapshotKind,
        start: datetime,
        decision: MarketState | None = None,
    ) -> tuple[Slot, MarketState | None]:
        found = self.store.find(Mode.PROSPECTIVE, day, session, kind)
        if found is not None:
            return Slot.STORED, self.store.load(found)
        if self.store.has_gap(day, session, kind):
            return Slot.GAP, None
        end = snapshot_time(day, session, kind) + PROSPECTIVE_MAX_DELAY
        now = self.clock()
        label = f"{session.value} {kind.value}"
        if now > end or start > end:
            return self._gap(day, session, kind, f"missed: no capture in the window ending {end}")
        if now < start:
            return Slot.PENDING, None
        try:
            board = self.capture(session, self.pipeline.held(day))
            self.store.write(
                board,
                kind=kind,
                decision_snapshot_id=None if decision is None else decision.snapshot_id,
            )
        except (LateSnapshotError, GapRecordedError) as e:
            return self._gap(day, session, kind, f"missed: {e}")
        except Exception as e:
            record_failure(self.logs_dir, f"capture-{label}", e, self.clock())
            self.notify("REMY scheduler", f"{label} capture failed: {e}")
            return self._gap(day, session, kind, f"missed: capture failed: {type(e).__name__}")
        self.log(f"{label}: stored {board.snapshot_id[:16]} as_of {board.as_of.isoformat()}")
        return Slot.STORED, board

    def _gap(
        self, day: date, session: Session, kind: SnapshotKind, reason: str
    ) -> tuple[Slot, MarketState | None]:
        try:
            self.store.record_gap(day, session, kind, reason[:300], now=self.clock())
        except SnapshotExistsError:  # a concurrent capture won: use it
            found = self.store.find(Mode.PROSPECTIVE, day, session, kind)
            assert found is not None
            return Slot.STORED, self.store.load(found)
        self.log(f"{session.value} {kind.value}: gap ({reason})")
        return Slot.GAP, None

    def _reconcile(self, today: date) -> None:
        """Resolve the slots of earlier sessions missed while the scheduler was down
        (Codex R1.6 F05). A watermark file holds the first day not yet reconciled; it starts
        at the first tick's day. Every unresolved slot of an earlier session becomes a gap:
        nothing is captured or recommended late. Entries of a stored decision board are
        resolved against its execution slot (unfilled if that is a gap); positions are
        managed at the next live board."""
        path = self.data_dir / "scheduler" / "watermark"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(today.isoformat() + "\n", encoding="utf-8")
            return
        day = date.fromisoformat(path.read_text(encoding="utf-8").strip())
        while day < today:
            if is_session(day):
                for session in (Session.OPEN, Session.CLOSE):
                    self._late_session(day, session)
            day = next_session(day)
            path.write_text(min(day, today).isoformat() + "\n", encoding="utf-8")

    def _late_session(self, day: date, session: Session) -> None:
        key = session.value.lower()
        if self.markers.done(day, f"{key}-done"):
            return
        dec_state, decision = self._slot(
            day, session, SnapshotKind.DECISION, snapshot_time(day, session, SnapshotKind.DECISION)
        )
        if decision is not None and not self.markers.done(day, f"{key}-recommend"):
            self.markers.mark(day, f"{key}-recommend", "SKIPPED: session reconciled late")
        ex_state, execution = (
            self._slot(
                day,
                session,
                SnapshotKind.EXECUTION,
                snapshot_time(day, session, SnapshotKind.EXECUTION),
                decision,
            )
            if decision is not None
            else self._gap(
                day, session, SnapshotKind.EXECUTION, "missed: the decision slot is a gap"
            )
        )
        assert dec_state is not Slot.PENDING and ex_state is not Slot.PENDING  # past windows
        if decision is not None:
            filled = self.pipeline.fill_entries(decision, execution)
            if filled:
                self.log(f"{day} {session.value}: {filled} entry events (reconciled)")
        self.markers.mark(day, f"{key}-done", "reconciled late")

    def _session(self, day: date, session: Session) -> None:
        key = session.value.lower()
        if self.markers.done(day, f"{key}-done"):
            return
        dec_time = snapshot_time(day, session, SnapshotKind.DECISION)
        if self.clock() < dec_time:
            return
        dec_state, decision = self._slot(day, session, SnapshotKind.DECISION, dec_time)
        if dec_state is Slot.PENDING:
            return
        if decision is not None and not self.markers.done(day, f"{key}-recommend"):
            try:
                self.pipeline.code_commit = self.commit()
            except DirtyTreeError as e:
                # No recommendations from uncommitted code. The slot is skipped, not retried
                # later with a stale receipt time; positions are still managed below.
                record_failure(self.logs_dir, f"{key}-recommend", e, self.clock())
                self.notify("REMY scheduler", "recommendations skipped: uncommitted changes")
                self.markers.mark(day, f"{key}-recommend", "SKIPPED: uncommitted changes")
                self.log(f"{session.value}: recommendations skipped (dirty tree)")
            else:
                self._recommend(day, session, decision)

        exec_start = snapshot_time(day, session, SnapshotKind.EXECUTION)
        if decision is not None:
            exec_start = max(exec_start, decision.as_of + EXECUTION_DELAY)
            ex_state, execution = self._slot(
                day, session, SnapshotKind.EXECUTION, exec_start, decision
            )
        else:  # no decision: the execution slot is a gap too (record_gap is idempotent)
            ex_state, execution = self._gap(
                day, session, SnapshotKind.EXECUTION, "missed: the decision slot is a gap"
            )
        if ex_state is Slot.PENDING:
            return
        if decision is not None:
            filled = self.pipeline.fill_entries(decision, execution)
            managed = self.pipeline.manage(decision, execution)
            self._flush_timings()
            self.log(f"{session.value}: {filled} entry events, {managed} position events")
        self.markers.mark(day, f"{key}-done")

    def _recommend(self, day: date, session: Session, decision: MarketState) -> None:
        """Recommendations, guarded on their own (Codex R1.6 F03): a failing baseline is
        recorded and the slot is skipped, not retried later with a stale receipt time, and
        the execution capture, fills and management of the session still run. Picks
        already recorded before a failure are filled like any other."""
        key = session.value.lower()
        ok = False
        with self.guard(f"{key}-recommend"):
            picks = self.pipeline.recommend(decision)
            self.markers.mark(day, f"{key}-recommend", ",".join(picks))
            self.log(f"{session.value}: {len(picks)} recommendations")
            ok = True
        if not ok:
            self.markers.mark(day, f"{key}-recommend", "FAILED: see data/logs/failures")
        self._flush_timings()

    def _flush_timings(self) -> None:
        for line in self.pipeline.timings:
            self.log(line)
        self.pipeline.timings.clear()

    # ------------------------------------------------------------------ nightly

    def _nightly(self, today: date, registry: Registry) -> None:
        now = self.clock()
        day = today if is_session(today) else previous_session(today)
        if day == today and now < et_at(day, *NIGHTLY_HHMM):
            day = previous_session(today)
        if self.export_repo is not None and not self.markers.done(day, "export"):
            with self.guard("export"), export_lock(self.export_repo):
                # One lock across write, commit and push, so a concurrent `remy registry
                # export` cannot interleave; the marker is set only after the push.
                rows = registry.export(self.export_repo, day, lock=False)
                commit = self.push(self.export_repo, day, len(rows))
                self.markers.mark(day, "export", commit)
                self.log(f"export {day}: {len(rows)} rows, commit {commit[:12] or '-'}")
        if not self.markers.done(day, "backup"):
            with self.guard("backup"):
                dest = registry.backup(self.data_dir / "backup", now=now)
                self.markers.mark(day, "backup", dest.name)
                self.log(f"backup {day}: {dest.name}")
