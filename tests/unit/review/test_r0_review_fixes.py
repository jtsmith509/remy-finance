"""Regression tests for the independent R0 review findings (docs/review/R0-findings.md).

Each test is named after its finding. They failed on the reviewed commit aeda0c2.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

import remy_finance.snapshots.store as store_mod
from remy_finance.core import (
    FEES_1,
    CorporateAction,
    CorporateActionType,
    DividendEvent,
    LookaheadError,
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    Right,
    Session,
    Side,
    Structure,
    StructureType,
    UnderlyingQuote,
)
from remy_finance.data import FixtureSource
from remy_finance.data.massive import MassiveSource
from remy_finance.execution.settlement import settle_at_expiration
from remy_finance.snapshots.builder import build_prospective, build_replay
from remy_finance.snapshots.schedule import SnapshotKind
from remy_finance.snapshots.store import GapRecordedError, SnapshotExistsError, SnapshotStore
from remy_finance.universe.selection import UniverseDecision
from tests import factories as f
from tests.factories import EXP, bull_call_spread, leg

D = Decimal


# --------------------------------------------------------------- F03: float cash inputs


@pytest.mark.parametrize("bad", [0.1, 2.5, True])
def test_f03_structure_factory_rejects_float_and_bool_cash(bad: object) -> None:
    with pytest.raises(ValueError, match="rejects"):
        Structure.price([leg("100", Right.CALL, Side.LONG)], [bad], StructureType.LONG_CALL, FEES_1)  # type: ignore[list-item]


@pytest.mark.parametrize("bad", [100.5, False])
def test_f03_contract_factory_rejects_float_and_bool_strike(bad: object) -> None:
    with pytest.raises(ValueError, match="rejects"):
        OptionContract.build("XYZ", EXP, bad, Right.CALL)  # type: ignore[arg-type]


def test_f03_pnl_and_settlement_reject_float_prices() -> None:
    with pytest.raises(ValueError):
        bull_call_spread().pnl_at_expiry(110.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        settle_at_expiration(bull_call_spread().legs, 1, expiration=EXP, official_close=110.0)  # type: ignore[arg-type]


def test_f03_exact_inputs_still_accepted() -> None:
    s = Structure.price(
        [leg("100", Right.CALL, Side.LONG)], ["0.1"], StructureType.LONG_CALL, FEES_1
    )
    assert s.net_premium == D(10) and s.capital_at_risk == D("10.65")
    assert OptionContract.build("XYZ", EXP, 100, Right.CALL).strike == D(100)


# --------------------------------------------------- F04: exactness at large prices


@pytest.mark.parametrize("s_t", ["105", "1e27", "1e29", "1e100", "1e300"])
def test_f04_bounded_spread_payoff_exact_at_any_terminal_price(s_t: str) -> None:
    s = bull_call_spread()  # +100C @2.50, -105C @1.00
    assert s.payoff_at_expiry(D(s_t)) == D(500)
    assert s.pnl_at_expiry(D(s_t)) == D("348.70")


def test_f04_unbounded_long_call_is_exact_not_rounded() -> None:
    s = Structure.price(
        [leg("100", Right.CALL, Side.LONG)], ["2.50"], StructureType.LONG_CALL, FEES_1
    )
    with localcontext() as ctx:
        ctx.prec = 200
        expected = (D("1e100") - 100) * 100 - D("250.65")
    assert s.pnl_at_expiry(D("1e100")) == expected


def test_f04_settlement_exact_at_large_close() -> None:
    st_ = settle_at_expiration(bull_call_spread().legs, 3, expiration=EXP, official_close=D("1e40"))
    assert st_.settlement_value == D(1500)


@given(
    st.integers(min_value=1, max_value=400),
    st.integers(min_value=1, max_value=50),
    st.integers(min_value=0, max_value=300),
)
def test_f04_bounded_verticals_keep_exact_max_gain_at_huge_prices(
    k: int, w: int, exponent: int
) -> None:
    s = Structure.price(
        [leg(str(k), Right.CALL, Side.LONG), leg(str(k + w), Right.CALL, Side.SHORT)],
        ["1.00", "0.40"],
        StructureType.VERTICAL_DEBIT,
        FEES_1,
    )
    big = D(10) ** exponent + k + w
    assert s.payoff_at_expiry(big) == D(w * 100)
    assert s.pnl_at_expiry(big) == D(w * 100) - s.net_premium - s.entry_fees


def test_f04_dates_unaffected() -> None:  # sanity: the change does not touch non-decimal fields
    assert bull_call_spread().expiration == date(2026, 11, 20)


# ------------------------------------- F02 / F06: prospective builder read guards

LIVE_AT = f.AS_OF + timedelta(seconds=3)


class _Live:
    def __init__(self, raise_in: str | None = None) -> None:
        self.raise_in = raise_in

    def live_chain_quotes(self, underlying: str) -> tuple[OptionQuote, ...]:
        if self.raise_in == "chain":
            raise LookaheadError("vendor returned a quote stamped after the request")
        return (f.option_quote(at=f.AS_OF),)

    def live_underlying_quote(self, symbol: str) -> UnderlyingQuote | None:
        if self.raise_in == "underlying":
            raise LookaheadError("vendor returned a quote stamped after the request")
        return f.underlying_quote(at=f.AS_OF)


def _prospective(live: _Live, events: FixtureSource) -> MarketState:
    return build_prospective(
        live,
        events,
        Session.OPEN,
        universe_version="U",
        underlyings=("XYZ",),
        clock=lambda: LIVE_AT,
    )


@pytest.mark.parametrize("where", ["chain", "underlying"])
def test_f02_live_lookahead_error_aborts_instead_of_becoming_note(where: str) -> None:
    with pytest.raises(LookaheadError):
        _prospective(_Live(raise_in=where), FixtureSource())


def _split(at: datetime) -> CorporateAction:
    return CorporateAction(
        symbol="XYZ",
        action_type=CorporateActionType.SPLIT,
        effective_date=at.date(),
        details=(("split_from", "1"), ("split_to", "2")),
        event_time=at,
        public_time=at,
        source="fixture",
    )


class _DividendsDown(FixtureSource):
    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        raise RuntimeError("dividend endpoint down")


def test_f06_dividend_failure_does_not_skip_independent_split_read() -> None:
    events = _DividendsDown([_split(f.AS_OF - timedelta(days=3))])
    board = _prospective(_Live(), events)
    assert len(board.corporate_actions) == 1
    assert [n.code for n in board.quality_notes] == ["DIVIDENDS:RuntimeError"]
    assert board.option_quotes and board.underlying_quotes


def test_f06_massive_keeps_splits_when_dividend_feed_fails() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if "splits" in req.url.path:
            row = {
                "execution_date": "2026-06-10",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
            }
            return httpx.Response(200, json={"results": [row]})
        if "events" in req.url.path:
            return httpx.Response(200, json={"results": {"events": []}})
        return httpx.Response(500, text="dividends down")

    src = MassiveSource("k", base_url="http://p", transport=httpx.MockTransport(h))
    acts = src.corporate_actions("XYZ", datetime(2026, 11, 6, 15, tzinfo=UTC))
    assert [a.action_type for a in acts] == [CorporateActionType.SPLIT]
    assert any("special dividends unavailable" in s for s in src.skipped)


# ------------------------------------------------ F01: one terminal state per slot

DAY = f.AS_OF.date()


def _replay_board(version: str) -> MarketState:
    src = FixtureSource([f.option_quote(at=f.AS_OF), f.underlying_quote(at=f.AS_OF)])
    return build_replay(src, DAY, Session.OPEN, universe_version=version, underlyings=("XYZ",))


def _slow_frames(monkeypatch: pytest.MonkeyPatch, pause: float = 0.3) -> None:
    real = store_mod._options_frame

    def slow(quotes):  # type: ignore[no-untyped-def]
        time.sleep(pause)  # widen the window between the slot checks and publication
        return real(quotes)

    monkeypatch.setattr(store_mod, "_options_frame", slow)


def test_f01_snapshot_slot_has_only_one_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _slow_frames(monkeypatch)
    store = SnapshotStore(tmp_path)
    boards = [_replay_board("U-A"), _replay_board("U-B")]  # different content, same slot
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def go(board: MarketState) -> None:
        barrier.wait()
        try:
            store.write(board)
            outcomes.append("stored")
        except SnapshotExistsError:
            outcomes.append("refused")

    threads = [threading.Thread(target=go, args=(b,)) for b in boards]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["refused", "stored"]
    assert (
        store.find(Mode.REPLAY, DAY, Session.OPEN, SnapshotKind.DECISION) is not None
    )  # exactly one


def test_f01_gap_and_snapshot_cannot_both_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SnapshotStore(tmp_path)
    real = store_mod._options_frame
    gap_outcome: list[str] = []
    blocked: list[bool] = []

    def record() -> None:
        try:
            store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "late")
            gap_outcome.append("gap")
        except SnapshotExistsError:
            gap_outcome.append("refused")

    def interleave(quotes):  # type: ignore[no-untyped-def]
        t = threading.Thread(target=record)  # a competing job tries to record a gap mid-write
        t.start()
        t.join(timeout=0.3)
        blocked.append(t.is_alive())  # it must be waiting on the slot lock
        interleave.thread = t  # type: ignore[attr-defined]
        return real(quotes)

    monkeypatch.setattr(store_mod, "_options_frame", interleave)
    board = build_prospective(
        _Live(),
        FixtureSource(),
        Session.OPEN,
        universe_version="U",
        underlyings=("XYZ",),
        clock=lambda: LIVE_AT,
    )
    store.write(board)
    interleave.thread.join()  # type: ignore[attr-defined]
    assert blocked == [True]
    assert gap_outcome == ["refused"]
    assert not store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)
    assert store.find(Mode.PROSPECTIVE, DAY, Session.OPEN, SnapshotKind.DECISION) is not None


def test_f01_snapshot_refused_after_gap_and_gap_is_idempotent(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    first = store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "terminal down")
    again = store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "other reason")
    assert again == first and len(store.gaps()) == 1
    board = build_prospective(
        _Live(),
        FixtureSource(),
        Session.OPEN,
        universe_version="U",
        underlyings=("XYZ",),
        clock=lambda: LIVE_AT,
    )
    with pytest.raises(GapRecordedError):
        store.write(board)


def test_f01_interrupted_gap_write_leaves_no_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SnapshotStore(tmp_path)

    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store_mod.os, "fsync", boom)
    with pytest.raises(OSError):
        store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "late")
    monkeypatch.undo()
    assert not store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)
    assert not list(tmp_path.rglob("*.partial"))
    store.record_gap(DAY, Session.OPEN, SnapshotKind.DECISION, "late")  # retry works
    assert store.has_gap(DAY, Session.OPEN, SnapshotKind.DECISION)


# -------------------------------- F07: atomic universe writes; F08: fixture types


def _decision() -> UniverseDecision:
    return UniverseDecision(
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


def test_f07_interrupted_universe_write_does_not_poison_final_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from remy_finance.universe.selection import UniverseStore

    store = UniverseStore(tmp_path)

    def boom(fd: int) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(os, "fsync", boom)  # the write must be durable before it is published
    with pytest.raises(OSError):
        store.write(_decision())
    monkeypatch.undo()
    assert not (tmp_path / "UNIV-1" / f"{DAY}.json").exists()
    assert not list(tmp_path.rglob("*.partial"))
    assert store.decisions() == []  # readers are not broken
    store.write(_decision())  # retry succeeds
    assert store.membership_for(DAY) == _decision()


def test_f07_existing_decision_is_never_replaced(tmp_path: Path) -> None:
    from remy_finance.universe.selection import UniverseStore

    store = UniverseStore(tmp_path)
    path = store.write(_decision())
    before = path.read_text()
    with pytest.raises(FileExistsError):
        store.write(_decision())
    assert path.read_text() == before


def test_f08_file_fixture_round_trip_includes_option_volume(tmp_path: Path) -> None:
    from remy_finance.core import OptionVolume
    from remy_finance.data import PointInTimeView
    from remy_finance.data.timeutil import et_at

    d = date(2026, 11, 5)
    t = et_at(d, 17, 15)
    vol = OptionVolume(
        symbol="XYZ", session_date=d, contracts=1234, event_time=t, public_time=t, source="fx"
    )
    path = tmp_path / "fixture.jsonl"
    FixtureSource([vol]).dump(path)
    reloaded = FixtureSource.load(path)
    assert PointInTimeView(reloaded, f.AS_OF).option_volume("XYZ", d) == vol
