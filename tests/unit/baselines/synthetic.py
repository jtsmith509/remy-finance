"""A synthetic option chain from a known lognormal (Black-76) model, for MKT-1 tests."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from remy_finance.baselines.pricing import black76
from remy_finance.core import (
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    Right,
    Session,
    UnderlyingQuote,
)

AS_OF = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # Fri 10:00 ET
EXPIRY = date(2026, 11, 27)  # 21 days
SPOT = 100.0
RATE = 0.04
VOL = 0.25
CLOSE = datetime(2026, 11, 27, 21, 0, tzinfo=UTC)  # 16:00 ET
YEARS = (CLOSE - AS_OF).total_seconds() / (365 * 86400)
DF = math.exp(-RATE * YEARS)
FORWARD = SPOT / DF


def model_price(strike: float, call: bool, vol: float = VOL) -> float:
    return black76(FORWARD, strike, vol, YEARS, DF, call)


def chain(
    *,
    half_spread: str = "0",
    tick: str = "0.000001",
    vol_of: object = None,
    strikes: tuple[float, ...] = tuple(75 + 2.5 * i for i in range(21)),
    sym: str = "XYZ",
    size: int = 100,
) -> MarketState:
    """Bid/ask = model price -/+ half_spread, rounded outward to ``tick``: bid floors at 0
    (no bid, as in real chains far out of the money), ask at one tick."""
    hs, tk = Decimal(half_spread), Decimal(tick)
    qt = AS_OF - timedelta(seconds=1)
    quotes = []
    for k in strikes:
        for right in (Right.CALL, Right.PUT):
            vol = vol_of(k) if callable(vol_of) else VOL
            px = Decimal(repr(model_price(k, right is Right.CALL, vol)))
            bid = max(((px - hs) / tk).to_integral_value(ROUND_FLOOR) * tk, Decimal(0))
            ask = max(((px + hs) / tk).to_integral_value(ROUND_CEILING) * tk, tk)
            quotes.append(
                OptionQuote(
                    contract=OptionContract.build(sym, EXPIRY, Decimal(repr(k)), right),
                    bid=bid,
                    ask=ask,
                    bid_size=size,
                    ask_size=size,
                    quote_time=qt,
                    source="synthetic",
                )
            )
    spot = Decimal(repr(SPOT))
    return MarketState(
        session=Session.OPEN,
        as_of=AS_OF,
        mode=Mode.REPLAY,
        universe_version="SYN",
        underlyings=(sym,),
        underlying_quotes=(
            UnderlyingQuote(
                symbol=sym,
                bid=spot - Decimal("0.01"),
                ask=spot + Decimal("0.01"),
                bid_size=500,
                ask_size=500,
                quote_time=qt,
                source="synthetic",
            ),
        ),
        option_quotes=tuple(quotes),
    )
