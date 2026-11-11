"""Regression tests for Codex's R1.6 scheduler findings (docs/review/R1.6-R1.8-findings.md).

Ported from Codex's offline reproductions at b38b9b1. F02 follows DR-0039: boards carry the
held names, so the open position is managed, and held-only names are never recommended.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remy_finance.core import MarketState, Mode, Provenance, Session
from remy_finance.data.timeutil import et_at
from remy_finance.registry import Registry, registry_path
from remy_finance.scheduler import ops
from remy_finance.scheduler.held import HELD_ONLY, board_symbols, held_only
from remy_finance.scheduler.pipeline import Pipeline
from remy_finance.scheduler.runlog import RunLogStore
from remy_finance.snapshots.schedule import SnapshotKind
from remy_finance.snapshots.store import SnapshotStore
from tests.unit.baselines import synthetic as S
from tests.unit.execution.test_engine import PaperEngine, board, open_trade
from tests.unit.registry.helpers import recommendation
from tests.unit.scheduler.test_tick import MON, World, tick


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


# --------------------------------------------------------------------------- F01


def test_f01_failed_export_push_is_retried_on_clean_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "GIT_AUTHOR_NAME": "Review",
        "GIT_AUTHOR_EMAIL": "review@example.invalid",
        "GIT_COMMITTER_NAME": "Review",
        "GIT_COMMITTER_EMAIL": "review@example.invalid",
    }.items():
        monkeypatch.setenv(k, v)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "registry-export", str(remote))
    repo = tmp_path / "export"
    git(tmp_path, "clone", str(remote), str(repo))
    (repo / "README.md").write_text("export\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "push", "origin", "HEAD")
    old_remote = git(remote, "rev-parse", "refs/heads/registry-export")
    (repo / "new.jsonl").write_text("new rows\n")
    real_git = ops._git
    calls: list[tuple[str, ...]] = []

    def fail_first_push(path: Path, *args: str) -> str:
        calls.append(args)
        if args[0] == "push" and sum(x[0] == "push" for x in calls) == 1:
            raise subprocess.CalledProcessError(1, ["git", "push"], stderr="simulated outage")
        return real_git(path, *args)

    monkeypatch.setattr(ops, "_git", fail_first_push)
    with pytest.raises(subprocess.CalledProcessError):
        ops.push_export(repo, MON, 1)
    assert git(repo, "rev-parse", "HEAD") != old_remote
    assert git(repo, "status", "--porcelain") == ""
    head = ops.push_export(repo, MON, 0)
    assert git(remote, "rev-parse", "refs/heads/registry-export") == git(
        repo, "rev-parse", "HEAD"
    ), "retry skipped the unpushed export commit"
    assert head == git(repo, "rev-parse", "HEAD")
    assert ops.push_export(repo, MON, 0) == ""  # nothing left to push


# --------------------------------------------------------------------------- F02


def test_f02_board_symbols_add_held_names_after_the_members() -> None:
    symbols, extra = board_symbols(["AAA", "XYZ"], {"XYZ", "OLD", "GONE"})
    assert symbols == ["AAA", "XYZ", "GONE", "OLD"]
    assert extra == ("GONE", "OLD")


@dataclass
class HeldWorld(World):
    held_seen: list[frozenset[str]] = field(default_factory=list)

    def capture(self, session: Session, held: frozenset[str] = frozenset()) -> MarketState:
        self.held_seen.append(held)
        return super().capture(session, held)


def test_f02_tick_captures_boards_with_the_held_names(tmp_path: Path) -> None:
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    try:
        engine = PaperEngine(reg)
        old = et_at(date(2026, 10, 30), 10, 0)
        rec = recommendation(decision_time=old, receipt_time=old + timedelta(seconds=30))
        assert rec.candidate.legs[0].contract.underlying == "XYZ"
        assert engine.position(open_trade(engine, rec)).is_open
    finally:
        reg.close()
    world = HeldWorld(now=et_at(MON, 10, 0))
    tick(world, tmp_path, et_at(MON, 10, 0))
    assert world.held_seen and all("XYZ" in h for h in world.held_seen)


def test_f02_held_only_names_are_never_recommended(tmp_path: Path) -> None:
    at = et_at(MON, 10, 0)
    plain = S.chain(half_spread="0.02", tick="0.01").model_copy(
        update={"as_of": at, "mode": Mode.PROSPECTIVE, "session": Session.OPEN}
    )
    held = plain.model_copy(
        update={
            "provenance": (
                *plain.provenance,
                Provenance(source="scheduler", details=((HELD_ONLY, "XYZ"),)),
            )
        }
    )
    assert held_only(held) == {"XYZ"} and held_only(plain) == frozenset()
    for brd, expect_picks in ((plain, True), (held, False)):
        data = tmp_path / ("plain" if expect_picks else "held")
        reg = Registry.create(registry_path(data, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
        try:
            p = Pipeline(
                reg, RunLogStore(data / "runs"), "0" * 40, lambda: at + timedelta(seconds=30)
            )
            picks = p.recommend(brd)
            assert bool(picks) is expect_picks
        finally:
            reg.close()


def test_f02_position_off_the_board_is_reported_not_silently_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    try:
        engine = PaperEngine(reg)
        old_decision = et_at(date(2026, 10, 30), 10, 0)
        rec = recommendation(
            decision_time=old_decision, receipt_time=old_decision + timedelta(seconds=30)
        )
        open_trade(engine, rec)
        # Codex's reproduction: a board built without the held name (as before DR-0039).
        decision = board(et_at(date(2026, 11, 2), 10, 0)).model_copy(
            update={"underlyings": ("ABC",), "underlying_quotes": (), "option_quotes": ()}
        )
        execution = decision.model_copy(update={"as_of": decision.as_of + timedelta(minutes=5)})
        pipeline = Pipeline(reg, RunLogStore(tmp_path / "runs"), "commit", lambda: execution.as_of)
        called: list[Any] = []
        monkeypatch.setattr(PaperEngine, "on_snapshot", lambda *a: called.append(a) or [])
        pipeline.manage(decision, execution)
        assert not called
        assert any("not managed" in t and "XYZ" in t for t in pipeline.timings)
        # With the held name on the board (DR-0039), the position is managed.
        on_board = board(et_at(date(2026, 11, 2), 10, 0))
        pipeline.manage(on_board, on_board.model_copy(update={"as_of": execution.as_of}))
        assert called
    finally:
        reg.close()


# --------------------------------------------------------------------------- F03


def test_f03_recommendation_failure_does_not_block_execution_or_management(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(now=et_at(MON, 10, 0))

    def broken_recommend(self: Pipeline, decision: MarketState) -> list[str]:
        raise RuntimeError("baseline failed")

    managed: list[Any] = []
    monkeypatch.setattr(Pipeline, "recommend", broken_recommend)
    monkeypatch.setattr(
        Pipeline, "manage", lambda self, decision, execution: managed.append(execution) or 0
    )
    tick(world, tmp_path, et_at(MON, 10, 0))
    tick(world, tmp_path, et_at(MON, 10, 7))
    execution = SnapshotStore(tmp_path / "snapshots").find(
        Mode.PROSPECTIVE, MON, Session.OPEN, SnapshotKind.EXECUTION
    )
    assert execution is not None and managed
    assert list((tmp_path / "logs" / "failures").glob("*open-recommend*.json"))
    assert world.notes and "open-recommend failed" in world.notes[0][1]


# --------------------------------------------------------------------------- F05


def test_f05_missing_previous_day_slots_are_recorded_after_restart(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 9, 0))
    tick(world, tmp_path, et_at(MON, 9, 0))
    # The scheduler was down for all of Monday's scheduled captures.
    tick(world, tmp_path, et_at(MON + timedelta(days=1), 9, 0))
    store = SnapshotStore(tmp_path / "snapshots")
    missing = [
        (session, kind)
        for session in Session
        for kind in SnapshotKind
        if not store.has_gap(MON, session, kind)
    ]
    assert not missing, f"missed previous-day slots remain neither stored nor gaps: {missing}"


def test_f05_late_reconciliation_resolves_entries_without_recommending(tmp_path: Path) -> None:
    world = World(now=et_at(MON, 10, 0))
    tick(world, tmp_path, et_at(MON, 10, 0))  # decision stored, recommendations made
    with Registry.open(registry_path(tmp_path, Mode.PROSPECTIVE)) as reg:
        recs = reg.recommendations()
        assert recs
    # Down until Tuesday: the open execution and the whole close session were missed.
    tick(world, tmp_path, et_at(MON + timedelta(days=1), 9, 0))
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.has_gap(MON, Session.OPEN, SnapshotKind.EXECUTION)
    assert store.has_gap(MON, Session.CLOSE, SnapshotKind.DECISION)
    with Registry.open(registry_path(tmp_path, Mode.PROSPECTIVE)) as reg:
        assert len(reg.recommendations()) == len(recs)  # nothing recommended late
        for r in recs:
            kinds = [e.kind.value for _, e in reg.events(r.trade_id)]
            assert "ORDER_UNFILLED" in kinds, kinds
        assert reg.verify().ok
    wm = (tmp_path / "scheduler" / "watermark").read_text().strip()
    assert datetime.fromisoformat(wm).date() == MON + timedelta(days=1)
