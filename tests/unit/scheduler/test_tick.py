"""The prospective tick (R1.6): simulated trading days with a fake clock and capture."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import MarketState, Mode, OfficialClose, Session
from remy_finance.data import FixtureSource
from remy_finance.data.timeutil import et_at, session_close
from remy_finance.registry import EventKind, Registry, registry_path
from remy_finance.scheduler.ops import DirtyTreeError
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.scheduler.tick import Tick
from remy_finance.snapshots.schedule import SnapshotKind
from remy_finance.snapshots.store import SnapshotStore
from tests.unit.baselines import synthetic as S

MON = date(2026, 11, 9)


@dataclass
class World:
    """A fake clock, capture function and notification sink."""

    now: datetime
    fetch_seconds: int = 40
    fail_capture: bool = False
    dirty: bool = False
    notes: list[tuple[str, str]] = field(default_factory=list)
    closes: FixtureSource = field(default_factory=FixtureSource)

    def clock(self) -> datetime:
        return self.now

    def capture(self, session: Session, held: frozenset[str] = frozenset()) -> MarketState:
        if self.fail_capture:
            raise ConnectionError("Theta Terminal unreachable")
        self.now += timedelta(seconds=self.fetch_seconds)  # as_of is stamped after the fetch
        return S.chain(half_spread="0.02", tick="0.01").model_copy(
            update={"as_of": self.now, "mode": Mode.PROSPECTIVE, "session": session}
        )

    def commit(self) -> str:
        if self.dirty:
            raise DirtyTreeError("uncommitted changes")
        return "0123456789abcdef0123456789abcdef01234567"

    def notify(self, title: str, message: str) -> None:
        self.notes.append((title, message))


def tick(world: World, data: Path, at: datetime, **kw: object) -> list[str]:
    world.now = at
    t = Tick(
        data_dir=data,
        capture=world.capture,
        closes=world.closes,
        clock=world.clock,
        commit=world.commit,
        notify=world.notify,
        **kw,  # type: ignore[arg-type]
    )
    return t.run()


def registry(data: Path) -> Registry:
    return Registry.open(registry_path(data, Mode.PROSPECTIVE))


def run_day(world: World, data: Path, day: date, **kw: object) -> list[str]:
    log: list[str] = []
    for hh, mm, ss in (
        (9, 59, 0),
        (10, 0, 20),
        (10, 3, 0),
        (10, 5, 30),
        (10, 6, 40),
        (15, 45, 10),
        (15, 50, 50),
        (15, 52, 0),
        (17, 31, 0),
    ):
        log += tick(world, data, et_at(day, hh, mm) + timedelta(seconds=ss), **kw)
    return log


def test_a_full_trading_day(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    log = run_day(world, tmp_path, MON)
    store = SnapshotStore(tmp_path / "snapshots")
    for session in (Session.OPEN, Session.CLOSE):
        for kind in (SnapshotKind.DECISION, SnapshotKind.EXECUTION):
            assert store.find(Mode.PROSPECTIVE, MON, session, kind) is not None, (session, kind)
    with registry(tmp_path) as reg:
        recs = reg.recommendations()
        assert {r.engine for r in recs} == {"MKT-1", "RAND-1"}
        assert len(recs) == 2  # one underlying: the per-day cap leaves nothing at the close
        for r in recs:
            kinds = [e.kind for _, e in reg.events(r.trade_id)]
            assert kinds[0] is EventKind.ORDER_SUBMITTED and len(kinds) >= 2
        assert reg.verify().ok
    runs = RunLogStore(tmp_path / "runs").all(Mode.PROSPECTIVE)
    assert len(runs) == 4  # 2 baselines x 2 sessions; the close ones are null decisions
    assert sum(1 for r in runs if not r.selected) == 2
    assert any(a.startswith("backup") for a in log)
    assert not world.notes


def test_execution_waits_five_minutes_after_the_actual_decision(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0), fetch_seconds=150)  # decision as_of 10:02:30
    tick(world, tmp_path, et_at(MON, 10, 0))
    tick(world, tmp_path, et_at(MON, 10, 6))  # 10:06 < 10:07:30: too early
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.EXECUTION) is None
    tick(world, tmp_path, et_at(MON, 10, 8))
    path = store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.EXECUTION)
    assert path is not None
    assert store.load(path).as_of >= et_at(MON, 10, 2) + timedelta(seconds=30 + 300)


def test_rerunning_a_tick_writes_nothing_new(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    with registry(tmp_path) as reg:
        before = (reg.head("recommendations"), reg.head("journal"))
    run_day(world, tmp_path, MON)
    with registry(tmp_path) as reg:
        assert (reg.head("recommendations"), reg.head("journal")) == before


def test_a_missed_window_records_gaps_and_no_trades(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 10, 30))
    log = tick(world, tmp_path, et_at(MON, 10, 30))
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.has_gap(MON, Session.OPEN, SnapshotKind.DECISION)
    assert store.has_gap(MON, Session.OPEN, SnapshotKind.EXECUTION)
    assert any("gap" in a for a in log)
    with registry(tmp_path) as reg:
        assert reg.recommendations() == []


def test_capture_failure_is_a_gap_a_record_and_a_notification(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0), fail_capture=True)
    tick(world, tmp_path, et_at(MON, 10, 1))
    assert SnapshotStore(tmp_path / "snapshots").has_gap(MON, Session.OPEN, SnapshotKind.DECISION)
    assert world.notes and "capture failed" in world.notes[0][1]
    assert list((tmp_path / "logs" / "failures").glob("*capture*.json"))


def test_dirty_tree_skips_recommendations_but_not_management(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)  # opens two positions
    world.dirty = True
    tue = MON + timedelta(days=1)
    log = run_day(world, tmp_path, tue)
    assert any("skipped (dirty tree)" in a for a in log)
    with registry(tmp_path) as reg:
        assert len(reg.recommendations()) == 2  # nothing new on Tuesday
    assert any("uncommitted" in m for _, m in world.notes)
    assert any("position events" in a for a in log)


def test_overlapping_tick_exits(tmp_path: Path) -> None:
    import fcntl

    world = World(now=et_at(MON, 9, 0))
    lock = tmp_path / "scheduler" / "tick.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        assert tick(world, tmp_path, et_at(MON, 10, 1)) == ["another tick is running"]


def test_expiration_is_settled_once_the_official_close_is_public(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    t = session_close(S.EXPIRY)
    world.closes.add(
        OfficialClose(
            symbol="XYZ",
            session_date=S.EXPIRY,
            price=Decimal("101.37"),
            event_time=t,
            public_time=et_at(S.EXPIRY, 17, 15),
            source="fixture",
        )
    )
    tick(world, tmp_path, et_at(S.EXPIRY, 17, 0))  # not public yet
    with registry(tmp_path) as reg:
        open_before = [
            r
            for r in reg.recommendations()
            if reg.events(r.trade_id)[-1][1].kind is not EventKind.TRADE_CLOSED
        ]
    tick(world, tmp_path, et_at(S.EXPIRY, 17, 20))
    with registry(tmp_path) as reg:
        for r in open_before:
            assert reg.events(r.trade_id)[-1][1].kind is EventKind.TRADE_CLOSED
        assert reg.verify().ok


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_nightly_export_is_committed_and_pushed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }.items():
        monkeypatch.setenv(k, v)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "registry-export", str(remote))
    checkout = tmp_path / "export"
    _git(tmp_path, "clone", str(remote), str(checkout))
    _git(checkout, "checkout", "-B", "registry-export")
    (checkout / "README.md").write_text("registry export\n")
    _git(checkout, "add", "README.md")
    _git(checkout, "commit", "-m", "init")
    _git(checkout, "push", "-u", "origin", "registry-export")

    world = World(now=et_at(MON, 9, 0))
    data = tmp_path / "data"
    log = run_day(world, data, MON, export_repo=checkout)
    assert any(a.startswith("export 2026-11-09") for a in log), log
    exported = checkout / "prospective" / "2026" / "11" / "09.jsonl"
    assert exported.exists()
    assert _git(remote, "log", "--oneline", "registry-export").count("\n") == 1  # init + export
    with registry(data) as reg:
        assert reg.verify(export_dir=checkout).ok


def test_weekend_tick_only_does_nightly_work_for_friday(tmp_path: Path) -> None:
    fri = date(2026, 11, 13)
    world = World(now=et_at(fri, 9, 0))
    log = tick(world, tmp_path, datetime(2026, 11, 14, 15, 0, tzinfo=UTC))  # Saturday
    assert any(a.startswith("backup 2026-11-13") for a in log)
    assert SnapshotStore(tmp_path / "snapshots").gaps() == []
