"""Expiration settlement EXP-1 (handoff R0.10, DR-0010).

- OCC exercise-by-exception: an option in the money by >= $0.01 at the official close is
  exercised (long) or assigned (short); otherwise it expires worthless.
- Settle at intrinsic value using the official closing price, treating any resulting stock
  as closed at that same price (so no stock position survives settlement).
- If any short leg finishes within $0.25 of its strike, flag ``PIN_RISK_APPROXIMATE``:
  real-world exercise decisions near the strike are uncertain.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from remy_finance.core import FEES_1, FeeSchedule, Leg, Record, Side
from remy_finance.core.exact import exact_decimal
from remy_finance.core.payoff import intrinsic, strikes_of
from remy_finance.core.types import NonNegDec, to_decimal

ZERO = Decimal(0)


class ExpirationRule(Record):
    version: str = Field(min_length=1)
    exercise_threshold: NonNegDec  # ITM by >= this at the official close => exercised
    pin_window: NonNegDec  # short leg within this of its strike => PIN_RISK_APPROXIMATE


EXP_1 = ExpirationRule(
    version="EXP-1", exercise_threshold=Decimal("0.01"), pin_window=Decimal("0.25")
)


class SettlementFlag(StrEnum):
    PIN_RISK_APPROXIMATE = "PIN_RISK_APPROXIMATE"


class LegSettlement(Record):
    osi_symbol: str
    side: Side
    contracts: int = Field(ge=1)
    intrinsic: NonNegDec  # per share at the official close
    exercised: bool  # exercised (long) or assigned (short)
    value: Decimal  # signed cash, $: +received / -paid at settlement


class ExpirationSettlement(Record):
    rule_version: str
    fee_version: str
    expiration: date
    official_close: NonNegDec
    legs: tuple[LegSettlement, ...]
    settlement_value: Decimal  # sum of leg values, $ (before premium and entry fees)
    exercise_assignment_fees: NonNegDec
    flags: tuple[SettlementFlag, ...] = ()


def settle_at_expiration(
    legs: Sequence[Leg],
    quantity: int,
    *,
    expiration: date,
    official_close: Decimal,
    fees: FeeSchedule = FEES_1,
    rule: ExpirationRule = EXP_1,
) -> ExpirationSettlement:
    """Settle the still-open ``legs`` of ``quantity`` units of a structure at expiration."""
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    if not legs:
        raise ValueError("nothing to settle")
    official_close = to_decimal(official_close)
    if official_close < 0:
        raise ValueError("official close must be >= 0")
    for leg in legs:
        if leg.contract.expiration != expiration:
            raise ValueError(
                f"{leg.contract.osi_symbol} expires {leg.contract.expiration}, not {expiration}"
            )

    with exact_decimal(official_close, *strikes_of(legs), fees.exercise_assignment_per_contract):
        return _settle(legs, quantity, expiration, official_close, fees, rule)


def _settle(
    legs: Sequence[Leg],
    quantity: int,
    expiration: date,
    official_close: Decimal,
    fees: FeeSchedule,
    rule: ExpirationRule,
) -> ExpirationSettlement:
    results: list[LegSettlement] = []
    exercised_contracts = 0
    pin = False
    for leg in legs:
        c = leg.contract
        contracts = leg.ratio * quantity
        iv = intrinsic(c.right, c.strike, official_close)
        exercised = iv >= rule.exercise_threshold
        value = int(leg.side) * contracts * c.multiplier * iv if exercised else ZERO
        if exercised:
            exercised_contracts += contracts
        if leg.side is Side.SHORT and abs(official_close - c.strike) <= rule.pin_window:
            pin = True
        results.append(
            LegSettlement(
                osi_symbol=c.osi_symbol,
                side=leg.side,
                contracts=contracts,
                intrinsic=iv,
                exercised=exercised,
                value=value,
            )
        )

    return ExpirationSettlement(
        rule_version=rule.version,
        fee_version=fees.version,
        expiration=expiration,
        official_close=official_close,
        legs=tuple(results),
        settlement_value=sum((r.value for r in results), ZERO),
        exercise_assignment_fees=fees.exercise_assignment_per_contract * exercised_contracts,
        flags=(SettlementFlag.PIN_RISK_APPROXIMATE,) if pin else (),
    )
