"""R0.6 required fixtures (handoff Section 5): payoff, capital at risk and fees.

Multiplier 100; fees $0.65/contract/side (FEES-1); exercise fee $0; prices are the
fill prices under the spread-crossing rule.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from remy_finance.core import (
    FEES_1,
    Right,
    Side,
    Structure,
    StructureType,
    UndefinedRiskError,
)
from remy_finance.core.payoff import intrinsic
from tests.factories import leg

C, P = Right.CALL, Right.PUT
L, S = Side.LONG, Side.SHORT
D = Decimal


def long_call() -> Structure:
    return Structure.price([leg("100", C, L)], ["2.50"], StructureType.LONG_CALL, FEES_1)


def bull_call_debit() -> Structure:
    return Structure.price(
        [leg("100", C, L), leg("105", C, S)], ["2.50", "1.00"], StructureType.VERTICAL_DEBIT, FEES_1
    )


def bull_put_credit() -> Structure:
    return Structure.price(
        [leg("95", P, S), leg("90", P, L)], ["1.20", "0.40"], StructureType.VERTICAL_CREDIT, FEES_1
    )


def iron_condor() -> Structure:
    return Structure.price(
        [leg("95", P, S), leg("90", P, L), leg("105", C, S), leg("110", C, L)],
        ["1.20", "0.40", "1.00", "0.35"],
        StructureType.IRON_CONDOR,
        FEES_1,
    )


def long_call_butterfly() -> Structure:
    return Structure.price(
        [leg("95", C, L), leg("100", C, S, ratio=2), leg("105", C, L)],
        ["6.20", "2.40", "0.70"],
        StructureType.LONG_CALL_BUTTERFLY,
        FEES_1,
    )


# R0 amendment (DR-0035): long-volatility debit structures, fixtures from Josh's table.
def long_straddle() -> Structure:
    return Structure.price(
        [leg("100", C, L), leg("100", P, L)], ["2.50", "2.30"], StructureType.LONG_STRADDLE, FEES_1
    )


def long_strangle() -> Structure:
    return Structure.price(
        [leg("105", C, L), leg("95", P, L)], ["1.05", "1.25"], StructureType.LONG_STRANGLE, FEES_1
    )


def long_iron_condor() -> Structure:
    return Structure.price(
        [leg("95", P, L), leg("90", P, S), leg("105", C, L), leg("110", C, S)],
        ["1.25", "0.40", "1.05", "0.35"],
        StructureType.LONG_IRON_CONDOR,
        FEES_1,
    )


def long_iron_butterfly() -> Structure:
    return Structure.price(
        [leg("100", C, L), leg("100", P, L), leg("105", C, S), leg("95", P, S)],
        ["2.50", "2.30", "1.00", "1.20"],
        StructureType.LONG_IRON_BUTTERFLY,
        FEES_1,
    )


# (builder, capital at risk, [(S_T, expected P&L), ...]) -- straight from the handoff table
FIXTURES = [
    (long_call, "250.65", [("110", "749.35"), ("95", "-250.65")]),
    (bull_call_debit, "151.30", [("105", "348.70"), ("120", "348.70"), ("102", "48.70")]),
    (bull_put_credit, "421.30", [("92", "-221.30"), ("95", "78.70"), ("150", "78.70")]),
    (iron_condor, "357.60", [("100", "142.40"), ("112", "-357.60")]),
    (long_call_butterfly, "212.60", [("100", "287.40"), ("90", "-212.60")]),
    # R0 amendment (DR-0035)
    (
        long_straddle,
        "481.30",
        [
            ("100", "-481.30"),
            ("110", "518.70"),
            ("90", "518.70"),
            ("95", "18.70"),
            ("0", "9518.70"),
        ],
    ),
    (
        long_strangle,
        "231.30",
        [
            ("95", "-231.30"),
            ("100", "-231.30"),
            ("105", "-231.30"),
            ("110", "268.70"),
            ("90", "268.70"),
        ],
    ),
    (
        long_iron_condor,
        "157.60",
        [
            ("95", "-157.60"),
            ("100", "-157.60"),
            ("105", "-157.60"),
            ("110", "342.40"),
            ("150", "342.40"),
            ("90", "342.40"),
            ("0", "342.40"),
        ],
    ),
    (
        long_iron_butterfly,
        "262.60",
        [
            ("100", "-262.60"),
            ("105", "237.40"),
            ("150", "237.40"),
            ("95", "237.40"),
            ("0", "237.40"),
        ],
    ),
]


@pytest.mark.parametrize(
    ("build", "car", "points"), FIXTURES, ids=lambda x: getattr(x, "__name__", "")
)
def test_handoff_fixture(build: object, car: str, points: list[tuple[str, str]]) -> None:
    assert callable(build)
    s: Structure = build()
    assert s.capital_at_risk == D(car)
    for s_t, pnl in points:
        assert s.pnl_at_expiry(D(s_t)) == D(pnl), f"S_T={s_t}"


@pytest.mark.parametrize(
    ("build", "car", "points"), FIXTURES, ids=lambda x: getattr(x, "__name__", "")
)
def test_worst_case_equals_capital_at_risk(build: object, car: str, points: object) -> None:
    assert callable(build)
    s: Structure = build()
    grid = [D(x) / 4 for x in range(0, 4 * 300)]
    worst = min(s.pnl_at_expiry(x) for x in grid)
    assert worst == -s.capital_at_risk


def test_risk_numbers_of_fixtures() -> None:
    s = long_call()
    assert (s.max_loss, s.max_gain, s.entry_fees, s.exit_fees) == (D(250), None, D("0.65"), D(0))
    s = bull_call_debit()
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(150),
        D(150),
        D(350),
        D("1.30"),
    )
    s = bull_put_credit()
    assert (s.net_premium, s.max_loss, s.max_gain) == (D(-80), D(420), D(80))
    s = iron_condor()
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(-145),
        D(355),
        D(145),
        D("2.60"),
    )
    s = long_call_butterfly()
    assert (s.contracts_per_unit, s.entry_fees, s.max_loss, s.max_gain) == (
        4,
        D("2.60"),
        D(210),
        D(290),
    )


def test_risk_numbers_of_long_volatility_fixtures() -> None:
    s = long_straddle()
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(480),
        D(480),
        None,
        D("1.30"),
    )
    s = long_strangle()
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(230),
        D(230),
        None,
        D("1.30"),
    )
    s = long_iron_condor()  # debit 155; max payoff 500 (one 5-wide wing in the money)
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(155),
        D(155),
        D(345),
        D("2.60"),
    )
    s = long_iron_butterfly()  # debit 260; max payoff 500
    assert (s.net_premium, s.max_loss, s.max_gain, s.entry_fees) == (
        D(260),
        D(260),
        D(240),
        D("2.60"),
    )
    for build in (long_straddle, long_strangle, long_iron_condor, long_iron_butterfly):
        assert build().exit_fees == 0  # HOLD-1: no closing trade, so no exit fees in CaR


def test_quantity_scales_linearly() -> None:
    s = bull_call_debit()
    assert s.pnl_at_expiry(D(102), quantity=3) == 3 * s.pnl_at_expiry(D(102))


def test_naked_short_call_rejected() -> None:
    with pytest.raises(UndefinedRiskError, match="unbounded"):
        Structure.price([leg("100", C, S)], ["2.40"], StructureType.OTHER_DEFINED_RISK, FEES_1)


def test_call_ratio_spread_rejected() -> None:
    # +1 100C / -2 105C: net short one call above 105 -> unbounded loss.
    with pytest.raises(UndefinedRiskError):
        Structure.price(
            [leg("100", C, L), leg("105", C, S, ratio=2)],
            ["2.5", "1"],
            StructureType.OTHER_DEFINED_RISK,
            FEES_1,
        )


def test_naked_short_put_is_bounded_but_large() -> None:
    # Loss of a short put is bounded (S_T >= 0): K*100 - premium. Admitted by the maths;
    # movegen (R0.7) only generates the MOVEGEN-1 structure types.
    s = Structure.price([leg("100", P, S)], ["2.40"], StructureType.OTHER_DEFINED_RISK, FEES_1)
    assert s.max_loss == D(9760) and s.capital_at_risk == D("9760.65")


def test_unbounded_structure_cannot_be_constructed_directly() -> None:
    s = long_call()
    with pytest.raises(ValidationError, match="unbounded"):
        s.model_copy(update={"legs": (leg("100", C, S),)})


def test_closing_trade_adds_exit_fees() -> None:
    s = Structure.price(
        [leg("100", C, L), leg("105", C, S)],
        ["2.50", "1.00"],
        StructureType.VERTICAL_DEBIT,
        FEES_1,
        closing_trade_required=True,
    )
    assert s.exit_fees == D("1.30") and s.capital_at_risk == D("152.60")


@pytest.mark.parametrize(
    ("right", "k", "s", "v"),
    [
        (C, "100", "110", "10"),
        (C, "100", "90", "0"),
        (P, "100", "90", "10"),
        (P, "100", "110", "0"),
        (C, "100", "100", "0"),
    ],
)
def test_intrinsic(right: Right, k: str, s: str, v: str) -> None:
    assert intrinsic(right, D(k), D(s)) == D(v)


def test_negative_underlying_rejected() -> None:
    with pytest.raises(ValueError):
        intrinsic(C, D(100), D(-1))
