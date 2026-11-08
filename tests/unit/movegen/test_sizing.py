"""SIZE-1 (DR-0006): quantity = min(floor(2000 / CaR), floor(10 / largest leg ratio))."""

from decimal import Decimal

import pytest

from remy_finance.core import FEES_1, Right, Side, Structure, StructureType
from remy_finance.movegen.config import SIZE_1, MovegenConfig, WidthBand
from tests.factories import leg

D = Decimal


def long_call(price: str) -> Structure:
    return Structure.price(
        [leg("100", Right.CALL, Side.LONG)], [price], StructureType.LONG_CALL, FEES_1
    )


@pytest.mark.parametrize(
    ("price", "qty"),
    [("2.50", 7), ("10.00", 1), ("19.99", 1), ("20.00", 0), ("0.20", 10), ("1.99", 10)],
)
def test_budget_and_cap(price: str, qty: int) -> None:
    # CaR = 100 x price + 0.65; e.g. 2.50 -> 250.65 -> floor(2000/250.65) = 7
    assert SIZE_1.quantity(long_call(price)) == qty


def test_ratio_two_leg_caps_at_five() -> None:
    fly = Structure.price(
        [
            leg("95", Right.CALL, Side.LONG),
            leg("100", Right.CALL, Side.SHORT, 2),
            leg("105", Right.CALL, Side.LONG),
        ],
        ["0.10", "0.04", "0.01"],
        StructureType.LONG_CALL_BUTTERFLY,
        FEES_1,
    )
    assert SIZE_1.quantity(fly) == 5


def test_width_must_sit_on_lattice() -> None:
    with pytest.raises(ValueError):
        WidthBand(min_price=D(0), max_price=D(50), widths=(D("2.5"),), strike_step=D(1))


def test_band_lookup() -> None:
    from remy_finance.movegen.config import MOVEGEN_1

    assert isinstance(MOVEGEN_1, MovegenConfig)
    assert MOVEGEN_1.band_for(D("49.99")).strike_step == D("0.5")
    assert MOVEGEN_1.band_for(D(50)).widths == (D("2.5"), D(5), D(10))
    assert MOVEGEN_1.band_for(D(740)).strike_step == D(10)
