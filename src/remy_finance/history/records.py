"""HIST-1 records (DR-0045): one frozen observation per (series, symbol, session)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from remy_finance.core.market import _PublicObservation
from remy_finance.core.types import Dec


class HistSeries(StrEnum):
    CLOSE = "CLOSE"  # official close, raw (unadjusted); public 17:15 ET
    ATMIV30 = "ATMIV30"  # 30-day ATM implied vol, annualized; public 17:15 ET
    YIELD_1M = "YIELD_1M"  # Treasury yields as fractions; public next session 09:30 ET
    YIELD_3M = "YIELD_3M"
    YIELD_10Y = "YIELD_10Y"


YIELD_TENOR_YEARS = {
    HistSeries.YIELD_1M: 1 / 12,
    HistSeries.YIELD_3M: 0.25,
    HistSeries.YIELD_10Y: 10.0,
}
RATE_SYMBOL = "UST"  # the symbol yields are stored under


class HistObservation(_PublicObservation):
    """A daily value. ``event_time`` is when it was observed in the market (the close);
    ``public_time`` when it became available; ``ingest_time`` when we fetched it."""

    series: HistSeries
    symbol: str = Field(min_length=1)
    session: date
    value: Dec
    details: tuple[tuple[str, str], ...] = ()
