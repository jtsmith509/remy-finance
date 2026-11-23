"""DR-0046: tracked-only names on boards, forecast targets, and the nightly HIST-1 step."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from remy_finance.core import MarketState, Mode, Provenance, Session
from remy_finance.data.timeutil import et_at
from remy_finance.history.build import BuildSummary, last_session_of_week, nightly_window
from remy_finance.history.closes import needed_closes
from remy_finance.registry import Registry, registry_path
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.scheduler.tracked import (
    TRACKED_ONLY,
    board_plan,
    forecast_targets,
    not_recommendable,
    plan_provenance,
    tracked_only,
)
from remy_finance.snapshots.schedule import SnapshotKind
from remy_finance.snapshots.store import SnapshotStore
from tests.unit.baselines import synthetic as S
from tests.unit.scheduler.test_tick import MON, World, run_day, tick

TUE = date(2026, 11, 10)


def test_board_plan_orders_and_dedupes() -> None:
    plan = board_plan(["AAA", "XYZ"], {"XYZ", "OLD"}, {"OLD", "TRK", "AAA"})
    assert plan.symbols == ["AAA", "XYZ", "OLD", "TRK"]
    assert (plan.held_only, plan.tracked_only) == (("OLD",), ("TRK",))
    (prov,) = plan_provenance(plan)
    assert dict(prov.details) == {"held_only": "OLD", TRACKED_ONLY: "TRK"}
    assert plan_provenance(board_plan(["AAA"], (), ())) == ()


def _board(extra: tuple[tuple[str, str], ...] = ()) -> MarketState:
    at = et_at(MON, 10, 0)
    b = S.chain(half_spread="0.02", tick="0.01").model_copy(
        update={"as_of": at, "mode": Mode.PROSPECTIVE, "session": Session.OPEN}
    )
    if extra:
        prov = Provenance(source="scheduler", details=extra)
        b = b.model_copy(update={"provenance": (*b.provenance, prov)})
    return b


def test_tracked_only_names_are_never_recommended(tmp_path: Path) -> None:
    tracked = _board(((TRACKED_ONLY, "XYZ"),))
    assert tracked_only(tracked) == {"XYZ"} and not_recommendable(tracked) == {"XYZ"}
    assert tracked_only(_board()) == frozenset()
    for brd, expect in ((_board(), True), (tracked, False)):
        data = tmp_path / str(expect)
        reg = Registry.create(registry_path(data, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
        try:
            at = brd.as_of + timedelta(seconds=30)
            pipe = Pipeline(reg, RunLogStore(data / "runs"), "0" * 40, lambda at=at: at)
            picks = pipe.recommend(brd)
            assert bool(picks) is expect
        finally:
            reg.close()


@dataclass
class TrackWorld(World):
    seen: list[tuple[frozenset[str], frozenset[str]]] = field(default_factory=list)
    hist_calls: list[tuple[date, frozenset[str]]] = field(default_factory=list)
    deadlines: list[datetime] = field(default_factory=list)
    hist_result: BuildSummary = field(default_factory=lambda: BuildSummary(symbols=10))

    def capture(
        self,
        session: Session,
        held: frozenset[str] = frozenset(),
        tracked: frozenset[str] = frozenset(),
    ) -> MarketState:
        self.seen.append((held, tracked))
        return super().capture(session, held, tracked)

    def hist(self, day: date, tracked: frozenset[str], deadline: datetime) -> BuildSummary:
        self.hist_calls.append((day, tracked))
        self.deadlines.append(deadline)
        return self.hist_result


def test_tick_passes_board_tracked_names_to_capture(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 10, 0))
    tick(world, tmp_path, et_at(MON, 10, 0), board_tracked=lambda d: frozenset({"TRK"}))
    assert world.seen and all(t == {"TRK"} for _, t in world.seen)


def test_nightly_runs_once_after_1730_with_tracked_targets(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON, hist_nightly=world.hist)  # last tick 17:31
    assert [d for d, _ in world.hist_calls] == [MON]
    (_, tracked) = world.hist_calls[0]
    assert "XYZ" in tracked  # the run logs' unexpired S_T/F targets (and the open position)
    tick(world, tmp_path, et_at(MON, 20, 0), hist_nightly=world.hist)
    assert len(world.hist_calls) == 1  # done for MON


def test_nightly_never_runs_near_session_hours(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 9, 0))
    for hh, mm in ((8, 0), (9, 59), (12, 0), (17, 29)):
        tick(world, tmp_path, et_at(TUE, hh, mm), hist_nightly=world.hist)
    assert world.hist_calls == []
    tick(world, tmp_path, et_at(TUE, 6, 50), hist_nightly=world.hist)  # < 1 h to 07:45
    assert world.hist_calls == []
    tick(world, tmp_path, et_at(TUE, 6, 40), hist_nightly=world.hist)
    assert [d for d, _ in world.hist_calls] == [MON]
    assert world.deadlines == [et_at(TUE, 7, 45)]  # stops before TUE's quiet hours


def test_nightly_retries_hourly_then_gives_up(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 17, 31))
    world.hist_result = BuildSummary(symbols=10, fetch_errors=["yields: 503"])
    times = [(17, 31), (18, 0), (18, 31), (19, 32), (20, 33), (21, 34)]
    for hh, mm in times:
        tick(world, tmp_path, et_at(MON, hh, mm), hist_nightly=world.hist)
    assert len(world.hist_calls) == 3  # 17:31, 18:31, 19:32; 18:00 throttled
    assert any("gave up" in m for _, m in world.notes)
    tick(world, tmp_path, et_at(MON, 23, 0), hist_nightly=world.hist)
    assert len(world.hist_calls) == 3


def test_one_absent_close_is_not_retried_but_a_vendor_delay_is(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 17, 31))
    world.hist_result = BuildSummary(symbols=10, closes_absent=[("GONE", MON)])
    tick(world, tmp_path, et_at(MON, 17, 31), hist_nightly=world.hist)
    tick(world, tmp_path, et_at(MON, 19, 0), hist_nightly=world.hist)
    assert len(world.hist_calls) == 1
    other = tmp_path / "delay"
    world2 = TrackWorld(now=et_at(MON, 17, 31))
    world2.hist_result = BuildSummary(symbols=10, closes_absent=[(f"S{i}", MON) for i in range(8)])
    tick(world2, other, et_at(MON, 17, 31), hist_nightly=world2.hist)
    tick(world2, other, et_at(MON, 18, 40), hist_nightly=world2.hist)
    assert len(world2.hist_calls) == 2


def test_a_failing_nightly_does_not_block_the_backup(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 17, 31))

    def boom(day: date, tracked: frozenset[str], deadline: datetime) -> BuildSummary:
        raise ConnectionError("terminal down")

    log = tick(world, tmp_path, et_at(MON, 17, 31), hist_nightly=boom)
    assert any(a.startswith("backup") for a in log)
    assert any("hist-nightly failed" in m for _, m in world.notes)


def test_forecast_targets_and_needed_closes(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    runs = RunLogStore(tmp_path / "runs")
    fits = [f for r in runs.all(Mode.PROSPECTIVE) for f in r.fits if f.ok]
    assert fits
    expiry = max(f.expiration for f in fits)
    assert forecast_targets(runs, Mode.PROSPECTIVE, MON) == {"XYZ"}
    assert forecast_targets(runs, Mode.PROSPECTIVE, expiry + timedelta(days=1)) == frozenset()
    need = needed_closes(tmp_path, expiry)
    assert ("XYZ", expiry) in need
    assert needed_closes(tmp_path, MON) == set()


def test_window_and_week_helpers() -> None:
    assert nightly_window(date(2026, 11, 20)) == (date(2026, 11, 9), date(2026, 11, 20))
    assert last_session_of_week(date(2026, 11, 20))  # a Friday
    assert last_session_of_week(date(2026, 11, 27))  # the half-day Friday after Thanksgiving
    assert not last_session_of_week(date(2026, 11, 25))
    assert not last_session_of_week(MON)


def test_evening_deadline_is_the_next_sessions_morning(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(date(2026, 11, 20), 17, 31))  # a Friday
    tick(world, tmp_path, et_at(date(2026, 11, 20), 17, 31), hist_nightly=world.hist)
    assert world.deadlines == [et_at(date(2026, 11, 23), 7, 45)]  # Monday


def test_a_deadline_stop_is_retried(tmp_path: Path) -> None:
    world = TrackWorld(now=et_at(MON, 17, 31))
    world.hist_result = BuildSummary(symbols=10, stopped_at_deadline=True)
    tick(world, tmp_path, et_at(MON, 17, 31), hist_nightly=world.hist)
    tick(world, tmp_path, et_at(MON, 18, 40), hist_nightly=world.hist)
    assert len(world.hist_calls) == 2


def test_nightly_tracks_forecast_targets_without_an_open_position(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = TrackWorld(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)  # writes run logs with XYZ targets
    monkeypatch.setattr(Pipeline, "held", lambda self, day: frozenset())
    tick(world, tmp_path, et_at(MON, 18, 0), hist_nightly=world.hist)
    assert world.hist_calls and "XYZ" in world.hist_calls[0][1]


def test_nightly_tracks_open_positions_without_run_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = TrackWorld(now=et_at(MON, 17, 31))
    monkeypatch.setattr(Pipeline, "held", lambda self, day: frozenset({"HELD"}))
    tick(world, tmp_path, et_at(MON, 17, 31), hist_nightly=world.hist)
    assert world.hist_calls[0][1] == {"HELD"}


def test_a_bad_hist_store_fails_only_settlement(tmp_path: Path) -> None:
    from remy_finance.history import HistStore, hist_path
    from remy_finance.history.closes import StoreFirstCloses

    bad = hist_path(tmp_path)
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a database" * 100)
    world = TrackWorld(now=et_at(MON, 9, 0))
    # Built without opening the store, as run_tick does.
    world.closes = StoreFirstCloses(lambda: HistStore(bad), world.closes)  # type: ignore[assignment]
    log = run_day(world, tmp_path, MON)
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.DECISION) is not None
    assert any(a.startswith("backup") for a in log)
    later = tick(world, tmp_path, et_at(S.EXPIRY, 18))  # a position expires: settle reads
    assert any(a.startswith("FAILED settle") for a in later)
    assert any(a.startswith("backup") for a in later)


def test_live_capture_marks_tracked_only_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import remy_finance.data.massive as massive
    import remy_finance.data.thetadata as thetadata
    import remy_finance.snapshots.builder as builder
    import remy_finance.universe.selection as selection
    from remy_finance.scheduler.live import live_capture

    class Membership:
        members = ("AAA",)
        version_label = "UNIV-1 test"

    seen: dict[str, object] = {}

    def fake_build(theta: object, source: object, session: Session, **kw: object) -> MarketState:
        seen.update(kw)
        board = _board()
        return board.model_copy(update={"provenance": kw["provenance"]})

    class Stub:
        def __init__(self, *a: object, **k: object) -> None: ...
        def prefetch(self, symbols: object) -> None: ...

    monkeypatch.setattr(selection.UniverseStore, "membership_for", lambda self, d: Membership())
    monkeypatch.setattr(thetadata, "ThetaDataSource", Stub)
    monkeypatch.setattr(massive, "MassiveSource", Stub)
    monkeypatch.setattr(builder, "build_prospective", fake_build)
    board = live_capture(tmp_path)(Session.OPEN, frozenset({"OLD"}), frozenset({"TRK", "AAA"}))
    assert seen["underlyings"] == ["AAA", "OLD", "TRK"]
    assert tracked_only(board) == {"TRK"} and not_recommendable(board) == {"OLD", "TRK"}
