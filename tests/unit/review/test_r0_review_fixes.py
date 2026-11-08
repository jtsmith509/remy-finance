"""Regression tests for the independent R0 review findings (docs/review/R0-findings.md).

Each test is named after its finding. They failed on the reviewed commit aeda0c2.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from remy_finance.core import FEES_1, OptionContract, Right, Side, Structure, StructureType
from remy_finance.execution.settlement import settle_at_expiration
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
