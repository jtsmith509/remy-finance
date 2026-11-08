"""Frozen paper fill rule FILL-1 and the cost model (handoff R0.8, R1.2; DR-0003, DR-0005).

- Buy legs fill at the displayed ask, sell legs at the displayed bid, and only if the
  displayed size at that price covers the full leg quantity (and the price is > 0).
- A multi-leg order fills only if every leg fills (all-or-none, single attempt).
- Opening trades follow each leg's side (long = buy); closing trades reverse it.
- Fees (FEES-1) are charged per contract per side, only on a fill.
- Stock from an assumed assignment is closed by crossing the underlying spread and is
  assumed always fillable (DR-0009).

Which quote to use is the caller's job: fills use the *execution quote* taken 5 minutes
after the decision snapshot (DR-0003), never the snapshot itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from remy_finance.core import FEES_1, FeeSchedule, Leg, OptionQuote, Record, Side, UnderlyingQuote
from remy_finance.core.exact import exact_decimal
from remy_finance.core.types import NonNegDec

ZERO = Decimal(0)


class FillRule(Record):
    version: str = Field(min_length=1)
    execution_latency_seconds: int = Field(ge=0)
    require_full_displayed_size: bool


FILL_1 = FillRule(version="FILL-1", execution_latency_seconds=300, require_full_displayed_size=True)


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class UnfilledReason(StrEnum):
    NO_QUOTE = "NO_QUOTE"
    NO_PRICE = "NO_PRICE"  # zero ask (buy) or zero bid (sell)
    INSUFFICIENT_SIZE = "INSUFFICIENT_SIZE"


def trade_side(leg: Leg, *, opening: bool) -> TradeSide:
    long_ = leg.side is Side.LONG
    return TradeSide.BUY if long_ == opening else TradeSide.SELL


def assumed_price(quote: OptionQuote, side: TradeSide) -> Decimal:
    """The FILL-1 price for a trade side: ask for buys, bid for sells."""
    return quote.ask if side is TradeSide.BUY else quote.bid


class LegFill(Record):
    osi_symbol: str
    trade_side: TradeSide
    contracts: int = Field(ge=1)
    price: NonNegDec | None  # per share; None when there was no quote
    displayed_size: int | None
    filled: bool
    reason: UnfilledReason | None = None


class OrderFill(Record):
    """Outcome of one all-or-none paper order under FILL-1."""

    rule_version: str
    fee_version: str
    opening: bool
    quantity: int = Field(ge=1)
    legs: tuple[LegFill, ...]
    filled: bool
    premium_cash: Decimal  # signed $: + received, - paid (0 if unfilled)
    fees: NonNegDec  # 0 if unfilled
    net_cash: Decimal  # premium_cash - fees


def fill_order(
    legs: Sequence[Leg],
    quantity: int,
    quotes: Mapping[str, OptionQuote],
    *,
    opening: bool,
    fees: FeeSchedule = FEES_1,
    rule: FillRule = FILL_1,
) -> OrderFill:
    """Apply FILL-1 to ``quantity`` units of ``legs`` against ``quotes`` (keyed by OSI)."""
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    if not legs:
        raise ValueError("an order needs at least one leg")
    results: list[LegFill] = []
    cash = ZERO
    for leg in legs:
        c = leg.contract
        side = trade_side(leg, opening=opening)
        contracts = leg.ratio * quantity
        quote = quotes.get(c.osi_symbol)
        if quote is None:
            results.append(
                LegFill(
                    osi_symbol=c.osi_symbol,
                    trade_side=side,
                    contracts=contracts,
                    price=None,
                    displayed_size=None,
                    filled=False,
                    reason=UnfilledReason.NO_QUOTE,
                )
            )
            continue
        if quote.contract.osi_symbol != c.osi_symbol:
            raise ValueError(f"quote for {quote.contract.osi_symbol} keyed as {c.osi_symbol}")
        price = assumed_price(quote, side)
        size = quote.ask_size if side is TradeSide.BUY else quote.bid_size
        reason: UnfilledReason | None = None
        if price <= 0:
            reason = UnfilledReason.NO_PRICE
        elif rule.require_full_displayed_size and size < contracts:
            reason = UnfilledReason.INSUFFICIENT_SIZE
        results.append(
            LegFill(
                osi_symbol=c.osi_symbol,
                trade_side=side,
                contracts=contracts,
                price=price,
                displayed_size=size,
                filled=reason is None,
                reason=reason,
            )
        )
        sign = -1 if side is TradeSide.BUY else 1
        with exact_decimal(cash, price):
            cash += sign * price * contracts * c.multiplier

    filled = all(r.filled for r in results)
    total_contracts = sum(r.contracts for r in results)
    fee = fees.option_fees(total_contracts) if filled else ZERO
    premium = cash if filled else ZERO
    with exact_decimal(premium, fee):
        net = premium - fee
    return OrderFill(
        rule_version=rule.version,
        fee_version=fees.version,
        opening=opening,
        quantity=quantity,
        legs=tuple(results),
        filled=filled,
        premium_cash=premium,
        fees=fee,
        net_cash=net,
    )


class StockFill(Record):
    symbol: str
    shares: int  # signed: + bought, - sold
    price: NonNegDec
    cash: Decimal  # signed $
    fees: NonNegDec


def close_stock(
    position_shares: int, quote: UnderlyingQuote, *, fees: FeeSchedule = FEES_1
) -> StockFill:
    """Close a stock position by crossing the spread: sell long at bid, buy back short at ask."""
    if position_shares == 0:
        raise ValueError("no stock position to close")
    if position_shares > 0:
        traded, price = -position_shares, quote.bid
    else:
        traded, price = -position_shares, quote.ask
    if price <= 0:
        raise ValueError(f"no usable underlying price to close {quote.symbol}")
    with exact_decimal(price, fees.stock_per_share):
        cash, fee = -traded * price, fees.stock_per_share * abs(traded)
    return StockFill(symbol=quote.symbol, shares=traded, price=price, cash=cash, fees=fee)
