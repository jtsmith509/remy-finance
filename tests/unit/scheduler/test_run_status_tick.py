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
