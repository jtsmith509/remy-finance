"""The vendor-neutral market data interface (handoff R0.3).

Models, movegen and baselines never call a source directly: they read through
``PointInTimeView`` (see ``view``), which enforces the no-look-ahead rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from remy_finance.core import (
    CorporateAction,
    DividendEvent,
    OptionContract,
    OptionQuote,
    OptionVolume,
    RatePoint,
    UnderlyingQuote,
)


@runtime_checkable
class MarketDataSource(Protocol):
    def list_contracts(self, underlying: str, as_of: datetime) -> Sequence[OptionContract]:
        """Contracts listed for ``underlying`` on the session containing ``as_of``."""
        ...

    def chain_quotes_at(self, underlying: str, ts: datetime) -> Sequence[OptionQuote]:
        """For every contract of ``underlying``, its latest NBBO at or before ``ts``
        within the same session (no prior-day quotes)."""
        ...

    def underlying_quote_at(self, symbol: str, ts: datetime) -> UnderlyingQuote | None: ...

    def dividends(self, symbol: str, known_as_of: datetime) -> Sequence[DividendEvent]:
        """Dividends whose ``public_time`` <= ``known_as_of``."""
        ...

    def corporate_actions(
        self, symbol: str, known_as_of: datetime
    ) -> Sequence[CorporateAction]: ...

    def rates(self, known_as_of: datetime) -> Sequence[RatePoint]: ...

    def option_volume(self, underlying: str, session_date: date) -> OptionVolume | None:
        """Total option volume for one completed session (UNIV-1 extension, DR-0023)."""
        ...
