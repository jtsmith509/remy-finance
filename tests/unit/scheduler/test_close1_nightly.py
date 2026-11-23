"""DR-0046: tracked-only names on boards, forecast targets, and the nightly HIST-1 step."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

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
    hist_result: BuildSummary = field(default_factory=lambda: BuildSummary(symbols=10))

    def capture(
        self,
        session: Session,
        held: frozenset[str] = frozenset(),
        tracked: frozenset[str] = frozenset(),
    ) -> MarketState:
        self.seen.append((held, tracked))
        return super().capture(session, held, tracked)

    def hist(self, day: date, tracked: frozenset[str]) -> BuildSummary:
        self.hist_calls.append((day, tracked))
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
    tick(world, tmp_path, et_at(TUE, 7, 59), hist_nightly=world.hist)  # before 08:00
    assert [d for d, _ in world.hist_calls] == [MON]


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

    def boom(day: date, tracked: frozenset[str]) -> BuildSummary:
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
