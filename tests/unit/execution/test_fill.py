"""FILL-1 cost-model tests, including the handoff R1.2 fixtures (pure parts)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from remy_finance.core import Leg, OptionQuote, Right, Side, UnderlyingQuote
from remy_finance.execution.fill import TradeSide, UnfilledReason, close_stock, fill_order
from tests.factories import contract, leg

D = Decimal
T = datetime(2026, 11, 6, 15, 5, tzinfo=UTC)


def q(strike: str, right: Right, bid: str, ask: str, bs: int = 50, as_: int = 50) -> OptionQuote:
    return OptionQuote(
        contract=contract(strike, right),
        bid=D(bid),
        ask=D(ask),
        bid_size=bs,
        ask_size=as_,
        quote_time=T,
        source="exec",
    )


def book(*quotes: OptionQuote) -> dict[str, OptionQuote]:
    return {x.contract.osi_symbol: x for x in quotes}


def condor_legs() -> list[Leg]:
    return [
        leg("95", Right.PUT, Side.SHORT),
        leg("90", Right.PUT, Side.LONG),
        leg("105", Right.CALL, Side.SHORT),
        leg("110", Right.CALL, Side.LONG),
    ]


def test_fixture_buy_5_with_ask_size_3_unfilled() -> None:
    f = fill_order(
        [leg("100", Right.CALL, Side.LONG)],
        5,
        book(q("100", Right.CALL, "2.40", "2.50", as_=3)),
        opening=True,
    )
    assert not f.filled and f.legs[0].reason is UnfilledReason.INSUFFICIENT_SIZE
    assert f.net_cash == 0 and f.fees == 0


def test_fixture_condor_one_leg_short_size_whole_order_unfilled() -> None:
    quotes = book(
        q("95", Right.PUT, "1.20", "1.30"),
        q("90", Right.PUT, "0.30", "0.40"),
        q("105", Right.CALL, "1.00", "1.10", bs=1),  # we sell 2: bid size 1 is short
        q("110", Right.CALL, "0.30", "0.35"),
    )
    f = fill_order(condor_legs(), 2, quotes, opening=True)
    assert not f.filled
    assert [x.filled for x in f.legs] == [True, True, False, True]
    assert f.net_cash == 0


def test_fixture_fill_uses_the_quotes_given_execution_not_snapshot() -> None:
    snapshot = book(q("100", Right.CALL, "2.40", "2.50"))
    execution = book(q("100", Right.CALL, "2.55", "2.65"))
    legs = [leg("100", Right.CALL, Side.LONG)]
    f = fill_order(legs, 1, execution, opening=True)
    assert f.filled and f.legs[0].price == D("2.65")
    assert fill_order(legs, 1, snapshot, opening=True).legs[0].price == D("2.50")


def test_condor_open_cash_and_fees_match_r06_fixture() -> None:
    quotes = book(
        q("95", Right.PUT, "1.20", "1.30"),
        q("90", Right.PUT, "0.30", "0.40"),
        q("105", Right.CALL, "1.00", "1.10"),
        q("110", Right.CALL, "0.30", "0.35"),
    )
    f = fill_order(condor_legs(), 1, quotes, opening=True)
    assert (
        f.filled and f.premium_cash == D(145) and f.fees == D("2.60") and f.net_cash == D("142.40")
    )


def test_closing_reverses_sides() -> None:
    quotes = book(q("100", Right.CALL, "3.00", "3.10"), q("105", Right.CALL, "1.20", "1.30"))
    legs = [leg("100", Right.CALL, Side.LONG), leg("105", Right.CALL, Side.SHORT)]
    f = fill_order(legs, 2, quotes, opening=False)
    assert [x.trade_side for x in f.legs] == [TradeSide.SELL, TradeSide.BUY]
    assert f.premium_cash == D(2 * 300 - 2 * 130) and f.fees == D("2.60")


def test_missing_quote_and_zero_bid_unfilled() -> None:
    legs = [leg("100", Right.CALL, Side.LONG)]
    assert fill_order(legs, 1, {}, opening=True).legs[0].reason is UnfilledReason.NO_QUOTE
    worthless = book(q("100", Right.CALL, "0", "0.05"))
    f = fill_order(legs, 1, worthless, opening=False)  # sell at a zero bid
    assert not f.filled and f.legs[0].reason is UnfilledReason.NO_PRICE


def test_exact_size_fills() -> None:
    f = fill_order(
        [leg("100", Right.CALL, Side.LONG, ratio=2)],
        3,
        book(q("100", Right.CALL, "1", "1.1", as_=6)),
        opening=True,
    )
    assert f.filled and f.legs[0].contracts == 6


def test_mismatched_quote_key_rejected() -> None:
    with pytest.raises(ValueError):
        fill_order(
            [leg("100", Right.CALL, Side.LONG)],
            1,
            {contract("100").osi_symbol: q("105", Right.CALL, "1", "1.1")},
            opening=True,
        )


def test_close_stock_crosses_spread() -> None:
    uq = UnderlyingQuote(
        symbol="XYZ",
        bid=D("99.98"),
        ask=D("100.02"),
        bid_size=1,
        ask_size=1,
        quote_time=T,
        source="exec",
    )
    short = close_stock(-200, uq)
    assert short.shares == 200 and short.price == D("100.02") and short.cash == D("-20004")
    long_ = close_stock(100, uq)
    assert long_.shares == -100 and long_.price == D("99.98") and long_.cash == D("9998")
