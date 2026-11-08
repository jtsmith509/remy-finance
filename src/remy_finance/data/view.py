"""PointInTimeView: the only way models, movegen and baselines read market data.

Two guards, both raising ``LookaheadError``:
1. a request for a time later than ``as_of``;
2. any returned observation that became available after ``as_of``
   (``quote_time`` for quotes, ``public_time`` for everything else).
A source returning future data is a defect, so the view raises rather than filters.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TypeVar

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    LookaheadError,
    OptionContract,
    OptionQuote,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.data.protocol import MarketDataSource
from remy_finance.data.timeutil import require_aware

T = TypeVar("T", OptionQuote, UnderlyingQuote, DividendEvent, CorporateAction, RatePoint)


class PointInTimeView:
    def __init__(self, source: MarketDataSource, as_of: datetime) -> None:
        self._source = source
        self._as_of = require_aware(as_of)

    @property
    def as_of(self) -> datetime:
        return self._as_of

    # ---------------------------------------------------------------- guards

    def _check_request(self, ts: datetime, what: str) -> datetime:
        require_aware(ts)
        if ts > self._as_of:
            raise LookaheadError(
                f"{what} requested at {ts.isoformat()} > as_of {self._as_of.isoformat()}"
            )
        return ts

    def _check_items(self, items: Iterable[T], what: str) -> tuple[T, ...]:
        out = tuple(items)
        for item in out:
            if item.available_time > self._as_of:
                raise LookaheadError(
                    f"{what}: source returned data available at {item.available_time.isoformat()} "
                    f"> as_of {self._as_of.isoformat()}"
                )
        return out

    # ----------------------------------------------------------------- reads

    def list_contracts(self, underlying: str) -> tuple[OptionContract, ...]:
        """Contracts listed on the session of ``as_of``; callers cannot ask for a later one."""
        return tuple(self._source.list_contracts(underlying, self._as_of))

    def chain_quotes(self, underlying: str, ts: datetime | None = None) -> tuple[OptionQuote, ...]:
        at = self._check_request(ts or self._as_of, f"chain_quotes({underlying})")
        return self._check_items(
            self._source.chain_quotes_at(underlying, at), f"chain_quotes({underlying})"
        )

    def underlying_quote(self, symbol: str, ts: datetime | None = None) -> UnderlyingQuote | None:
        at = self._check_request(ts or self._as_of, f"underlying_quote({symbol})")
        q = self._source.underlying_quote_at(symbol, at)
        return None if q is None else self._check_items((q,), f"underlying_quote({symbol})")[0]

    def dividends(self, symbol: str) -> tuple[DividendEvent, ...]:
        return self._check_items(
            self._source.dividends(symbol, self._as_of), f"dividends({symbol})"
        )

    def corporate_actions(self, symbol: str) -> tuple[CorporateAction, ...]:
        return self._check_items(
            self._source.corporate_actions(symbol, self._as_of), f"corporate_actions({symbol})"
        )

    def rates(self) -> tuple[RatePoint, ...]:
        return self._check_items(self._source.rates(self._as_of), "rates")
