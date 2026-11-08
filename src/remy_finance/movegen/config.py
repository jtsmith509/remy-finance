"""MOVEGEN-1 candidate bounds (DR-0008, accepted; strike lattice DR-0020, proposed)
and SIZE-1 paper sizing (DR-0006)."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from remy_finance.core import Record, Structure, StructureType
from remy_finance.core.types import NonNegDec, PosDec


class WidthBand(Record):
    """Allowed spread widths and the strike lattice step for an underlying price band."""

    min_price: NonNegDec  # inclusive
    max_price: PosDec | None  # exclusive; None = no upper bound
    widths: tuple[PosDec, ...] = Field(min_length=1)
    strike_step: PosDec

    @model_validator(mode="after")
    def _check(self) -> Self:
        for w in self.widths:
            if (w / self.strike_step) != (w / self.strike_step).to_integral_value():
                raise ValueError(f"width {w} is not a multiple of strike_step {self.strike_step}")
        return self

    def contains(self, price: Decimal) -> bool:
        return price >= self.min_price and (self.max_price is None or price < self.max_price)


class MovegenConfig(Record):
    version: str = Field(min_length=1)
    min_dte: int = Field(ge=0)
    max_dte: int = Field(ge=0)
    strike_band: PosDec  # |K - S| <= strike_band x S for every leg
    width_bands: tuple[WidthBand, ...] = Field(min_length=1)
    structure_types: tuple[StructureType, ...]
    condor_shorts_out_of_the_money: bool
    exit_policy_id: str = Field(min_length=1)

    def band_for(self, price: Decimal) -> WidthBand:
        for band in self.width_bands:
            if band.contains(price):
                return band
        raise ValueError(f"no width band covers price {price}")


D = Decimal
MOVEGEN_1 = MovegenConfig(
    version="MOVEGEN-1",
    min_dte=7,
    max_dte=60,
    strike_band=D("0.25"),
    width_bands=(
        WidthBand(
            min_price=D(0), max_price=D(50), widths=(D(1), D("2.5"), D(5)), strike_step=D("0.5")
        ),
        WidthBand(
            min_price=D(50), max_price=D(200), widths=(D("2.5"), D(5), D(10)), strike_step=D("2.5")
        ),
        WidthBand(
            min_price=D(200), max_price=D(500), widths=(D(5), D(10), D(20)), strike_step=D(5)
        ),
        WidthBand(
            min_price=D(500), max_price=None, widths=(D(10), D(20), D(50)), strike_step=D(10)
        ),
    ),
    structure_types=(
        StructureType.LONG_CALL,
        StructureType.LONG_PUT,
        StructureType.VERTICAL_DEBIT,
        StructureType.VERTICAL_CREDIT,
        StructureType.IRON_CONDOR,
        StructureType.LONG_CALL_BUTTERFLY,
        StructureType.LONG_PUT_BUTTERFLY,
    ),
    condor_shorts_out_of_the_money=True,
    exit_policy_id="HOLD-1",
)


class SizingRule(Record):
    version: str = Field(min_length=1)
    budget: PosDec  # max capital at risk per recommendation, $
    max_contracts_per_leg: int = Field(ge=1)

    def quantity(self, structure: Structure) -> int:
        """min(floor(budget / CaR), floor(cap / largest leg ratio)); 0 = not recommendable."""
        if structure.capital_at_risk <= 0:
            raise ValueError("capital at risk must be > 0 to size a position")
        by_budget = int(self.budget // structure.capital_at_risk)
        by_cap = self.max_contracts_per_leg // max(leg.ratio for leg in structure.legs)
        return max(0, min(by_budget, by_cap))


SIZE_1 = SizingRule(version="SIZE-1", budget=D(2000), max_contracts_per_leg=10)
