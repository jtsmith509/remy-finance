"""R0.4 tests: schedule, builder (replay and prospective), write-once store, gaps."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from remy_finance.core import (
    LookaheadError,
    MarketState,
    Mode,
    OptionQuote,
    QualityFlag,
    Right,
    Session,
    UnderlyingQuote,
)
from remy_finance.data import FixtureSource
from remy_finance.snapshots.builder import build_prospective, build_replay
from remy_finance.snapshots.schedule import SnapshotKind, snapshot_time
from remy_finance.snapshots.store import (
    GapRecordedError,
    SnapshotExistsError,
    SnapshotIntegrityError,
    SnapshotStore,
)
from tests import factories as f

D = Decimal
DAY = date(2026, 11, 6)  # Friday; EST
OPEN = snapshot_time(DAY, Session.OPEN)


# ------------------------------------------------------------------- schedule


def test_schedule_times_dst_and_half_days() -> None:
    assert datetime(2026, 11, 6, 15, 0, tzinfo=UTC) == OPEN  # 10:00 EST
    assert snapshot_time(date(2026, 7, 6), Session.OPEN) == datetime(
        2026, 7, 6, 14, 0, tzinfo=UTC
    )  # EDT
    assert snapshot_time(DAY, Session.CLOSE) == datetime(2026, 11, 6, 20, 45, tzinfo=UTC)
    assert snapshot_time(DAY, Session.CLOSE, SnapshotKind.EXECUTION) == datetime(
        2026, 11, 6, 20, 50, tzinfo=UTC
    )
    # day after Thanksgiving 2026 is a 13:00 close: close snapshot at 12:45 ET
    assert snapshot_time(date(2026, 11, 27), Session.CLOSE) == datetime(
        2026, 11, 27, 17, 45, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="not an XNYS"):
        snapshot_time(date(2026, 11, 7), Session.OPEN)  # Saturday


# -------------------------------------------------------------------- builder


def fixture_source() -> FixtureSource:
    return FixtureSource(
        [
            f.option_quote("100", at=OPEN - timedelta(seconds=2)),
            f.option_quote("105", at=OPEN - timedelta(minutes=20)),  # stale under QAGE-1
            f.option_quote("95", Right.PUT, bid="0", at=OPEN - timedelta(seconds=1)),  # one-sided
            f.option_quote("110", at=OPEN + timedelta(seconds=1)),  # after as_of: must not appear
            f.underlying_quote(at=OPEN - timedelta(seconds=1)),
            f.dividend(),
            f.rate(),
        ]
    )


def replay(
    src: FixtureSource | None = None,
    syms: Sequence[str] = ("XYZ",),
    session: Session = Session.OPEN,
    kind: SnapshotKind = SnapshotKind.DECISION,
) -> MarketState:
    return build_replay(
        src or fixture_source(), DAY, session, kind=kind, universe_version="U-T", underlyings=syms
    )


def test_replay_build_reads_point_in_time_and_flags() -> None:
    s = replay()
    assert s.as_of == OPEN and s.mode is Mode.REPLAY
    strikes = {q.contract.strike for q in s.option_quotes}
    assert strikes == {D(100), D(105), D(95)}
    flags = {q.contract.strike: set(q.quality_flags) for q in s.option_quotes}
    assert flags[D(105)] == {QualityFlag.STALE_QUOTE}
    assert flags[D(95)] == {QualityFlag.ONE_SIDED}
    assert flags[D(100)] == set()
    assert s.dividends and s.rates and not s.quality_notes


def test_missing_symbol_becomes_a_note_not_a_crash() -> None:
    s = replay(syms=("XYZ", "ABC"))
    codes = {(n.scope, n.code) for n in s.quality_notes}
    assert ("ABC", "NO_OPTION_QUOTES") in codes and ("ABC", "NO_UNDERLYING_QUOTE") in codes


class FailingFor(FixtureSource):
    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        if underlying == "BAD":
            raise RuntimeError("vendor timeout")
        return super().chain_quotes_at(underlying, ts)


def test_vendor_error_for_one_symbol_is_recorded() -> None:
    src = FailingFor(fixture_source().records())
    s = replay(src, syms=("XYZ", "BAD"))
    assert any(n.scope == "BAD" and n.code == "RuntimeError" for n in s.quality_notes)
    assert s.option_quotes


class Leaky(FixtureSource):
    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        return (f.option_quote(at=ts + timedelta(seconds=1)),)


def test_lookahead_propagates_from_builder() -> None:
    with pytest.raises(LookaheadError):
        replay(Leaky(fixture_source().records()))


class FakeLive:
    def __init__(self, stamp: datetime) -> None:
        self.stamp = stamp

    def live_chain_quotes(self, underlying: str) -> tuple[OptionQuote, ...]:
        return (f.option_quote(at=self.stamp),)

    def live_underlying_quote(self, symbol: str) -> UnderlyingQuote | None:
        return f.underlying_quote(at=self.stamp)


def test_prospective_as_of_after_fetch() -> None:
    fetched_at = OPEN + timedelta(seconds=3)
    s = build_prospective(
        FakeLive(fetched_at - timedelta(seconds=1)),
        fixture_source(),
        Session.OPEN,
        universe_version="U-T",
        underlyings=("XYZ",),
        clock=lambda: fetched_at,
    )
    assert s.mode is Mode.PROSPECTIVE and s.as_of == fetched_at


def test_prospective_quote_after_clock_is_lookahead() -> None:
    with pytest.raises(LookaheadError):
        build_prospective(
            FakeLive(OPEN + timedelta(seconds=5)),
            fixture_source(),
            Session.OPEN,
            universe_version="U-T",
            underlyings=("XYZ",),
            clock=lambda: OPEN,
        )


# ---------------------------------------------------------------------- store


def test_round_trip_preserves_snapshot_id(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    s = replay()
    m = store.write(s)
    path = store.find(Mode.REPLAY, DAY, Session.OPEN, SnapshotKind.DECISION)
    assert path is not None and path.parent.parent.name == "replay"
    loaded = store.load(path)
    assert loaded == s and loaded.snapshot_id == m.snapshot_id
    assert m.n_option_quotes == 3


def test_write_once(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.write(replay())
    with pytest.raises(SnapshotExistsError):
        store.write(replay())


def test_tampering_is_detected(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.write(replay())
    path = store.find(Mode.REPLAY, DAY, Session.OPEN, SnapshotKind.DECISION)
    assert path is not None
    board = path / "board.json"
    board.chmod(0o644)
    board.write_text(board.read_text().replace("U-T", "U-X"))
    with pytest.raises(SnapshotIntegrityError):
        store.load(path)


def test_execution_snapshot_links_decision(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    decision = store.write(replay())
    exec_state = replay(kind=SnapshotKind.EXECUTION)
    with pytest.raises(ValueError, match="link"):
        store.write(exec_state, kind=SnapshotKind.EXECUTION)
    with pytest.raises(ValueError, match="not found"):
        store.write(exec_state, kind=SnapshotKind.EXECUTION, decision_snapshot_id="0" * 64)
    m = store.write(
        exec_state, kind=SnapshotKind.EXECUTION, decision_snapshot_id=decision.snapshot_id
    )
    assert m.as_of == OPEN + timedelta(minutes=5)


def test_replay_must_sit_on_schedule(tmp_path: Path) -> None:
    off = build_replay(
        fixture_source(), DAY, Session.OPEN, universe_version="U-T", underlyings=("XYZ",)
    )
    off = off.model_copy(update={"as_of": OPEN + timedelta(seconds=30)})
    with pytest.raises(ValueError, match="scheduled"):
        SnapshotStore(tmp_path).write(off)


def prospective(at: datetime) -> MarketState:
    return build_prospective(
        FakeLive(at - timedelta(seconds=1)),
        fixture_source(),
        Session.OPEN,
        universe_version="U-T",
        underlyings=("XYZ",),
        clock=lambda: at,
    )


def test_gap_blocks_backfill_and_late_prospective_refused(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    with pytest.raises(ValueError, match="scheduled time"):
        store.write(prospective(OPEN + timedelta(minutes=11)))
    store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "Terminal down at 10:00")
    with pytest.raises(GapRecordedError):
        store.write(prospective(OPEN + timedelta(seconds=5)))
    assert len(store.gaps()) == 1
    store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "again")  # idempotent
    assert len(store.gaps()) == 1


def test_gap_cannot_replace_an_existing_snapshot(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.write(prospective(OPEN + timedelta(seconds=5)))
    with pytest.raises(SnapshotExistsError):
        store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "late")
    assert (
        store.find(Mode.REPLAY, DAY, Session.OPEN, SnapshotKind.DECISION) is None
    )  # trees are separate
