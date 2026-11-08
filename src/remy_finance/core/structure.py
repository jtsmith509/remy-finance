"""Defined-risk structures and candidates (handoff Section 4, R0.6).

A ``Structure`` records its legs, the per-share entry price of each leg (the fill price
under FILL-1: ask for buys, bid for sells), and its fees. Its validator recomputes
max loss, max gain and capital at risk from those inputs, so a structure can only
exist if its numbers are consistent, and one with unbounded loss cannot exist at all.

Conventions (per unit = one set of leg ratios, in dollars):
- ``net_premium``: positive = debit paid, negative = credit received.
- ``max_loss`` / ``max_gain``: contractual, from premiums and terminal payoff, before fees.
- ``capital_at_risk`` = ``max_loss`` + ``entry_fees`` + ``exit_fees``.
  ``exit_fees`` is non-zero only when the exit policy requires a closing trade
  (HOLD-1 does not: expiration settles at $0 exercise/assignment fee under FEES-1).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from remy_finance.core import payoff
from remy_finance.core.enums import Priceability, StructureType
from remy_finance.core.exact import exact_decimal
from remy_finance.core.fees import FeeSchedule
from remy_finance.core.instruments import Leg
from remy_finance.core.record import Record
from remy_finance.core.types import NonNegDec, to_decimal


class Structure(Record):
    """An ordered, defined-risk combination of legs with its entry pricing."""

    legs: tuple[Leg, ...] = Field(min_length=1)
    structure_type: StructureType
    entry_prices: tuple[NonNegDec, ...]
    fee_version: str = Field(min_length=1)
    entry_fees: NonNegDec
    exit_fees: NonNegDec
    capital_at_risk: NonNegDec
    max_loss: NonNegDec
    max_gain: NonNegDec | None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(self.entry_prices) != len(self.legs):
            raise ValueError("entry_prices must have one price per leg")
        contracts = [leg.contract for leg in self.legs]
        if len({c.underlying for c in contracts}) != 1:
            raise ValueError("all legs must share one underlying")
        if len({c.expiration for c in contracts}) != 1:
            raise ValueError("all legs must share one expiration (no calendars/diagonals)")
        symbols = [c.osi_symbol for c in contracts]
        if len(set(symbols)) != len(symbols):
            raise ValueError("a contract may appear in only one leg")
        if not all(c.standard_deliverable for c in contracts):
            raise ValueError("non-standard-deliverable contracts are excluded")

        low, high = payoff.payoff_extremes(self.legs, self.net_premium)  # may raise
        with exact_decimal(
            low, self.entry_fees, self.exit_fees, *([high] if high is not None else [])
        ):
            max_loss = max(-low, payoff.ZERO)
            max_gain = None if high is None else max(high, payoff.ZERO)
            expected = {
                "max_loss": max_loss,
                "max_gain": max_gain,
                "capital_at_risk": max_loss + self.entry_fees + self.exit_fees,
            }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"{name}={getattr(self, name)} but legs and prices imply {value}")
        return self

    # ------------------------------------------------------------------ derived

    @property
    def underlying(self) -> str:
        return self.legs[0].contract.underlying

    @property
    def expiration(self) -> date:
        return self.legs[0].contract.expiration

    @property
    def net_premium(self) -> Decimal:
        return payoff.net_premium(self.legs, self.entry_prices)

    @property
    def contracts_per_unit(self) -> int:
        return payoff.contracts_per_unit(self.legs)

    def payoff_at_expiry(self, s: Decimal) -> Decimal:
        """Terminal payoff per unit at S_T = s, before premiums and fees."""
        return payoff.terminal_payoff(self.legs, s)

    def pnl_at_expiry(self, s: Decimal, quantity: int = 1) -> Decimal:
        """Net P&L held to expiration (HOLD-1/EXP-1): payoff - premium - all fees."""
        if quantity < 0:
            raise ValueError("quantity must be >= 0")
        s = to_decimal(s)
        with exact_decimal(
            s, *self.entry_prices, self.entry_fees, self.exit_fees, *payoff.strikes_of(self.legs)
        ):
            per_unit = (
                self.payoff_at_expiry(s) - self.net_premium - self.entry_fees - self.exit_fees
            )
            return per_unit * quantity

    # ------------------------------------------------------------------ builder

    @classmethod
    def price(
        cls,
        legs: Sequence[Leg],
        entry_prices: Sequence[Decimal | int | str],
        structure_type: StructureType,
        fees: FeeSchedule,
        *,
        closing_trade_required: bool = False,
    ) -> Structure:
        """Build a structure, computing fees and risk numbers. Raises UndefinedRiskError."""
        legs_t = tuple(legs)
        prices = tuple(to_decimal(p) for p in entry_prices)  # floats/bools rejected
        n_contracts = payoff.contracts_per_unit(legs_t)
        entry_fees = fees.option_fees(n_contracts)
        exit_fees = fees.option_fees(n_contracts) if closing_trade_required else Decimal(0)
        premium = payoff.net_premium(legs_t, prices)
        low, high = payoff.payoff_extremes(legs_t, premium)
        with exact_decimal(low, entry_fees, exit_fees):
            max_loss = max(-low, payoff.ZERO)
            capital_at_risk = max_loss + entry_fees + exit_fees
        return cls(
            legs=legs_t,
            structure_type=structure_type,
            entry_prices=prices,
            fee_version=fees.version,
            entry_fees=entry_fees,
            exit_fees=exit_fees,
            capital_at_risk=capital_at_risk,
            max_loss=max_loss,
            max_gain=None if high is None else max(high, payoff.ZERO),
        )


class Candidate(Record):
    """A candidate trade, or the null move.

    - Null move: no legs, no structure type, no structure, quantity 0, PRICED.
    - PRICED: ``structure`` is the priced structure and its legs equal ``legs``.
    - UNPRICEABLE: ``structure`` is None (no executable prices) and a reason is given.

    The defined-risk rule is checked from the legs alone, so even an unpriceable
    candidate cannot have unbounded loss.
    """

    legs: tuple[Leg, ...] = ()
    structure_type: StructureType | None = None
    structure: Structure | None = None
    quantity: int = Field(ge=0)
    entry_rule: str = Field(min_length=1)
    exit_policy_id: str = Field(min_length=1)
    priceability: Priceability
    unpriceable_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.legs:
            if self.structure_type is not None or self.structure is not None:
                raise ValueError("the null move has no structure")
            if self.quantity != 0:
                raise ValueError("the null move has quantity 0")
            if self.priceability is not Priceability.PRICED or self.unpriceable_reason:
                raise ValueError("the null move is always PRICED with no reason")
            return self
        if self.structure_type is None:
            raise ValueError("a non-null candidate needs a structure_type")
        payoff.payoff_extremes(self.legs, payoff.ZERO)  # defined-risk check from legs alone
        if self.priceability is Priceability.PRICED:
            if self.unpriceable_reason is not None:
                raise ValueError("PRICED candidates must not carry an unpriceable_reason")
            if self.structure is None:
                raise ValueError("PRICED candidates need a priced structure")
            if self.structure.legs != self.legs:
                raise ValueError("structure legs differ from candidate legs")
            if self.structure.structure_type is not self.structure_type:
                raise ValueError("structure type differs from candidate structure_type")
        else:
            if not self.unpriceable_reason:
                raise ValueError("UNPRICEABLE candidates must state a reason")
            if self.structure is not None:
                raise ValueError("UNPRICEABLE candidates carry no priced structure")
        return self

    @property
    def is_null_move(self) -> bool:
        return not self.legs

    @classmethod
    def null_move(cls, entry_rule: str = "NONE", exit_policy_id: str = "NONE") -> Candidate:
        return cls(
            quantity=0,
            entry_rule=entry_rule,
            exit_policy_id=exit_policy_id,
            priceability=Priceability.PRICED,
        )

    @classmethod
    def priced(
        cls, structure: Structure, quantity: int, entry_rule: str, exit_policy_id: str
    ) -> Candidate:
        return cls(
            legs=structure.legs,
            structure_type=structure.structure_type,
            structure=structure,
            quantity=quantity,
            entry_rule=entry_rule,
            exit_policy_id=exit_policy_id,
            priceability=Priceability.PRICED,
        )
