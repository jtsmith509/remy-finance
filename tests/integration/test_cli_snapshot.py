"""CLI: `remy snapshot build` defaults to the stored universe (no network: fixture source)."""

from __future__ import annotations

from datetime import UTC, datetime
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


def test_rerun_resumes_instead_of_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_build_without_symbols_uses_universe(tmp_path, monkeypatch)
    again = CliRunner().invoke(
        cli.app, ["snapshot", "build", "--date", DAY.isoformat(), "--session", "open"]
    )
    assert again.exit_code == 0, again.output
    assert again.output.count("already stored") == 2


def test_take_on_a_weekend_exits_cleanly_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as dt

    class Sunday(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:  # type: ignore[override]
            return dt.datetime(2026, 11, 8, 15, 30, tzinfo=dt.UTC)

    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "datetime", Sunday)
    monkeypatch.setattr(
        cli, "_sources", lambda no_events: pytest.fail("must not fetch outside the window")
    )
    result = CliRunner().invoke(
        cli.app, ["snapshot", "take", "--session", "open", "--symbols", "XYZ"]
    )
    assert result.exit_code == 2 and "not a trading session" in result.output


# ------------------------------------------- F05: missed prospective captures


def _freeze(monkeypatch: pytest.MonkeyPatch, at: datetime) -> None:
    import datetime as dt

    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:  # type: ignore[override]
            return at

    monkeypatch.setattr(cli, "datetime", Frozen)


FRI_1011_ET = datetime(2026, 11, 6, 15, 11, tzinfo=UTC)
FRI_1002_ET = datetime(2026, 11, 6, 15, 2, tzinfo=UTC)


def _take(*extra: str) -> object:
    return CliRunner().invoke(
        cli.app,
        ["snapshot", "take", "--session", "open", "--symbols", "XYZ", "--no-events", *extra],
    )


def test_f05_late_snapshot_take_records_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    _freeze(monkeypatch, FRI_1011_ET)
    monkeypatch.setattr(cli, "_sources", lambda no_events: pytest.fail("must not fetch when late"))
    result = _take("--with-execution")
    assert result.exit_code == 2 and "too late" in result.output  # type: ignore[attr-defined]
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)
    assert store.has_gap(DAY, Session.OPEN, SnapshotKind.EXECUTION)


def test_f05_capture_overrun_records_a_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import remy_finance.snapshots.builder as builder

    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    _freeze(monkeypatch, FRI_1002_ET)  # started inside the window ...
    monkeypatch.setattr(cli, "_sources", lambda no_events: (None, None))
    slow_board = f.market_state(
        mode=Mode.PROSPECTIVE, as_of=FRI_1011_ET
    )  # ... but finished at 10:11
    monkeypatch.setattr(builder, "build_prospective", lambda *a, **k: slow_board)
    result = _take()
    assert result.exit_code == 2 and "overran" in result.output  # type: ignore[attr-defined]
    store = SnapshotStore(tmp_path / "snapshots")
    assert store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)
    assert store.find(Mode.PROSPECTIVE, DAY, Session.OPEN, SnapshotKind.DECISION) is None


def test_f05_capture_failure_records_gap_then_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import remy_finance.snapshots.builder as builder

    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    _freeze(monkeypatch, FRI_1002_ET)
    monkeypatch.setattr(cli, "_sources", lambda no_events: (None, None))

    def boom(*a: object, **k: object) -> object:
        raise RuntimeError("terminal crashed")

    monkeypatch.setattr(builder, "build_prospective", boom)
    result = _take()
    assert result.exit_code != 0 and isinstance(result.exception, RuntimeError)  # type: ignore[attr-defined]
    assert SnapshotStore(tmp_path / "snapshots").has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)


def test_f05_no_gap_for_non_session_days_or_early_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_sources", lambda no_events: pytest.fail("must not fetch"))
    _freeze(monkeypatch, datetime(2026, 11, 8, 15, 30, tzinfo=UTC))  # Sunday
    assert _take().exit_code == 2  # type: ignore[attr-defined]
    _freeze(monkeypatch, datetime(2026, 11, 6, 14, 30, tzinfo=UTC))  # Friday 09:30 ET, no --wait
    assert _take().exit_code == 2  # type: ignore[attr-defined]
    assert SnapshotStore(tmp_path / "snapshots").gaps() == []


def test_f05_late_rerun_after_success_records_no_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMY_DATA_DIR", str(tmp_path))
    store = SnapshotStore(tmp_path / "snapshots")
    store.write(f.market_state(mode=Mode.PROSPECTIVE, as_of=FRI_1002_ET))
    _freeze(monkeypatch, FRI_1011_ET)
    monkeypatch.setattr(cli, "_sources", lambda no_events: pytest.fail("must not fetch"))
    result = _take()
    assert result.exit_code == 2 and "already stored; no gap" in result.output  # type: ignore[attr-defined]
    assert not store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)
