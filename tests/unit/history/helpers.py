"""Synthetic HIST-1 inputs (offline)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from remy_finance.baselines.mkt1 import YEAR_SECONDS
from remy_finance.baselines.pricing import black76
from remy_finance.data.timeutil import NEW_YORK, et_at, session_close
from remy_finance.history.atm_iv import ChainRow
from remy_finance.history.records import HistObservation, HistSeries

RATE = 0.04


def years(session: date, expiration: date) -> float:
    a = datetime.combine(session, time(16), tzinfo=NEW_YORK)
    b = datetime.combine(expiration, time(16), tzinfo=NEW_YORK)
    return (b - a).total_seconds() / YEAR_SECONDS


def chain(
    session: date,
    spot: float,
    vols: dict[int, float],
    half_spread: float = 0.0,
    strikes: tuple[float, ...] | None = None,
) -> list[ChainRow]:
    """Black-76 quotes at a flat vol per expiration (keyed by days to expiry)."""
    ks = strikes or tuple(spot * (0.8 + 0.025 * i) for i in range(17))
    out: list[ChainRow] = []
    for dte, vol in vols.items():
        exp = session + timedelta(days=dte)
        t = years(session, exp)
        df = math.exp(-RATE * t)
        fwd = spot / df
        for k in ks:
            for call in (True, False):
                px = black76(fwd, k, vol, t, df, call)
                out.append(ChainRow(exp, k, call, max(px - half_spread, 0.0), px + half_spread))
    return out


def rates() -> dict[float, float]:
    return {1 / 12: RATE, 0.25: RATE}


def obs(series: HistSeries, symbol: str, session: date, value: str, public: datetime | None = None,
        ingest: datetime | None = None) -> HistObservation:  # fmt: skip
    return HistObservation(
        series=series,
        symbol=symbol,
        session=session,
        value=Decimal(value),
        event_time=session_close(session),
        public_time=public or et_at(session, 17, 15),
        ingest_time=ingest,
        source="test",
    )
