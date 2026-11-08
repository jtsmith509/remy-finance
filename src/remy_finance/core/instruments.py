"""Option contracts, legs, structures and candidates (handoff Section 4)."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, field_validator, model_validator

from remy_finance.core.enums import Priceability, Right, Side, StructureType, Style
from remy_finance.core.record import Record
from remy_finance.core.types import NonNegDec, PosDec

_OSI_TAIL = re.compile(r"^(\d{6})([CP])(\d{8})$")
_OSI_ROOT = re.compile(r"^[A-Z][A-Z0-9.]{0,5}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def normalize_osi(symbol: str) -> str:
    """Return the 21-character OSI form: root padded to 6, YYMMDD, C/P, strike x 1000.

    Accepts the padded form or the compact form without spaces (e.g. ``AAPL261120C00340000``).
    """
    compact = symbol.strip().replace(" ", "").upper()
    if len(compact) < 16:
        raise ValueError(f"OSI symbol too short: {symbol!r}")
    root, tail = compact[:-15], compact[-15:]
    if not _OSI_ROOT.match(root):
        raise ValueError(f"invalid OSI root {root!r} in {symbol!r}")
    if not _OSI_TAIL.match(tail):
        raise ValueError(f"invalid OSI tail {tail!r} in {symbol!r}")
    return root.ljust(6) + tail


def build_osi(root: str, expiration: date, right: Right, strike: Decimal) -> str:
    milli = strike * 1000
    if milli != milli.to_integral_value() or milli <= 0 or milli >= 10**8:
        raise ValueError(f"strike {strike} is not representable in OSI (0.001 steps, < 100000)")
    if not 2000 <= expiration.year <= 2099:
        raise ValueError(f"expiration year out of OSI range: {expiration}")
    return normalize_osi(f"{root}{expiration:%y%m%d}{right.value}{int(milli):08d}")


def osi_root(osi_symbol: str) -> str:
    return osi_symbol[:6].rstrip()


class OptionContract(Record):
    """A listed equity/ETF option. Non-standard deliverables are excluded from the universe."""

    osi_symbol: str
    underlying: str
    expiration: date
    strike: PosDec
    right: Right
    style: Style = Style.AMERICAN
    multiplier: int = Field(default=100, gt=0)
    standard_deliverable: bool

    @field_validator("osi_symbol", mode="before")
    @classmethod
    def _normalize_osi(cls, value: object) -> object:
        return normalize_osi(value) if isinstance(value, str) else value

    @field_validator("underlying")
    @classmethod
    def _check_underlying(cls, value: str) -> str:
        if not _SYMBOL.match(value):
            raise ValueError(f"invalid underlying symbol {value!r}")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        expected = build_osi(self.root, self.expiration, self.right, self.strike)
        if expected != self.osi_symbol:
            raise ValueError(
                f"OSI symbol {self.osi_symbol!r} disagrees with fields (expected {expected!r})"
            )
        if self.standard_deliverable:
            # DR-0014: a numerically suffixed root (e.g. AAPL1) marks an adjusted contract.
            if self.root[-1].isdigit():
                raise ValueError(f"root {self.root!r} is adjusted; cannot be standard")
            if self.root != self.underlying:
                raise ValueError("standard contract's OSI root must equal its underlying")
            if self.multiplier != 100:
                raise ValueError("standard contract must have multiplier 100")
        return self

    @property
    def root(self) -> str:
        return osi_root(self.osi_symbol)

    @classmethod
    def build(
        cls,
        underlying: str,
        expiration: date,
        strike: Decimal | int | str,
        right: Right,
        *,
        root: str | None = None,
        multiplier: int = 100,
        standard_deliverable: bool = True,
    ) -> OptionContract:
        k = Decimal(strike)
        return cls(
            osi_symbol=build_osi(root or underlying, expiration, right, k),
            underlying=underlying,
            expiration=expiration,
            strike=k,
            right=right,
            multiplier=multiplier,
            standard_deliverable=standard_deliverable,
        )


class Leg(Record):
    """One leg of a structure: side +1 long / -1 short, ratio in whole contracts per unit."""

    contract: OptionContract
    side: Side
    ratio: int = Field(default=1, ge=1)


class Structure(Record):
    """An ordered, defined-risk combination of legs.

    ``capital_at_risk``, ``max_loss`` and ``max_gain`` are **per unit** (one set of leg
    ratios), in dollars. They are computed by the payoff module (R0.6); this type only
    checks that they are consistent. ``max_gain`` is None when unbounded.
    """

    legs: tuple[Leg, ...] = Field(min_length=1)
    structure_type: StructureType
    capital_at_risk: NonNegDec
    max_loss: NonNegDec
    max_gain: NonNegDec | None

    @model_validator(mode="after")
    def _check_legs(self) -> Self:
        underlyings = {leg.contract.underlying for leg in self.legs}
        if len(underlyings) != 1:
            raise ValueError(f"all legs must share one underlying, got {sorted(underlyings)}")
        symbols = [leg.contract.osi_symbol for leg in self.legs]
        if len(set(symbols)) != len(symbols):
            raise ValueError("a contract may appear in only one leg")
        if not all(leg.contract.standard_deliverable for leg in self.legs):
            raise ValueError("non-standard-deliverable contracts are excluded")
        if self.capital_at_risk < self.max_loss:
            raise ValueError("capital_at_risk must be >= max_loss")
        return self

    @property
    def underlying(self) -> str:
        return self.legs[0].contract.underlying


class Candidate(Record):
    """A candidate trade, or the null move (``structure is None``)."""

    structure: Structure | None
    quantity: int = Field(ge=0)
    entry_rule: str = Field(min_length=1)
    exit_policy_id: str = Field(min_length=1)
    priceability: Priceability
    unpriceable_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.structure is None:
            if self.quantity != 0:
                raise ValueError("the null move has quantity 0")
            if self.priceability is not Priceability.PRICED or self.unpriceable_reason:
                raise ValueError("the null move is always PRICED with no reason")
        if self.priceability is Priceability.UNPRICEABLE and not self.unpriceable_reason:
            raise ValueError("UNPRICEABLE candidates must state a reason")
        if self.priceability is Priceability.PRICED and self.unpriceable_reason is not None:
            raise ValueError("PRICED candidates must not carry an unpriceable_reason")
        return self

    @property
    def is_null_move(self) -> bool:
        return self.structure is None

    @classmethod
    def null_move(cls, entry_rule: str = "NONE", exit_policy_id: str = "NONE") -> Candidate:
        return cls(
            structure=None,
            quantity=0,
            entry_rule=entry_rule,
            exit_policy_id=exit_policy_id,
            priceability=Priceability.PRICED,
        )
