"""Run-status records from the scheduler (EVAL-2 §4.3, DR-0047): every scheduled slot and
engine gets one; engines run and record independently."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, RunStatusKind, registry_path
from remy_finance.scheduler import pipeline as P
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.scheduler.tick import GAP_REASON, LATE_REASON
from tests.unit.registry.test_run_status import legacy_registry
from tests.unit.scheduler.test_tick import MON, World, registry, run_day, tick

TUE = MON + timedelta(days=1)


def statuses(data: Path) -> dict[tuple[str, str], tuple[str, str]]:
    with registry(data) as reg:
        return {
            (f"{s.session_date} {s.session.value}", s.engine): (s.status.value, s.reason)
            for s in reg.run_statuses()
        }


def test_a_full_day_records_one_status_per_slot_and_engine(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    got = statuses(tmp_path)
    assert {k: v[0] for k, v in got.items()} == {
        (f"{MON} OPEN", "MKT-1"): "ok",
        (f"{MON} OPEN", "RAND-1"): "ok",
        (f"{MON} CLOSE", "MKT-1"): "null",
        (f"{MON} CLOSE", "RAND-1"): "null",
    }
    with registry(tmp_path) as reg:
        by_key = {s.status_key: s for s in reg.run_statuses()}
        runs = {
            (r.engine, r.snapshot_id): r
            for r in RunLogStore(tmp_path / "runs").all(Mode.PROSPECTIVE)
        }
        for s in by_key.values():
            log = runs[(s.engine, s.snapshot_id)]
            from remy_finance.core.canonical import sha256_hex

            assert s.run_log_hash == sha256_hex(log.canonical_json())
            assert s.n_selected == len(log.selected) and s.code_commit == log.code_commit
            assert s.slot_time == et_at(
                MON, 10 if s.session is Session.OPEN else 15, 0 if s.session is Session.OPEN else 45
            )
        assert reg.verify().ok
    run_day(world, tmp_path, MON)  # a re-run records nothing new
    assert statuses(tmp_path) == got


def test_a_gap_is_skipped_for_every_engine(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 10, 30))
    tick(world, tmp_path, et_at(MON, 10, 30))
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "MKT-1")] == ("skipped", GAP_REASON)
    assert got[(f"{MON} OPEN", "RAND-1")] == ("skipped", GAP_REASON)


def test_a_dirty_tree_is_skipped(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0), dirty=True)
    run_day(world, tmp_path, MON)
    got = statuses(tmp_path)
    assert {v for v in got.values()} == {("skipped", "uncommitted changes")} and len(got) == 4


def test_one_failing_engine_never_stops_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*a: Any, **k: Any) -> Any:
        raise ZeroDivisionError("RAND-1 bug")

    monkeypatch.setattr(P, "BASELINES", (("RAND-1", broken), P.BASELINES[0]))
    world = World(now=et_at(MON, 9, 0))
    log = run_day(world, tmp_path, MON)
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "RAND-1")] == ("failed", "ZeroDivisionError: RAND-1 bug")
    assert got[(f"{MON} OPEN", "MKT-1")][0] == "ok"  # ran after the failure
    with registry(tmp_path) as reg:
        assert {r.engine for r in reg.recommendations()} == {"MKT-1"}
    assert any("RAND-1 failed" in m for _, m in world.notes)
    assert list((tmp_path / "logs" / "failures").glob("*recommend-RAND-1*.json"))
    assert any(a.startswith("FAILED open-recommend RAND-1") for a in log)


def test_a_movegen_failure_fails_every_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def boom(board: Any) -> Any:
        calls.append(1)
        raise MemoryError("movegen")

    monkeypatch.setattr(P, "generate", boom)
    world = World(now=et_at(MON, 9, 0))
    tick(
        world,
        tmp_path,
        et_at(
            MON,
            10,
            0,
        )
        + timedelta(seconds=20),
    )
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "MKT-1")] == ("failed", "MemoryError: movegen")
    assert got[(f"{MON} OPEN", "RAND-1")] == ("failed", "MemoryError: movegen")
    assert calls == [1]  # generated once, the error shared


@dataclass
class CrashWorld(World):
    crash: bool = True

    def commit(self) -> str:
        if self.crash:
            raise RuntimeError("git unavailable")  # not a dirty tree: the session step fails
        return super().commit()


def test_a_session_reconciled_late_is_skipped(tmp_path: Path) -> None:
    world = CrashWorld(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))  # decision stored
    world.crash = False
    tick(world, tmp_path, et_at(TUE, 9, 0))  # Monday reconciled on Tuesday
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "MKT-1")] == ("skipped", LATE_REASON)
    assert got[(f"{MON} CLOSE", "MKT-1")] == ("skipped", GAP_REASON)


def test_not_scheduled_engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orig = P.Pipeline.__post_init__

    def post(self: P.Pipeline) -> None:
        orig(self)
        self.disabled = {"RAND-1": "not deployed on this slot"}

    monkeypatch.setattr(P.Pipeline, "__post_init__", post)
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "RAND-1")] == ("not_scheduled", "not deployed on this slot")
    assert got[(f"{MON} OPEN", "MKT-1")][0] == "ok"
    tick(world, tmp_path, et_at(TUE, 10, 30))  # a gap: still not_scheduled, not skipped
    assert statuses(tmp_path)[(f"{TUE} OPEN", "RAND-1")][0] == "not_scheduled"


def test_a_legacy_registry_keeps_trading_and_statuses_follow_after_migration(
    tmp_path: Path,
) -> None:
    path = registry_path(tmp_path, Mode.PROSPECTIVE)
    path.parent.mkdir(parents=True)
    legacy_registry(path)
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    with Registry.open(path) as reg:
        assert len(reg.recommendations()) == 2 and reg.run_statuses() == []
    Registry.migrate(path)
    run_day(world, tmp_path, TUE)
    got = statuses(tmp_path)
    assert {k[0] for k in got} == {f"{TUE} OPEN", f"{TUE} CLOSE"}  # Monday stays unrecorded
    with Registry.open(path) as reg:
        assert reg.verify().ok


def test_a_run_log_without_its_status_gets_one_on_rerun(tmp_path: Path) -> None:
    """A crash between writing the run log and the status: the re-run records the status
    from the stored run log."""
    path = registry_path(tmp_path, Mode.PROSPECTIVE)
    path.parent.mkdir(parents=True)
    legacy_registry(path)  # statuses can't be written: as if the process died before them
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))
    Registry.migrate(path)
    with Registry.open(path) as reg:
        p = P.Pipeline(reg, RunLogStore(tmp_path / "runs"), "0" * 40, lambda: world.now)
        from remy_finance.snapshots.schedule import SnapshotKind
        from remy_finance.snapshots.store import SnapshotStore

        store = SnapshotStore(tmp_path / "snapshots")
        board = store.load(store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.DECISION))  # type: ignore[arg-type]
        p.recommend(board)
        kinds = {s.engine: s.status for s in reg.run_statuses()}
    assert kinds == {"MKT-1": RunStatusKind.OK, "RAND-1": RunStatusKind.OK}


# ------------------------------------------------- review regressions (run-status review)


def test_a_failed_status_write_never_stops_the_other_engine_and_is_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL2-01, SCHED-03."""
    orig = Registry.add_run_status

    def flaky(self: Any, status: Any) -> Any:
        if status.engine == "MKT-1":
            raise RuntimeError("database is locked")
        return orig(self, status)

    monkeypatch.setattr(Registry, "add_run_status", flaky)
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))
    with registry(tmp_path) as reg:
        assert {r.engine for r in reg.recommendations()} == {"MKT-1", "RAND-1"}
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "RAND-1")][0] == "ok" and (f"{MON} OPEN", "MKT-1") not in got
    assert any("MKT-1 failed: RuntimeError" in m for _, m in world.notes)
    monkeypatch.setattr(Registry, "add_run_status", orig)
    log = tick(world, tmp_path, et_at(MON, 10, 3))  # the next tick repairs it from the log
    assert statuses(tmp_path)[(f"{MON} OPEN", "MKT-1")][0] == "ok"
    assert any("run statuses repaired" in a for a in log)


class Die(BaseException):
    pass


def test_a_crash_after_the_run_log_is_recorded_from_it_when_reconciled_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL2-03, SCHED-02: an engine that ran is never recorded as skipped."""
    orig = P.Pipeline._record_run

    def die(self: Any, board: Any, log: Any) -> None:
        raise Die()  # the process is killed between the run log and its status

    monkeypatch.setattr(P.Pipeline, "_record_run", die)
    world = World(now=et_at(MON, 9, 0))
    with pytest.raises(Die):
        tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))
    monkeypatch.setattr(P.Pipeline, "_record_run", orig)
    tick(world, tmp_path, et_at(TUE, 9, 0))  # Monday reconciled late
    got = statuses(tmp_path)
    assert got[(f"{MON} OPEN", "MKT-1")][0] == "ok"  # from its run log
    assert got[(f"{MON} OPEN", "RAND-1")] == ("skipped", LATE_REASON)  # never ran


def test_failures_do_not_carry_into_the_next_slot_and_fields_are_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTS-07: failures reset per board; snapshot id, engine version and marker note."""
    calls = {"n": 0}
    rand = dict(P.BASELINES)["RAND-1"]

    def once(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ZeroDivisionError("first call only")
        return rand(*a, **k)

    monkeypatch.setattr(P, "BASELINES", (P.BASELINES[0], ("RAND-1", once)))
    world = World(now=et_at(MON, 9, 0))
    run_day(world, tmp_path, MON)
    failures = [p.name for p in (tmp_path / "logs" / "failures").glob("*.json")]
    assert any("open-recommend-RAND-1" in f for f in failures)
    assert not any("close-recommend" in f for f in failures)
    markers = tmp_path / "scheduler" / "markers"
    assert "FAILED: RAND-1" in (markers / f"{MON}-open-recommend.done").read_text()
    assert "FAILED" not in (markers / f"{MON}-close-recommend.done").read_text()
    with registry(tmp_path) as reg:
        for s in reg.run_statuses():
            assert s.engine_version == "3"  # before the cut-over (DR-0048)


def test_skipped_statuses_carry_the_decision_board(tmp_path: Path) -> None:
    from remy_finance.snapshots.schedule import SnapshotKind
    from remy_finance.snapshots.store import SnapshotStore

    world = World(now=et_at(MON, 9, 0), dirty=True)
    tick(world, tmp_path, et_at(MON, 10, 0) + timedelta(seconds=20))
    store = SnapshotStore(tmp_path / "snapshots")
    found = store.find(Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.DECISION)
    assert found is not None
    with registry(tmp_path) as reg:
        assert {s.snapshot_id for s in reg.run_statuses()} == {store.load(found).snapshot_id}


def test_replay_stops_on_an_engine_failure_and_records_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EVAL2-02, SCHED-01, TESTS-01."""
    from tests.unit.scheduler.test_replay import END, START, driver
    from tests.unit.scheduler.test_replay import data as data_fixture

    data = data_fixture.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    good = driver(data).run(START, END)
    with Registry.open(registry_path(data, Mode.REPLAY)) as reg:
        st = reg.run_statuses()
    assert len(st) == 3 * 2 * 2 and good.recommended > 0  # 3 days x 2 sessions x 2 engines

    other = data_fixture.__wrapped__(tmp_path / "bad")  # type: ignore[attr-defined]

    def boom(board: Any) -> Any:
        raise ZeroDivisionError("movegen bug")

    monkeypatch.setattr(P, "generate", boom)
    with pytest.raises(RuntimeError, match="movegen bug"):
        driver(other).run(START, END)
    with Registry.open(registry_path(other, Mode.REPLAY)) as reg:
        assert reg.run_statuses() == []  # replay records no FAILED status: it re-runs


def test_replay_refuses_an_unmigrated_registry(tmp_path: Path) -> None:
    from tests.unit.scheduler.test_replay import END, START, driver
    from tests.unit.scheduler.test_replay import data as data_fixture

    data = data_fixture.__wrapped__(tmp_path)  # type: ignore[attr-defined]
    path = registry_path(data, Mode.REPLAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_registry(path, Mode.REPLAY)
    with pytest.raises(RuntimeError, match="registry migrate"):
        driver(data).run(START, END)


def test_doctor_warns_until_migrated(tmp_path: Path) -> None:
    """TESTS-06."""
    from datetime import UTC, datetime

    from remy_finance.scheduler.doctor import Status, run_checks

    path = registry_path(tmp_path, Mode.PROSPECTIVE)
    path.parent.mkdir(parents=True)
    legacy_registry(path)
    now = datetime(2026, 11, 24, 23, tzinfo=UTC)

    def checks() -> list[Any]:
        found = run_checks(tmp_path, now, terminal_probe=lambda: None, tree_clean=lambda: True)
        return [c for c in found if c.name.startswith("Run status")]

    (warn,) = checks()
    assert warn.status is Status.WARN and "migrate" in warn.detail
    Registry.migrate(path)
    assert checks() == []
