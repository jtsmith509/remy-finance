"""Annotated field types: Decimal-only numerics and timezone-aware UTC datetimes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator


def _reject_float_and_bool(value: Any) -> Any:
    # bool is an int subclass and float is lossy; both are rejected outright.
    if isinstance(value, bool | float):
        raise ValueError(f"Decimal field rejects {type(value).__name__} input {value!r}")
    return value


def _finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"Decimal must be finite, got {value!r}")
    return value


def _non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError(f"must be >= 0, got {value}")
    return value


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError(f"must be > 0, got {value}")
    return value


def _reject_numeric_datetime(value: Any) -> Any:
    if isinstance(value, bool | int | float | Decimal):
        raise ValueError("datetime fields require an ISO string or datetime, not a number")
    return value


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"timezone-aware datetime required, got naive {value!r}")
    return value.astimezone(UTC)


Dec = Annotated[Decimal, BeforeValidator(_reject_float_and_bool), AfterValidator(_finite)]
"""Any finite Decimal (e.g. a rate, which may be negative)."""

NonNegDec = Annotated[Dec, AfterValidator(_non_negative)]
"""Finite Decimal >= 0: prices, premiums, capital at risk."""

PosDec = Annotated[Dec, AfterValidator(_positive)]
"""Finite Decimal > 0: strikes, dividend amounts."""

UtcDatetime = Annotated[
    datetime, BeforeValidator(_reject_numeric_datetime), AfterValidator(_require_aware_utc)
]
"""Timezone-aware datetime, normalized to UTC."""


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Strict conversion at public factory boundaries: Decimal, int or numeric str only.

    Floats and bools are rejected *before* conversion (a float has already lost the exact
    value); non-finite results are rejected too.
    """
    _reject_float_and_bool(value)
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int | str):
        try:
            result = Decimal(value)
        except ArithmeticError as e:
            raise ValueError(f"not a decimal number: {value!r}") from e
    else:
        raise ValueError(f"Decimal field rejects {type(value).__name__} input {value!r}")
    return _finite(result)
