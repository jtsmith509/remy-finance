"""Option contracts and legs (handoff Section 4). Structures live in ``structure``."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, field_validator, model_validator

from remy_finance.core.enums import Right, Side, Style
from remy_finance.core.record import Record
from remy_finance.core.types import PosDec, to_decimal

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
        k = to_decimal(strike)  # floats/bools rejected
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
