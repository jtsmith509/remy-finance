"""R0.9 ASSIGN-1 fixtures (handoff Section 5) plus edge cases."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from remy_finance.core import DividendEvent, LookaheadError, Right, Side
from remy_finance.execution.assignment import (
    AssignmentAssessment,
    AssignmentReason,
    assess_short_leg,
    assignment_consequence,
)
from tests.factories import leg

D = Decimal
AS_OF = datetime(2026, 11, 13, 20, 45, tzinfo=UTC)  # Fri 15:45 ET
NEXT = date(2026, 11, 16)  # Monday


def div(
    amount: str, ex: date = NEXT, public: datetime = AS_OF - timedelta(days=20)
) -> DividendEvent:
    return DividendEvent(
        symbol="XYZ",
        ex_date=ex,
        amount=D(amount),
        event_time=public,
        public_time=public,
        source="fixture",
    )


def short_call_100(**kw: Any) -> AssignmentAssessment:
    return assess_short_leg(
        leg("100", Right.CALL, Side.SHORT),
        option_bid=D("4.05"),
        underlying_price=D(104),
        as_of=AS_OF,
        next_session=NEXT,
        **kw,
    )


def test_fixture_short_call_dividend_assigned() -> None:
    a = short_call_100(known_dividends=[div("0.25")])
    assert a.extrinsic == D("0.05")
    assert a.assigned and a.reason is AssignmentReason.DIVIDEND


def test_fixture_short_call_small_dividend_not_assigned() -> None:
    a = short_call_100(known_dividends=[div("0.04")])
    # dividend 0.04 <= extrinsic 0.05; threshold: 0.05 < 0.05 is false
    assert not a.assigned and a.reason is None


def test_fixture_short_put_threshold_assigned() -> None:
    a = assess_short_leg(
        leg("100", Right.PUT, Side.SHORT),
        option_bid=D("9.98"),
        underlying_price=D(90),
        as_of=AS_OF,
        next_session=NEXT,
    )
    assert a.intrinsic == D(10) and a.extrinsic == D(0)
    assert a.assigned and a.reason is AssignmentReason.EXTRINSIC_THRESHOLD


@pytest.mark.parametrize(
    ("right", "s", "bid"),
    [
        (Right.CALL, "95", "0.01"),
        (Right.PUT, "105", "0.00"),
        (Right.CALL, "100", "0.00"),
        (Right.PUT, "100", "0.00"),
    ],
)
def test_out_of_or_at_the_money_never_assigned(right: Right, s: str, bid: str) -> None:
    a = assess_short_leg(
        leg("100", right, Side.SHORT),
        option_bid=D(bid),
        underlying_price=D(s),
        as_of=AS_OF,
        next_session=NEXT,
        known_dividends=[div("5.00")],
    )
    assert not a.assigned


def test_threshold_is_strict() -> None:
    at = assess_short_leg(
        leg("100", Right.PUT, Side.SHORT),
        option_bid=D("10.05"),
        underlying_price=D(90),
        as_of=AS_OF,
        next_session=NEXT,
    )
    below = assess_short_leg(
        leg("100", Right.PUT, Side.SHORT),
        option_bid=D("10.0499"),
        underlying_price=D(90),
        as_of=AS_OF,
        next_session=NEXT,
    )
    assert not at.assigned and below.assigned


def test_dividend_rule_only_for_calls_and_next_session() -> None:
    put = assess_short_leg(
        leg("100", Right.PUT, Side.SHORT),
        option_bid=D("10.50"),
        underlying_price=D(90),
        as_of=AS_OF,
        next_session=NEXT,
        known_dividends=[div("1.00")],
    )
    assert not put.assigned  # extrinsic 0.50, puts ignore the dividend rule
    later = short_call_100(known_dividends=[div("0.25", ex=NEXT + timedelta(days=1))])
    assert not later.assigned


def test_unknown_dividend_is_lookahead() -> None:
    with pytest.raises(LookaheadError):
        short_call_100(known_dividends=[div("0.25", public=AS_OF + timedelta(minutes=1))])


def test_missing_quote_is_conservative() -> None:
    a = assess_short_leg(
        leg("100", Right.CALL, Side.SHORT),
        option_bid=None,
        underlying_price=D("100.50"),
        as_of=AS_OF,
        next_session=NEXT,
    )
    assert a.extrinsic == 0 and a.assigned


def test_long_legs_rejected() -> None:
    with pytest.raises(ValueError):
        assess_short_leg(
            leg("100", Right.CALL, Side.LONG),
            option_bid=D(1),
            underlying_price=D(104),
            as_of=AS_OF,
            next_session=NEXT,
        )


def test_consequence_short_call_owes_dividend() -> None:
    c = assignment_consequence(
        leg("100", Right.CALL, Side.SHORT), 2, dividend_per_share_before_close=D("0.25")
    )
    assert c.stock_shares == -200 and c.strike_cash == D(20000) and c.dividend_owed == D(50)


def test_consequence_short_put_takes_stock_no_dividend_owed() -> None:
    c = assignment_consequence(
        leg("100", Right.PUT, Side.SHORT), 1, dividend_per_share_before_close=D("0.25")
    )
    assert c.stock_shares == 100 and c.strike_cash == D(-10000) and c.dividend_owed == 0
