"""The R2 cut-over (DR-0048): v3 before the cut-over slot, v4 from it; replays name it."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, registry_path
from remy_finance.scheduler import cutover as C
from remy_finance.scheduler.runlog import RunLogStore
from tests.unit.scheduler.test_tick import MON, World, registry, run_day

TUE = MON + timedelta(days=1)


def test_no_cutover_means_v3_everywhere() -> None:
    assert C.CUTOVER is None  # set only by the cut-over DR's commit
    assert C.baseline_version(date(2030, 1, 2), Session.CLOSE) == "3"


def test_the_cutover_slot_and_everything_after_run_v4() -> None:
    at = (TUE, Session.CLOSE)
    v = lambda d, s: C.baseline_version(d, s, cutover=at)  # noqa: E731
    assert v(MON, Session.CLOSE) == "3"
    assert v(TUE, Session.OPEN) == "3"
    assert v(TUE, Session.CLOSE) == "4"
    assert v(TUE + timedelta(days=1), Session.OPEN) == "4"


def test_replay_override_wins_and_is_checked() -> None:
    assert C.baseline_version(MON, Session.OPEN, "4") == "4"
    assert C.baseline_version(MON, Session.OPEN, "3", cutover=(MON, Session.OPEN)) == "3"
    with pytest.raises(ValueError):
        C.baseline_version(MON, Session.OPEN, "5")


def test_the_scheduler_switches_at_the_cutover_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(C, "CUTOVER", (TUE, Session.OPEN))
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    run_day(world, tmp_path, TUE)
    with registry(tmp_path) as reg:
        by_day = {(r.engine, r.decision_time.date() >= TUE): r.engine_version
                  for r in reg.recommendations()}  # fmt: skip
        statuses = {(s.session_date, s.engine): s.engine_version for s in reg.run_statuses()}
    assert by_day[("MKT-1", False)] == "3" and by_day[("MKT-1", True)] == "4"
    assert by_day[("RAND-1", False)] == "3" and by_day[("RAND-1", True)] == "4"
    assert statuses[(MON, "MKT-1")] == "3" and statuses[(TUE, "MKT-1")] == "4"
    logs = RunLogStore(tmp_path / "runs").all(Mode.PROSPECTIVE)
    v4 = [r for r in logs if r.engine_version == "4"]
    assert v4 and all(any(f.startswith("CANNOT_WIN_EXCLUDED:") for f in r.flags) for r in v4)
    assert not any(f.startswith("CANNOT_WIN") for r in logs if r.engine_version == "3"
                   for f in r.flags)  # fmt: skip


def test_replay_runs_the_named_version(tmp_path: Path) -> None:
    from tests.unit.scheduler.test_replay import END, START, driver
    from tests.unit.scheduler.test_replay import data as data_fixture

    data = data_fixture.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    driver(data, baselines="4").run(START, END)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        assert {r.engine_version for r in reg.recommendations()} == {"4"}
        assert {s.engine_version for s in reg.run_statuses()} == {"4"}
    other = data_fixture.__wrapped__(tmp_path / "v3")  # type: ignore[attr-defined]
    driver(other).run(START, END)
    with Registry.open(registry_path(other, Mode.REPLAY)) as reg:
        assert {r.engine_version for r in reg.recommendations()} == {"3"}


# ------------------------------------------------------------ review regressions (v4)


def test_a_replay_registry_never_mixes_versions(tmp_path: Path) -> None:
    """RULES-01, CUTOVER-02, CUTOVER-03: v4 over a registry holding v3 records refuses."""
    from tests.unit.scheduler.test_replay import END, START, driver
    from tests.unit.scheduler.test_replay import data as data_fixture

    data = data_fixture.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    driver(data).run(START, START)  # v3, one day
    with pytest.raises(RuntimeError, match="archive it first"):
        driver(data, baselines="4").run(START, END)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        assert {r.engine_version for r in reg.recommendations()} == {"3"}


def test_a_stored_run_log_of_another_version_is_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RULES-01: the slot needs v4 but a v3 log exists for the board: failed, not v3."""
    world = World(now=et_at(MON, 9, 0))
    from tests.unit.scheduler.test_tick import tick

    tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))  # v3 logs written
    import remy_finance.scheduler.pipeline as P
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    store = SnapshotStore(tmp_path / "snapshots")
    found = store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.DECISION)
    assert found is not None
    board = store.load(found)
    monkeypatch.setattr(C, "CUTOVER", (MON, Session.OPEN))  # as if pulled mid-slot
    with Registry.open(registry_path(tmp_path, Mode.PROSPECTIVE)) as reg:
        p = P.Pipeline(reg, RunLogStore(tmp_path / "runs"), "0" * 40, lambda: world.now)
        assert p.recommend(board) == []
        assert {e for e, _ in p.failures} == {"MKT-1", "RAND-1"}
        assert all(isinstance(err, P.VersionMismatchError) for _, err in p.failures)


def test_the_per_day_cap_never_counts_the_other_versions_picks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUTOVER-01: a CLOSE cut-over; v3's OPEN picks don't cap v4 at the CLOSE."""
    monkeypatch.setattr(C, "CUTOVER", (MON, Session.CLOSE))
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    with registry(tmp_path) as reg:
        st = {(s.session, s.engine): (s.status.value, s.engine_version) for s in reg.run_statuses()}
    assert st[(Session.OPEN, "MKT-1")] == ("ok", "3")
    assert st[(Session.CLOSE, "MKT-1")] == ("ok", "4")  # not capped by v3's OPEN pick
    assert st[(Session.CLOSE, "RAND-1")] == ("ok", "4")


def test_statuses_without_a_run_log_carry_the_slot_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTS-1: a gap after the cut-over is a v4 skipped status."""
    from tests.unit.scheduler.test_tick import tick

    monkeypatch.setattr(C, "CUTOVER", (MON, Session.OPEN))
    world = World(now=et_at(MON, 10, 30))
    tick(world, tmp_path, et_at(MON, 10, 30))  # the OPEN slot is a gap
    with registry(tmp_path) as reg:
        got = {(s.engine, s.status.value): s.engine_version for s in reg.run_statuses()}
    assert got == {("MKT-1", "skipped"): "4", ("RAND-1", "skipped"): "4"}


def test_replay_cli_defaults_to_v3_and_checks_the_flag() -> None:
    """TESTS-5."""
    import inspect

    import typer

    from remy_finance import cli

    assert inspect.signature(cli.replay_run).parameters["baselines"].default == "v3"
    assert cli._baseline_version("v3") == "3" and cli._baseline_version("V4") == "4"
    with pytest.raises(typer.BadParameter):
        cli._baseline_version("v5")
