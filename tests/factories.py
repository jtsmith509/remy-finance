"""Builders for core records used across tests (no network, deterministic)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from remy_finance.core import (
    FEES_1,
    DividendEvent,
    Leg,
    MarketState,
    Mode,
    OptionContract,
    OptionQuote,
    RatePoint,
    Right,
    Session,
    Side,
    Structure,
    StructureType,
    UnderlyingQuote,
)

AS_OF = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # 10:00 ET
EXP = date(2026, 11, 20)


def contract(strike: str = "100", right: Right = Right.CALL, sym: str = "XYZ") -> OptionContract:
    return OptionContract.build(sym, EXP, strike, right)


def option_quote(
    strike: str = "100",
    right: Right = Right.CALL,
    sym: str = "XYZ",
    *,
    bid: str = "2.40",
    ask: str = "2.50",
    at: datetime = datetime(2026, 11, 6, 14, 59, 58, tzinfo=UTC),
) -> OptionQuote:
    return OptionQuote(
        contract=contract(strike, right, sym),
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=10,
        ask_size=12,
        quote_time=at,
        source="fixture",
    )


def underlying_quote(sym: str = "XYZ", at: datetime = AS_OF) -> UnderlyingQuote:
    return UnderlyingQuote(
        symbol=sym,
        bid=Decimal("99.98"),
        ask=Decimal("100.02"),
        bid_size=300,
        ask_size=200,
        quote_time=at,
        source="fixture",
    )


def dividend(public: datetime = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)) -> DividendEvent:
    return DividendEvent(
        symbol="XYZ",
        ex_date=date(2026, 11, 16),
        amount=Decimal("0.25"),
        declaration_date=date(2026, 10, 20),
        event_time=public,
        public_time=public,
        source="fixture",
    )


def rate() -> RatePoint:
    t = datetime(2026, 11, 5, 14, 30, tzinfo=UTC)
    return RatePoint(
        symbol="SOFR",
        tenor="ON",
        observation_date=date(2026, 11, 4),
        rate=Decimal("0.0392"),
        event_time=t,
        public_time=t,
        source="fixture",
    )


def leg(strike: str, right: Right, side: Side, ratio: int = 1, sym: str = "XYZ") -> Leg:
    return Leg(contract=contract(strike, right, sym), side=side, ratio=ratio)


def bull_call_spread() -> Structure:
    """Handoff R0.6 fixture: +1 100C @ 2.50, -1 105C @ 1.00."""
    return Structure.price(
        [leg("100", Right.CALL, Side.LONG), leg("105", Right.CALL, Side.SHORT)],
        ["2.50", "1.00"],
        StructureType.VERTICAL_DEBIT,
        FEES_1,
    )


def market_state(**overrides: object) -> MarketState:
    data: dict[str, object] = {
        "session": Session.OPEN,
        "as_of": AS_OF,
        "mode": Mode.REPLAY,
        "universe_version": "UNIV-TEST",
        "underlyings": ("XYZ",),
        "underlying_quotes": (underlying_quote(),),
        "option_quotes": (option_quote("100"), option_quote("105"), option_quote("95", Right.PUT)),
        "dividends": (dividend(),),
        "rates": (rate(),),
    }
    data.update(overrides)
    return MarketState.model_validate(data)
