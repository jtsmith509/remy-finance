"""PointInTimeView: the only way models, movegen and baselines read market data.

Two guards, both raising ``LookaheadError``:
1. a request for a time later than ``as_of``;
2. any returned observation that became available after ``as_of``
   (``quote_time`` for quotes, ``public_time`` for everything else).
A source returning future data is a defect, so the view raises rather than filters.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import TypeVar

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    LookaheadError,
    OfficialClose,
    OptionContract,
    OptionQuote,
    OptionVolume,
    RatePoint,
    UnderlyingQuote,
)
from remy_finance.data.protocol import MarketDataSource, OfficialCloseSource
from remy_finance.data.timeutil import et_date, require_aware

T = TypeVar(
    "T",
    OptionQuote,
    UnderlyingQuote,
    DividendEvent,
    CorporateAction,
    RatePoint,
    OptionVolume,
    OfficialClose,
)


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

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        """Volume for a completed session; asking for today's or a later session raises."""
        if session_date >= et_date(self._as_of):
            raise LookaheadError(
                f"option_volume({underlying}, {session_date}) requested; "
                f"as_of session is {et_date(self._as_of)}"
            )
        v = self._source.option_volume(underlying, session_date)
        return None if v is None else self._check_items((v,), f"option_volume({underlying})")[0]


def official_close_as_of(
    source: OfficialCloseSource, symbol: str, session_date: date, as_of: datetime
) -> OfficialClose | None:
    """Read an official close only if it was public at ``as_of`` (no look-ahead).

    Unlike the view's other reads this filters rather than raises: our ``public_time`` for
    a close is the vendor's report time (17:15 ET), so a close fetched earlier is simply
    "not yet public" and is retried later, never used."""
    require_aware(as_of)
    if session_date > et_date(as_of):
        raise LookaheadError(f"official_close({symbol}, {session_date}) requested at {as_of}")
    close = source.official_close(symbol, session_date)
    if close is None or close.public_time > as_of:
        return None  # not public yet: the caller tries again later
    return close
