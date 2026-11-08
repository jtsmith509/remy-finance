"""Conservative early-assignment rule ASSIGN-1 (handoff R0.9, DR-0009, DR-0019).

At each snapshot, for every open short leg:

- ``extrinsic = max(0, bid - intrinsic)``; using the bid is the conservative choice.
- **Dividend rule:** a short call is in the money, the next trading day is the ex-date of
  dividends known as of the snapshot, and their total > extrinsic: assume assigned.
- **Threshold rule:** any in-the-money short leg with extrinsic < threshold (strict):
  assume assigned.
- Out-of-the-money (and at-the-money) short legs are never assigned.

Assignment is assumed to occur before the next snapshot. The stock position and every
remaining leg are closed at the next snapshot by the fill engine (R1.2); this module
decides *whether* assignment is assumed and computes its position and cash effects.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from remy_finance.core import DividendEvent, Leg, LookaheadError, Record, Right, Side
from remy_finance.core.exact import exact_decimal
from remy_finance.core.payoff import intrinsic
from remy_finance.core.types import NonNegDec, UtcDatetime

ZERO = Decimal(0)


class AssignmentRule(Record):
    version: str = Field(min_length=1)
    extrinsic_threshold: NonNegDec


ASSIGN_1 = AssignmentRule(version="ASSIGN-1", extrinsic_threshold=Decimal("0.05"))


class AssignmentReason(StrEnum):
    DIVIDEND = "DIVIDEND"
    EXTRINSIC_THRESHOLD = "EXTRINSIC_THRESHOLD"


class AssignmentAssessment(Record):
    """The evidence and outcome of applying ASSIGN-1 to one short leg at one snapshot."""

    rule_version: str
    osi_symbol: str
    as_of: UtcDatetime
    underlying_price: NonNegDec
    option_bid: NonNegDec | None  # None: no quote; extrinsic treated as 0 (conservative)
    intrinsic: NonNegDec
    extrinsic: NonNegDec
    in_the_money: bool
    next_session: date
    dividend_next_session: NonNegDec
    assigned: bool
    reason: AssignmentReason | None


def known_dividend_on(
    dividends: Sequence[DividendEvent], symbol: str, ex_date: date, as_of: datetime
) -> Decimal:
    """Total cash dividends for ``symbol`` going ex on ``ex_date``, known as of ``as_of``.

    Passing a dividend that was not yet public at ``as_of`` is a look-ahead defect.
    """
    total = ZERO
    for d in dividends:
        if d.public_time > as_of:
            raise LookaheadError(
                f"dividend {d.symbol} ex {d.ex_date} public at {d.public_time} > as_of {as_of}"
            )
        if d.symbol == symbol and d.ex_date == ex_date:
            with exact_decimal(total, d.amount):
                total += d.amount
    return total


def assess_short_leg(
    leg: Leg,
    *,
    option_bid: Decimal | None,
    underlying_price: Decimal,
    as_of: datetime,
    next_session: date,
    known_dividends: Sequence[DividendEvent] = (),
    rule: AssignmentRule = ASSIGN_1,
) -> AssignmentAssessment:
    """Apply ASSIGN-1 to a short leg. ``underlying_price`` is the underlying mid (DR-0019)."""
    if leg.side is not Side.SHORT:
        raise ValueError("ASSIGN-1 applies to short legs only")
    c = leg.contract
    iv = intrinsic(c.right, c.strike, underlying_price)
    with exact_decimal(iv, *([option_bid] if option_bid is not None else [])):
        extrinsic = ZERO if option_bid is None else max(ZERO, option_bid - iv)
    itm = iv > 0
    dividend = known_dividend_on(known_dividends, c.underlying, next_session, as_of)

    reason: AssignmentReason | None = None
    if itm and c.right is Right.CALL and dividend > 0 and dividend > extrinsic:
        reason = AssignmentReason.DIVIDEND
    elif itm and extrinsic < rule.extrinsic_threshold:
        reason = AssignmentReason.EXTRINSIC_THRESHOLD

    return AssignmentAssessment(
        rule_version=rule.version,
        osi_symbol=c.osi_symbol,
        as_of=as_of,
        underlying_price=underlying_price,
        option_bid=option_bid,
        intrinsic=iv,
        extrinsic=extrinsic,
        in_the_money=itm,
        next_session=next_session,
        dividend_next_session=dividend,
        assigned=reason is not None,
        reason=reason,
    )


class AssignmentConsequence(Record):
    """Position and cash effect of an assumed assignment of ``contracts`` short contracts.

    ``stock_shares`` is signed (negative = short stock). ``strike_cash`` is signed cash
    from settling at the strike: a short call delivers stock and receives K x shares;
    a short put receives stock and pays K x shares. ``dividend_owed`` (>= 0) is owed when
    the resulting position is short stock across an ex-dividend date before it is closed.
    """

    osi_symbol: str
    contracts: int = Field(ge=1)
    stock_shares: int
    strike_cash: Decimal
    assignment_fees: NonNegDec
    dividend_owed: NonNegDec


def assignment_consequence(
    leg: Leg,
    contracts: int,
    *,
    dividend_per_share_before_close: Decimal = ZERO,
    assignment_fee_per_contract: Decimal = ZERO,
) -> AssignmentConsequence:
    """Effects of assignment. ``dividend_per_share_before_close``: total dividends going
    ex between the assumed assignment and the closing trade (from known dividends)."""
    if leg.side is not Side.SHORT:
        raise ValueError("only short legs are assigned")
    if contracts < 1:
        raise ValueError("contracts must be >= 1")
    c = leg.contract
    shares = contracts * c.multiplier
    # short call assigned: deliver stock we don't own (short stock), receive the strike;
    # short put assigned: buy stock at the strike (long stock), pay the strike
    stock = -shares if c.right is Right.CALL else shares
    with exact_decimal(c.strike, dividend_per_share_before_close, assignment_fee_per_contract):
        cash = c.strike * shares * (1 if stock < 0 else -1)
        owed = dividend_per_share_before_close * shares if stock < 0 else ZERO
        fees_due = assignment_fee_per_contract * contracts
    return AssignmentConsequence(
        osi_symbol=c.osi_symbol,
        contracts=contracts,
        stock_shares=stock,
        strike_cash=cash,
        assignment_fees=fees_due,
        dividend_owed=owed,
    )
