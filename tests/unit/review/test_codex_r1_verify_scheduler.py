"""Regression tests from Codex's verification of the R1 fixes (docs/review/R1-fixes-findings.md).

Ported from its offline probes at aa53762 (scheduler). Each failing probe is the regression
test for its V-finding; the passing probes guard the fixes they verified.
"""

import os
from datetime import date, timedelta
from pathlib import Path

from remy_finance.core import Mode, Session
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, registry_path
from remy_finance.scheduler.held import held_only
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.replay import ReplayDriver, ReplaySummary
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.snapshots.schedule import SnapshotKind
from remy_finance.snapshots.store import SnapshotStore
from remy_finance.universe.selection import UniverseDecision, UniverseStore
from tests.unit.scheduler.test_replay import build, closes
from tests.unit.scheduler.test_tick import MON, World, tick


def _unresolved(tmp_path, day):
    store = SnapshotStore(tmp_path / "snapshots")
    return [
        (session, kind)
        for session in Session
        for kind in SnapshotKind
        if not store.has_gap(day, session, kind)
    ]


def test_watermark_update_failure_does_not_permanently_disable_reconciliation(
    tmp_path, monkeypatch
):
    # Codex V06. The watermark is now replaced atomically, so the probe's truncate-then-raise
    # injection is replaced by a crash at the rename: the old watermark must survive intact.
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 9, 0))
    missed_day = MON + timedelta(days=1)
    real_replace = os.replace

    def crash_at_rename(src, dst):
        if Path(dst).name == "watermark":
            raise OSError("simulated crash during the watermark update")
        return real_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash_at_rename)
        tick(world, tmp_path, et_at(MON + timedelta(days=2), 9, 0))
    assert (tmp_path / "scheduler" / "watermark").read_text().strip() == MON.isoformat()
    tick(world, tmp_path, et_at(MON + timedelta(days=2), 9, 5))
    assert not _unresolved(tmp_path, missed_day)


def test_malformed_watermark_is_recovered_from_the_scheduler_markers(tmp_path):
    # A watermark left empty by a crash before the fix: recovered, never fatal.
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 17, 0))  # Monday's sessions resolve (markers written)
    (tmp_path / "scheduler" / "watermark").write_text("")
    log = tick(world, tmp_path, et_at(MON + timedelta(days=2), 9, 0))
    assert any("watermark unreadable" in line for line in log), log
    assert not _unresolved(tmp_path, MON + timedelta(days=1))
    assert (tmp_path / "scheduler" / "watermark").read_text().strip() == (
        MON + timedelta(days=2)
    ).isoformat()


def test_dr0039_replay_persists_held_provenance_and_excludes_held_only_recommendations(tmp_path):
    membership = UniverseDecision(
        rule_version="UNIV-1",
        candidate_list_version="REVIEW",
        selection_session=date(2026, 11, 2),
        as_of=et_at(date(2026, 11, 2), 9),
        window_start=date(2026, 10, 2),
        window_end=date(2026, 10, 30),
        scores=(),
        members=("AAA",),
        inputs_hash="0" * 64,
    )
    UniverseStore(tmp_path / "universe").write(membership)
    driver = ReplayDriver(tmp_path, build, closes(), "review01", log=lambda _: None)
    store = SnapshotStore(tmp_path / "snapshots")
    result = ReplaySummary()
    decision = driver._board(
        store, MON, Session.OPEN, SnapshotKind.DECISION, None, result, frozenset({"XYZ"})
    )
    execution = driver._board(
        store, MON, Session.OPEN, SnapshotKind.EXECUTION, decision, result, frozenset({"XYZ"})
    )
    for kind, board in ((SnapshotKind.DECISION, decision), (SnapshotKind.EXECUTION, execution)):
        assert board.underlyings == ("AAA", "XYZ")
        assert held_only(board) == frozenset({"XYZ"})
        path = store.find(Mode.REPLAY, MON, Session.OPEN, kind)
        loaded = store.load(path)
        assert loaded.snapshot_id == board.snapshot_id
        assert held_only(loaded) == frozenset({"XYZ"})
    with Registry.create(registry_path(tmp_path, Mode.REPLAY), Mode.REPLAY) as reg:
        pipeline = Pipeline(
            reg,
            RunLogStore(tmp_path / "runs"),
            "review01",
            lambda: decision.as_of + timedelta(seconds=30),
        )
        selected = pipeline.recommend(decision)
        assert selected
        assert {rec.engine for rec in reg.recommendations()} == {"MKT-1", "RAND-1"}
        assert {rec.candidate.legs[0].contract.underlying for rec in reg.recommendations()} == {
            "AAA"
        }
        assert pipeline.recommend(decision) == selected
        assert len(reg.recommendations()) == len(selected)
