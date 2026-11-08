"""R0.10 EXP-1 tests: exercise-by-exception, settlement at intrinsic, pin-risk flag."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remy_finance.core import FeeSchedule, Right, Side
from remy_finance.execution.settlement import SettlementFlag, settle_at_expiration
from tests.factories import EXP, bull_call_spread, leg

D = Decimal


def test_condor_settles_like_terminal_payoff() -> None:
    legs = [
        leg("95", Right.PUT, Side.SHORT),
        leg("90", Right.PUT, Side.LONG),
        leg("105", Right.CALL, Side.SHORT),
        leg("110", Right.CALL, Side.LONG),
    ]
    s = settle_at_expiration(legs, 2, expiration=EXP, official_close=D("112"))
    # calls: -2x100x7 + 2x100x2 = -1000; puts expire worthless
    assert s.settlement_value == D(-1000)
    assert [x.exercised for x in s.legs] == [False, False, True, True]
    assert s.flags == ()


def test_exercise_by_exception_threshold() -> None:
    long_call = [leg("100", Right.CALL, Side.LONG)]
    one_cent = settle_at_expiration(long_call, 1, expiration=EXP, official_close=D("100.01"))
    sub_cent = settle_at_expiration(long_call, 1, expiration=EXP, official_close=D("100.009"))
    assert one_cent.legs[0].exercised and one_cent.settlement_value == D(1)
    assert not sub_cent.legs[0].exercised and sub_cent.settlement_value == 0


@pytest.mark.parametrize(
    ("close", "flagged"),
    [("104.75", True), ("105.25", True), ("105", True), ("104.74", False), ("105.26", False)],
)
def test_pin_risk_flag_for_short_legs(close: str, flagged: bool) -> None:
    s = settle_at_expiration(bull_call_spread().legs, 1, expiration=EXP, official_close=D(close))
    assert (SettlementFlag.PIN_RISK_APPROXIMATE in s.flags) is flagged


def test_long_leg_near_strike_not_flagged() -> None:
    s = settle_at_expiration(
        [leg("100", Right.CALL, Side.LONG)], 1, expiration=EXP, official_close=D("100.10")
    )
    assert s.flags == ()


def test_exercise_fees_counted_per_exercised_contract() -> None:
    fees = FeeSchedule(
        version="TEST",
        option_per_contract_per_side=D("0.65"),
        exercise_assignment_per_contract=D("5"),
        stock_per_share=D(0),
    )
    s = settle_at_expiration(
        bull_call_spread().legs, 3, expiration=EXP, official_close=D(110), fees=fees
    )
    assert s.exercise_assignment_fees == D(30)  # both legs ITM, 3 contracts each
    assert s.fee_version == "TEST"


def test_wrong_expiration_rejected() -> None:
    with pytest.raises(ValueError):
        settle_at_expiration(
            bull_call_spread().legs, 1, expiration=EXP.replace(day=21), official_close=D(100)
        )


@given(
    st.decimals(min_value=0, max_value=500, places=2, allow_nan=False, allow_infinity=False),
    st.integers(1, 10),
)
def test_settlement_matches_structure_payoff_on_cent_prices(close: Decimal, qty: int) -> None:
    # With cent-precision closes, exercise-by-exception equals the terminal payoff, and the
    # structure's held-to-expiry P&L reconciles: settlement - premium - entry fees.
    s = bull_call_spread()
    st_ = settle_at_expiration(s.legs, qty, expiration=EXP, official_close=close)
    assert st_.settlement_value == s.payoff_at_expiry(close) * qty
    pnl = st_.settlement_value - (s.net_premium + s.entry_fees) * qty - st_.exercise_assignment_fees
    assert pnl == s.pnl_at_expiry(close, qty)
