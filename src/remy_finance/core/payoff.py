"""Terminal payoff, max loss / max gain, and the defined-risk test (R0.6).

All amounts are dollars **per unit** of a structure (one set of leg ratios), using each
contract's multiplier. A structure's terminal payoff is piecewise linear in the
underlying price S_T with kinks only at strikes, so its extremes on [0, inf) are found
by evaluating at S_T = 0 and at every strike, plus the slope beyond the highest strike.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from remy_finance.core.enums import Right
from remy_finance.core.exact import exact_decimal
from remy_finance.core.instruments import Leg

ZERO = Decimal(0)


class UndefinedRiskError(ValueError):
    """The structure's loss is unbounded (or not computable at entry): rejected."""


# The _raw helpers do no context handling: callers run them inside one exact_decimal
# context (DR-0025). Opening a context per elementary operation cost ~4x in movegen.


def _intrinsic_raw(right: Right, strike: Decimal, s: Decimal) -> Decimal:
    return max(s - strike, ZERO) if right is Right.CALL else max(strike - s, ZERO)


def _leg_payoff_raw(leg: Leg, s: Decimal) -> Decimal:
    c = leg.contract
    return int(leg.side) * leg.ratio * c.multiplier * _intrinsic_raw(c.right, c.strike, s)


def _terminal_payoff_raw(legs: Sequence[Leg], s: Decimal) -> Decimal:
    return sum((_leg_payoff_raw(leg, s) for leg in legs), ZERO)


def intrinsic(right: Right, strike: Decimal, s: Decimal) -> Decimal:
    """Per-share intrinsic value at expiry: max(S-K, 0) for calls, max(K-S, 0) for puts."""
    if s < 0:
        raise ValueError("underlying price must be >= 0")
    with exact_decimal(s, strike):
        return _intrinsic_raw(right, strike, s)


def strikes_of(legs: Sequence[Leg]) -> list[Decimal]:
    return [leg.contract.strike for leg in legs]


def leg_payoff(leg: Leg, s: Decimal) -> Decimal:
    if s < 0:
        raise ValueError("underlying price must be >= 0")
    with exact_decimal(s, leg.contract.strike):
        return _leg_payoff_raw(leg, s)


def terminal_payoff(legs: Sequence[Leg], s: Decimal) -> Decimal:
    """Sum of leg payoffs at S_T = s (before premiums and fees), computed exactly."""
    if s < 0:
        raise ValueError("underlying price must be >= 0")
    with exact_decimal(s, *strikes_of(legs)):
        return _terminal_payoff_raw(legs, s)


def net_premium(legs: Sequence[Leg], prices: Sequence[Decimal]) -> Decimal:
    """Net premium paid per unit: positive = debit, negative = credit."""
    if len(legs) != len(prices):
        raise ValueError("one price per leg required")
    with exact_decimal(*prices):
        return sum(
            (
                int(leg.side) * leg.ratio * leg.contract.multiplier * p
                for leg, p in zip(legs, prices, strict=True)
            ),
            ZERO,
        )


def contracts_per_unit(legs: Sequence[Leg]) -> int:
    return sum(leg.ratio for leg in legs)


def slope_above_highest_strike(legs: Sequence[Leg]) -> Decimal:
    """dPayoff/dS for S beyond every strike: only calls contribute."""
    return Decimal(
        sum(
            int(leg.side) * leg.ratio * leg.contract.multiplier
            for leg in legs
            if leg.contract.right is Right.CALL
        )
    )


def evaluation_points(legs: Sequence[Leg]) -> list[Decimal]:
    return sorted({ZERO, *(leg.contract.strike for leg in legs)})


def payoff_extremes(legs: Sequence[Leg], premium: Decimal) -> tuple[Decimal, Decimal | None]:
    """Return (min, max) of ``terminal_payoff - premium`` over S_T in [0, inf).

    Raises UndefinedRiskError if the minimum is unbounded (negative slope as S -> inf).
    The maximum is None when unbounded (positive slope as S -> inf).
    """
    slope = slope_above_highest_strike(legs)
    if slope < 0:
        raise UndefinedRiskError(f"loss is unbounded as S_T -> inf (slope {slope} per $1)")
    with exact_decimal(premium, *strikes_of(legs)):  # one context for the whole evaluation
        values = [_terminal_payoff_raw(legs, s) - premium for s in evaluation_points(legs)]
    low = min(values)
    high: Decimal | None = None if slope > 0 else max(values)
    return low, high
