"""Paper fill engine (handoff R1.2-R1.3): journal events from boards, end to end."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from remy_finance.core import (
    DividendEvent,
    MarketState,
    Mode,
    OfficialClose,
    OptionQuote,
    Right,
    Session,
    UnderlyingQuote,
)
from remy_finance.data.timeutil import NEW_YORK
from remy_finance.execution.engine import (
    EngineError,
    ExitResult,
    PaperEngine,
    TradeSummary,
    official_close_time,
)
from remy_finance.execution.exit_policy import HOLD_1
from remy_finance.registry import EventKind, JournalEvent, Recommendation, Registry, registry_path
from tests.factories import EXP, contract
from tests.unit.registry.helpers import recommendation

D = Decimal
E = EventKind
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # recorded after everything happened

Book = dict[tuple[str, Right], tuple[str, str] | tuple[str, str, int, int]]


def et(d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=NEW_YORK).astimezone(UTC)


def board(
    at: datetime,
    *,
    spot: tuple[str, str] | None = ("99.98", "100.02"),
    book: Book | None = None,
    dividends: tuple[DividendEvent, ...] = (),
    session: Session = Session.OPEN,
) -> MarketState:
    qt = at - timedelta(seconds=2)
    book = (
        book
        if book is not None
        else {("100", Right.CALL): ("2.40", "2.50"), ("105", Right.CALL): ("1.00", "1.10")}
    )
    quotes = []
    for (k, r), v in book.items():
        bid, ask, bs, as_ = (*v, 50, 50) if len(v) == 2 else v  # type: ignore[misc]
        quotes.append(
            OptionQuote(
                contract=contract(k, r),
                bid=D(bid),
                ask=D(ask),
                bid_size=bs,
                ask_size=as_,
                quote_time=qt,
                source="t",
            )
        )
    uqs = ()
    if spot is not None:
        uqs = (
            UnderlyingQuote(
                symbol="XYZ",
                bid=D(spot[0]),
                ask=D(spot[1]),
                bid_size=500,
                ask_size=500,
                quote_time=qt,
                source="t",
            ),
        )
    return MarketState(
        session=session,
        as_of=at,
        mode=Mode.PROSPECTIVE,
        universe_version="T",
        underlyings=("XYZ",),
        underlying_quotes=uqs,
        option_quotes=tuple(quotes),
        dividends=dividends,
    )


def execution_of(decision: MarketState, **kw: Any) -> MarketState:
    kw.setdefault("session", decision.session)
    return board(decision.as_of + timedelta(minutes=5), **kw)


@pytest.fixture
def engine(tmp_path: Path) -> PaperEngine:
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    return PaperEngine(reg)


def open_trade(engine: PaperEngine, rec: Recommendation | None = None, **exec_kw: Any) -> str:
    rec = rec or recommendation()
    engine.registry.add_recommendation(rec)
    engine.submit(rec.trade_id, NOW)
    exec_kw.setdefault(
        "book", {("100", Right.CALL): ("2.50", "2.60"), ("105", Right.CALL): ("0.90", "1.00")}
    )
    engine.fill_entry(rec.trade_id, board(rec.decision_time + timedelta(minutes=5), **exec_kw), NOW)
    return rec.trade_id


def kinds(engine: PaperEngine, tid: str) -> list[EventKind]:
    return [e.kind for _, e in engine.registry.events(tid)]


def summary(engine: PaperEngine, tid: str) -> TradeSummary:
    ev = engine.registry.events(tid)[-1][1]
    assert ev.kind is E.TRADE_CLOSED
    return TradeSummary.from_canonical_json(ev.evidence or "")


def close(price: str, d: date = EXP) -> OfficialClose:
    t = official_close_time(d)
    return OfficialClose(
        symbol="XYZ",
        session_date=d,
        price=D(price),
        event_time=t,
        public_time=t + timedelta(hours=1, minutes=15),
        source="t",
    )


# ------------------------------------------------------------------ entry (R1.2 fixtures)


def test_entry_fills_at_the_execution_quote_not_the_snapshot(engine: PaperEngine) -> None:
    tid = open_trade(engine)  # decision priced 2.50 / 1.00; execution 2.60 ask / 0.90 bid
    events = [e for _, e in engine.registry.events(tid)]
    assert [e.kind for e in events] == [E.ORDER_SUBMITTED, E.LEG_FILLED, E.LEG_FILLED]
    assert [e.cash for e in events[1:]] == [D("-521.30"), D("178.70")]  # 2 x 100 x px - 1.30
    assert engine.position(tid).cash == D("-342.60")


def test_buy_5_with_ask_size_3_is_unfilled_and_closed(engine: PaperEngine) -> None:
    rec = recommendation(quantity=5)
    tid = open_trade(
        engine,
        rec,
        book={("100", Right.CALL): ("2.50", "2.60", 50, 3), ("105", Right.CALL): ("0.90", "1.00")},
    )
    assert kinds(engine, tid) == [E.ORDER_SUBMITTED, E.ORDER_UNFILLED, E.TRADE_CLOSED]
    s = summary(engine, tid)
    assert not s.entry_filled and s.realized_pnl == 0 and s.realized_return is None
    assert "INSUFFICIENT_SIZE" in engine.registry.events(tid)[1][1].note


def test_execution_gap_means_unfilled(engine: PaperEngine) -> None:
    rec = recommendation()
    engine.registry.add_recommendation(rec)
    engine.submit(rec.trade_id, NOW)
    out = engine.fill_entry(rec.trade_id, None, NOW)
    assert [e.kind for e in out] == [E.ORDER_UNFILLED, E.TRADE_CLOSED]
    assert out[0].event_time == rec.decision_time + timedelta(minutes=5)


def test_wrong_execution_board_rejected(engine: PaperEngine) -> None:
    rec = recommendation()
    engine.registry.add_recommendation(rec)
    engine.submit(rec.trade_id, NOW)
    with pytest.raises(EngineError, match="outside"):
        engine.fill_entry(rec.trade_id, board(rec.decision_time), NOW)  # the snapshot itself
    with pytest.raises(EngineError, match="outside"):
        engine.fill_entry(rec.trade_id, board(rec.decision_time + timedelta(minutes=30)), NOW)
    with pytest.raises(EngineError, match="submit"):
        other = recommendation(engine="RAND-1")
        engine.registry.add_recommendation(other)
        engine.fill_entry(other.trade_id, None, NOW)


def test_steps_are_idempotent(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    rec = engine.position(tid).rec
    assert engine.submit(tid, NOW) == []
    assert engine.fill_entry(tid, None, NOW) == []
    d = board(et(date(2026, 11, 9), 10, 0))
    assert engine.on_snapshot(tid, d, execution_of(d), NOW) == []
    n = len(engine.registry.rows("journal"))
    assert engine.on_snapshot(tid, d, execution_of(d), NOW) == []
    assert len(engine.registry.rows("journal")) == n
    assert rec.exit_policy_id == HOLD_1.policy_id


# ------------------------------------------------------------------ HOLD-1 to expiration


def test_hold_to_expiration_settles_under_exp1(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    for day in (date(2026, 11, 9), date(2026, 11, 19), EXP):
        for sess, hh, mm in ((Session.OPEN, 10, 0), (Session.CLOSE, 15, 45)):
            d = board(et(day, hh, mm), session=sess)
            assert engine.on_snapshot(tid, d, execution_of(d), NOW) == []  # OTM short: nothing
    out = engine.settle(tid, close("103"), NOW)
    assert [e.kind for e in out] == [E.EXPIRATION_SETTLED, E.TRADE_CLOSED]
    assert out[0].cash == D("600")  # long 100C: 3 x 200; short 105C expires
    assert out[0].public_time is not None
    s = summary(engine, tid)
    assert s.realized_pnl == D("257.40")
    assert s.capital_at_risk == D("302.60")
    assert s.realized_return == (D("257.40") / D("302.60")).quantize(D("1E-12"))
    assert s.deviation == s.realized_return - D("-0.04")  # type: ignore[operator]
    assert engine.registry.verify().ok
    assert engine.settle(tid, close("103"), NOW) == []


def test_settle_checks_symbol_date_and_order(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    with pytest.raises(EngineError, match="official close"):
        engine.settle(tid, close("103", date(2026, 11, 19)), NOW)
    d = board(et(date(2026, 11, 23), 10, 0))
    with pytest.raises(EngineError, match="past expiration"):
        engine.on_snapshot(tid, d, execution_of(d), NOW)


# ------------------------------------------------------------------ ASSIGN-1 and forced close

ITM = {("100", Right.CALL): ("9.90", "10.10"), ("105", Right.CALL): ("5.02", "5.20")}


def test_assignment_then_forced_close_at_next_snapshot(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    d1 = board(
        et(date(2026, 11, 9), 15, 45), spot=("109.98", "110.02"), book=ITM, session=Session.CLOSE
    )
    out = engine.on_snapshot(tid, d1, execution_of(d1, book=ITM), NOW)
    assert [e.kind for e in out] == [E.ASSIGNMENT_ASSUMED]
    assert out[0].cash == D("21000")  # deliver 200 shares at the 105 strike
    pos = engine.position(tid)
    assert pos.stock_shares == -200 and len(pos.open_legs) == 1

    d2 = board(et(date(2026, 11, 10), 10, 0), spot=("110.00", "110.10"), book=ITM)
    out = engine.on_snapshot(tid, d2, execution_of(d2, spot=("110.00", "110.10"), book=ITM), NOW)
    assert [e.kind for e in out] == [E.EXIT_SUBMITTED, E.EXIT_FILLED, E.TRADE_CLOSED]
    r = ExitResult.from_canonical_json(out[1].evidence or "")
    assert r.forced and r.stock is not None and r.stock.shares == 200
    assert out[1].cash == D("-22020") + D("1978.70")  # buy stock at ask; sell 100C at bid
    assert summary(engine, tid).realized_pnl == D("616.10")
    assert engine.registry.verify().ok


def test_forced_close_retries_unfilled_options_and_closes_stock_first(
    engine: PaperEngine,
) -> None:
    tid = open_trade(engine)
    d1 = board(
        et(date(2026, 11, 9), 15, 45), spot=("109.98", "110.02"), book=ITM, session=Session.CLOSE
    )
    engine.on_snapshot(tid, d1, None, NOW)
    thin = {("100", Right.CALL): ("9.90", "10.10", 1, 50)}  # bid size 1 < 2 contracts
    d2 = board(et(date(2026, 11, 10), 10, 0), spot=("110.00", "110.10"), book=ITM)
    out = engine.on_snapshot(tid, d2, execution_of(d2, spot=("110.00", "110.10"), book=thin), NOW)
    assert [e.kind for e in out] == [E.EXIT_SUBMITTED, E.EXIT_UNFILLED]
    assert out[1].cash == D("-22020")  # the stock still closed (DR-0009)
    assert engine.position(tid).stock_shares == 0
    d3 = board(et(date(2026, 11, 10), 15, 45), book=ITM, session=Session.CLOSE)
    out = engine.on_snapshot(tid, d3, None, NOW)  # execution gap: unfilled again
    assert [e.kind for e in out] == [E.EXIT_SUBMITTED, E.EXIT_UNFILLED]
    d4 = board(et(date(2026, 11, 11), 10, 0), book=ITM)
    out = engine.on_snapshot(tid, d4, execution_of(d4, book=ITM), NOW)
    assert [e.kind for e in out] == [E.EXIT_SUBMITTED, E.EXIT_FILLED, E.TRADE_CLOSED]
    assert summary(engine, tid).exit_attempts == 3
    assert engine.registry.verify().ok


def test_dividend_assignment_owes_the_dividend_on_short_stock(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    pub = et(date(2026, 10, 20), 16, 0)
    div = DividendEvent(
        symbol="XYZ",
        ex_date=date(2026, 11, 10),
        amount=D("0.50"),
        event_time=pub,
        public_time=pub,
        source="t",
    )
    book = {("100", Right.CALL): ("9.90", "10.10"), ("105", Right.CALL): ("5.20", "5.40")}
    d1 = board(
        et(date(2026, 11, 9), 15, 45),
        spot=("109.98", "110.02"),
        book=book,
        dividends=(div,),
        session=Session.CLOSE,
    )  # extrinsic 0.20 < 0.50 dividend
    out = engine.on_snapshot(tid, d1, None, NOW)
    assert out[0].kind is E.ASSIGNMENT_ASSUMED and "DIVIDEND" in out[0].note
    d2 = board(et(date(2026, 11, 10), 10, 0), book=book, dividends=(div,))
    out = engine.on_snapshot(
        tid, d2, execution_of(d2, spot=("109.50", "109.60"), book=book, dividends=(div,)), NOW
    )
    r = ExitResult.from_canonical_json(out[1].evidence or "")
    assert r.dividend_owed == D("100")  # 0.50 x 200 shares short over the ex-date


def test_no_assign1_at_the_expiration_close_snapshot(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    d = board(et(EXP, 15, 45), spot=("109.98", "110.02"), book=ITM, session=Session.CLOSE)
    assert engine.on_snapshot(tid, d, execution_of(d, book=ITM), NOW) == []
    out = engine.settle(tid, close("110"), NOW)
    assert out[0].cash == D("1000")  # 10 x 200 - 5 x 200


def test_missing_underlying_quote_is_annotated_not_ignored(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    d = board(et(date(2026, 11, 9), 10, 0), spot=None)
    out = engine.on_snapshot(tid, d, None, NOW)
    assert [e.kind for e in out] == [E.ANNOTATION]
    assert "not assessed" in out[0].note


def test_board_mode_must_match(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    d = board(et(date(2026, 11, 9), 10, 0)).model_copy(update={"mode": Mode.REPLAY})
    with pytest.raises(EngineError, match="REPLAY"):
        engine.on_snapshot(tid, d, None, NOW)


def test_every_event_is_valid_journal(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    engine.settle(tid, close("90"), NOW)
    events: list[JournalEvent] = [e for _, e in engine.registry.events(tid)]
    assert sum((e.cash for e in events), D(0)) == summary(engine, tid).realized_pnl == D("-342.60")


def test_resume_after_crash_between_submit_and_fill(engine: PaperEngine) -> None:
    tid = open_trade(engine)
    d1 = board(
        et(date(2026, 11, 9), 15, 45), spot=("109.98", "110.02"), book=ITM, session=Session.CLOSE
    )
    engine.on_snapshot(tid, d1, None, NOW)
    d2 = board(et(date(2026, 11, 10), 10, 0), book=ITM)
    rec = engine.position(tid).rec
    engine.registry.add_event(  # the crashed run got this far
        JournalEvent(
            trade_id=tid,
            mode=rec.mode,
            kind=E.EXIT_SUBMITTED,
            event_time=d2.as_of,
            recorded_at=NOW,
            note="forced close after assumed assignment",
        )
    )
    out = engine.on_snapshot(tid, d2, execution_of(d2, book=ITM), NOW)
    assert [e.kind for e in out] == [E.EXIT_FILLED, E.TRADE_CLOSED]
    assert engine.registry.verify().ok


class ExitAtOpen:
    """A test-only discretionary policy: exit at the first managed snapshot."""

    policy_id = "HOLD-1"  # reuses the id so the recommendation validates
    closing_trade_required = False

    def wants_exit(self, position: object, board: MarketState) -> bool:
        return True


def test_exit_policy_interface_drives_discretionary_exits(tmp_path: Path) -> None:
    reg = Registry.create(registry_path(tmp_path, Mode.PROSPECTIVE), Mode.PROSPECTIVE)
    engine = PaperEngine(reg, exit_policies={"HOLD-1": ExitAtOpen()})
    tid = open_trade(engine)
    d = board(et(date(2026, 11, 9), 10, 0))
    out = engine.on_snapshot(tid, d, execution_of(d), NOW)
    assert [e.kind for e in out] == [E.EXIT_SUBMITTED, E.EXIT_FILLED, E.TRADE_CLOSED]
    r = ExitResult.from_canonical_json(out[1].evidence or "")
    assert not r.forced and r.stock is None
    assert out[1].cash == D("2.40") * 200 - D("1.10") * 200 - D("2.60")  # sell 100C, buy 105C
