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
