"""Versioned fee schedules (FEES-1, DR-0005)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from remy_finance.core.record import Record
from remy_finance.core.types import NonNegDec


class FeeSchedule(Record):
    """Per-contract option commissions; exercise/assignment and stock fees (all in $)."""

    version: str = Field(min_length=1)
    option_per_contract_per_side: NonNegDec
    exercise_assignment_per_contract: NonNegDec
    stock_per_share: NonNegDec

    def option_fees(self, contracts: int) -> Decimal:
        if contracts < 0:
            raise ValueError("contract count must be >= 0")
        return self.option_per_contract_per_side * contracts


FEES_1 = FeeSchedule(
    version="FEES-1",
    option_per_contract_per_side=Decimal("0.65"),
    exercise_assignment_per_contract=Decimal("0"),
    stock_per_share=Decimal("0"),
)
