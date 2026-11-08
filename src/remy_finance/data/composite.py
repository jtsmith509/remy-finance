"""CompositeSource: quotes and rates from one source, dividends and corporate actions
from another (ThetaData + Massive, DR-0013/DR-0014)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    OptionContract,
    OptionQuote,
    OptionVolume,
    RatePoint,
    UnderlyingQuote,
)


class _QuoteSource(Protocol):
    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]: ...
    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]: ...
    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None: ...
    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]: ...
    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None: ...


class _EventSource(Protocol):
    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]: ...
    def corporate_actions(
        self, symbol: str, known_as_of: datetime
    ) -> Sequence[CorporateAction]: ...


class CompositeSource:
    def __init__(self, quotes: _QuoteSource, events: _EventSource) -> None:
        self._q = quotes
        self._e = events

    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        return self._q.list_contracts(underlying, as_of)

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        return self._q.chain_quotes_at(underlying, ts)

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None:
        return self._q.underlying_quote_at(symbol, ts)

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        return self._e.dividends(symbol, known_as_of)

    def corporate_actions(self, symbol: str, known_as_of: datetime) -> Sequence[CorporateAction]:
        return self._e.corporate_actions(symbol, known_as_of)

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]:
        return self._q.rates(known_as_of)

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        return self._q.option_volume(underlying, session_date)
