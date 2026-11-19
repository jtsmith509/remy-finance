"""Regression tests from Codex's review of the R1 extras (docs/review/R1-extras-findings.md).

Ported from its offline probes at fc8b424 (scheduler_cli). The failing probes are the regression
tests for X01-X06; the passing ones guard behaviour Codex verified.
"""

from remy_finance.data.timeutil import et_at, previous_session
from remy_finance.snapshots.store import SnapshotStore
from tests.unit.scheduler.test_tick import MON, World, tick


def test_watermark_recovery_does_not_invent_gaps_before_scheduler_first_tick(tmp_path):
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 9, 0))
    friday = previous_session(MON)
    # The first tick creates a backup marker dated for the *previous* session,
    # although the scheduler did not operate on that session.
    assert (tmp_path / "scheduler" / "markers" / f"{friday}-backup.done").exists()
    assert SnapshotStore(tmp_path / "snapshots").gaps() == []
    (tmp_path / "scheduler" / "watermark").write_text("")
    tick(world, tmp_path, et_at(MON, 9, 5))
    old_gaps = [g for g in SnapshotStore(tmp_path / "snapshots").gaps() if g.session_date < MON]
    assert not old_gaps, "backup's prior-session marker causes invented pre-deployment gaps"
