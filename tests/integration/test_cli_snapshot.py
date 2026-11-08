"""CLI: `remy snapshot build` defaults to the stored universe (no network: fixture source)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import remy_finance.cli as cli
from remy_finance.core import Mode, Session
from remy_finance.data import FixtureSource
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time
from remy_finance.snapshots.store import SnapshotStore
from remy_finance.universe.selection import UniverseDecision, UniverseStore
from tests import factories as f

DAY = f.AS_OF.date()


def test_build_without_symbols_uses_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    src = FixtureSource(
        [
            f.option_quote(at=f.AS_OF),
            f.underlying_quote(at=f.AS_OF),
            f.option_quote(at=snapshot_time(DAY, Session.OPEN, SnapshotKind.EXECUTION)),
            f.underlying_quote(at=snapshot_time(DAY, Session.OPEN, SnapshotKind.EXECUTION)),
        ]
    )
    monkeypatch.setattr(cli, "_sources", lambda no_events: (src, src))
    UniverseStore(tmp_path / "universe").write(
        UniverseDecision(
            rule_version="UNIV-1",
            candidate_list_version="T",
            selection_session=DAY,
            as_of=f.AS_OF,
            window_start=DAY,
            window_end=DAY,
            scores=(),
            members=("XYZ",),
            inputs_hash="0" * 64,
        )
    )
    result = CliRunner().invoke(
        cli.app, ["snapshot", "build", "--date", DAY.isoformat(), "--session", "open", "--movegen"]
    )
    assert result.exit_code == 0, result.output
    assert (
        "DECISION" in result.output and "EXECUTION" in result.output and "movegen:" in result.output
    )
    path = SnapshotStore(tmp_path / "snapshots").find(
        Mode.REPLAY, DAY, Session.OPEN, SnapshotKind.DECISION
    )
    assert path is not None
    assert SnapshotStore(tmp_path / "snapshots").load(path).universe_version == f"UNIV-1:{DAY}"


def test_build_without_symbols_or_universe_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(
        cli.app, ["snapshot", "build", "--date", DAY.isoformat(), "--session", "open"]
    )
    assert result.exit_code != 0 and "no stored universe" in result.output
